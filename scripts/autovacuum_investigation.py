"""AIR #1 Step B — read-only investigation: why hasn't autovacuum/autoanalyze
fired on the 13 stale-stats tables?

Outputs findings to stdout AND scripts/autovacuum_investigation.md.

Probes (all SELECT-only):
  1. pg_stat_activity            — long-running txns holding xmin?
  2. pg_prepared_xacts           — uncommitted prepared transactions?
  3. pg_replication_slots        — slots holding xmin back?
  4. pg_stat_database            — txid age, deadlocks, conflicts
  5. pg_class.relfrozenxid age   — per-target table
  6. Per-table effective thresholds (vacuum + analyze) vs n_mod_since_analyze
  7. autovacuum-related GUCs cross-check
  8. pg_stat_progress_analyze    — anything in flight right now?

Does NOT propose a fix. Just classifies.
"""
from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path

import asyncpg

DSN = "postgresql://postgres:P0stgreSQL%21Dev%23847@104.130.220.20:30432/rcm_denials"

# 13 tables identified by AIR #1 verification (parents only for partitioned)
TARGETS = [
    "raw_segments",      # parent — covers raw_segments_p2026_06 child
    "parse_events",      # parent — covers parse_events_p2026_06 child
    "claim_lines",
    "diagnoses",
    "subscribers",
    "patients",
    "claim_certifications",
    "home_care_episodes",
    "edi_files",
    "pending_pair_registry",
    "code_masters",
    "providers",
    "payers",
]


def _fmt(ts):
    if ts is None:
        return "NEVER"
    if isinstance(ts, dt.datetime):
        return ts.strftime("%Y-%m-%d %H:%M:%S%z")
    return str(ts)


async def main() -> None:
    c = await asyncpg.connect(dsn=DSN, timeout=20)
    lines: list[str] = []

    def emit(s: str = ""):
        print(s)
        lines.append(s)

    emit(f"# Autovacuum behavior investigation — {dt.datetime.now(dt.timezone.utc).isoformat()}")
    emit("")
    emit("Read-only probes against `104.130.220.20:30432/rcm_denials` to classify")
    emit("why autoanalyze/autovacuum has never fired on the 13 stale-stats tables")
    emit("identified by AIR #1 verification.")
    emit("")

    # ----- 1. pg_stat_activity: long-running txns -----
    emit("## 1. pg_stat_activity — long-running transactions")
    rows = await c.fetch(
        """
        SELECT pid, datname, usename, application_name, state,
               xact_start, query_start, backend_xmin,
               LEFT(query, 120) AS query
        FROM pg_stat_activity
        WHERE state IS NOT NULL
          AND backend_type='client backend'
          AND xact_start IS NOT NULL
        ORDER BY xact_start
        """
    )
    if not rows:
        emit("  (no active client transactions)")
    else:
        for r in rows:
            age_s = None
            if r["xact_start"]:
                age_s = (dt.datetime.now(dt.timezone.utc) - r["xact_start"]).total_seconds()
            emit(
                f"  pid={r['pid']} state={r['state']} app={r['application_name']!r} "
                f"xact_age={age_s}s xmin={r['backend_xmin']}"
            )
            emit(f"    query: {r['query']}")
    emit("")

    # ----- 2. prepared xacts -----
    emit("## 2. pg_prepared_xacts — uncommitted prepared transactions")
    rows = await c.fetch("SELECT transaction, gid, prepared, owner, database FROM pg_prepared_xacts")
    if not rows:
        emit("  (none) — no prepared transactions are holding xmin back")
    else:
        for r in rows:
            emit(f"  gid={r['gid']} prepared_at={r['prepared']} owner={r['owner']}")
    emit("")

    # ----- 3. replication slots -----
    emit("## 3. pg_replication_slots — slots holding xmin")
    rows = await c.fetch(
        """
        SELECT slot_name, plugin, slot_type, active, xmin, catalog_xmin,
               restart_lsn, confirmed_flush_lsn
        FROM pg_replication_slots
        """
    )
    if not rows:
        emit("  (none) — no replication slots; nothing holding xmin externally")
    else:
        for r in rows:
            emit(f"  slot={r['slot_name']} active={r['active']} xmin={r['xmin']} "
                 f"catalog_xmin={r['catalog_xmin']}")
    emit("")

    # ----- 4. database-level stats -----
    emit("## 4. pg_stat_database — transaction activity")
    r = await c.fetchrow(
        """
        SELECT datname, xact_commit, xact_rollback,
               deadlocks, conflicts, temp_files, temp_bytes,
               stats_reset
        FROM pg_stat_database WHERE datname=current_database()
        """
    )
    emit(f"  db={r['datname']} commits={r['xact_commit']} rollbacks={r['xact_rollback']} "
         f"deadlocks={r['deadlocks']} conflicts={r['conflicts']}")
    emit(f"  stats_reset_at={r['stats_reset']}")
    emit("")

    # ----- 5. relfrozenxid age per target -----
    emit("## 5. pg_class.relfrozenxid age per target (autovacuum_freeze_max_age=200M default)")
    rows = await c.fetch(
        """
        SELECT c.relname, c.relkind,
               c.relfrozenxid::text AS frozen_xid,
               age(c.relfrozenxid) AS xid_age,
               c.reltuples::bigint AS reltuples,
               c.relpages
        FROM pg_class c
        WHERE c.relname = ANY($1::text[]) AND c.relkind IN ('r','p')
        ORDER BY c.relname
        """,
        TARGETS,
    )
    for r in rows:
        emit(f"  {r['relname']:28s} kind={r['relkind']} "
             f"reltuples={r['reltuples']:>10} relpages={r['relpages']:>8} "
             f"frozen_xid_age={r['xid_age']}")
    emit("")
    emit("  (xid_age << autovacuum_freeze_max_age means no anti-wraparound vacuum was triggered)")
    emit("")

    # ----- 6. per-table effective thresholds vs n_mod_since_analyze -----
    emit("## 6. Effective autovacuum thresholds per target")
    settings = {
        r["name"]: r["setting"]
        for r in await c.fetch(
            """
            SELECT name, setting FROM pg_settings
            WHERE name IN (
              'autovacuum_vacuum_threshold','autovacuum_vacuum_scale_factor',
              'autovacuum_analyze_threshold','autovacuum_analyze_scale_factor',
              'autovacuum_vacuum_insert_threshold','autovacuum_vacuum_insert_scale_factor'
            )
            """
        )
    }
    vt = int(settings["autovacuum_vacuum_threshold"])
    vsf = float(settings["autovacuum_vacuum_scale_factor"])
    at = int(settings["autovacuum_analyze_threshold"])
    asf = float(settings["autovacuum_analyze_scale_factor"])
    it = int(settings["autovacuum_vacuum_insert_threshold"])
    isf = float(settings["autovacuum_vacuum_insert_scale_factor"])
    emit(f"  cluster defaults: vac_threshold={vt}+{vsf}*N  "
         f"ana_threshold={at}+{asf}*N  ins_threshold={it}+{isf}*N")
    emit("")
    rows = await c.fetch(
        """
        SELECT s.relname, s.n_live_tup, s.n_dead_tup, s.n_mod_since_analyze,
               s.n_ins_since_vacuum
        FROM pg_stat_user_tables s
        WHERE s.relname = ANY($1::text[])
        ORDER BY s.relname
        """,
        TARGETS + ["raw_segments_p2026_06", "parse_events_p2026_06"],
    )
    for r in rows:
        live = r["n_live_tup"]
        eff_vac = vt + vsf * live
        eff_ana = at + asf * live
        eff_ins = it + isf * live
        emit(f"  {r['relname']:32s} live={live:>8} dead={r['n_dead_tup']:>6} "
             f"mod_since_ana={r['n_mod_since_analyze']:>8} ins_since_vac={r['n_ins_since_vacuum']:>8}")
        emit(f"    effective_thresholds: vac>{eff_vac:.0f} dead, ana>{eff_ana:.0f} mod, "
             f"insvac>{eff_ins:.0f} ins")
    emit("")

    # ----- 7. autovacuum GUC sanity -----
    emit("## 7. Autovacuum GUCs (sanity cross-check)")
    rows = await c.fetch(
        """
        SELECT name, setting, source
        FROM pg_settings
        WHERE name IN (
          'autovacuum','track_counts',
          'autovacuum_naptime','autovacuum_max_workers',
          'autovacuum_vacuum_cost_delay','autovacuum_vacuum_cost_limit',
          'autovacuum_freeze_max_age'
        )
        ORDER BY name
        """
    )
    for r in rows:
        emit(f"  {r['name']:42s} = {r['setting']:12s} (source={r['source']})")
    emit("")

    # ----- 8. anything in flight right now? -----
    emit("## 8. pg_stat_progress_analyze / _vacuum — anything in flight?")
    rows = await c.fetch(
        "SELECT pid, relid::regclass, phase, sample_blks_total, sample_blks_scanned "
        "FROM pg_stat_progress_analyze"
    )
    if not rows:
        emit("  ANALYZE: nothing in flight")
    else:
        for r in rows:
            emit(f"  ANALYZE pid={r['pid']} table={r['relid']} phase={r['phase']}")
    rows = await c.fetch(
        "SELECT pid, relid::regclass, phase, heap_blks_total, heap_blks_scanned "
        "FROM pg_stat_progress_vacuum"
    )
    if not rows:
        emit("  VACUUM: nothing in flight")
    else:
        for r in rows:
            emit(f"  VACUUM pid={r['pid']} table={r['relid']} phase={r['phase']}")
    emit("")

    # ----- Classification -----
    emit("## Classification")
    # Heuristics: if (no prepared xacts) AND (no long txns) AND (xid_age small)
    # AND (thresholds clearly crossed) → autovacuum SHOULD have fired.
    # The data above tells us which.
    emit("(see findings written manually in AIR #1 followup based on probe output above)")

    await c.close()

    out = Path(__file__).parent / "autovacuum_investigation.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[wrote {out}]")


if __name__ == "__main__":
    asyncio.run(main())
