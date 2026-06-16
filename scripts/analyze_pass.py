"""AIR #1 Steps A, C, D — ANALYZE pass for the 13 stale-stats tables.

Per AIR #1 approved scope:
  - ANALYZE only (no VACUUM)
  - Targets the 13 verification-identified tables
  - For partition parents (raw_segments, parse_events), names parent only so
    children are covered automatically
  - Captures before/after pg_stat_user_tables snapshots and prints a diff
  - Non-destructive; rollback is "do nothing"

Locking: ShareUpdateExclusive on each table (compatible with normal DML).
Run sequentially (one ANALYZE at a time) so we never block autovacuum on
multiple targets.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import time
from pathlib import Path

import asyncpg

DSN = "postgresql://postgres:P0stgreSQL%21Dev%23847@104.130.220.20:30432/rcm_denials"

# 13 targets; partition parents listed (children covered automatically)
TARGETS = [
    "raw_segments",      # partitioned parent; covers raw_segments_p2026_06 etc.
    "parse_events",      # partitioned parent; covers parse_events_p2026_06 etc.
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

# Tables we want to see in the snapshot (parents + their hot partition children)
SNAPSHOT_TABLES = TARGETS + ["raw_segments_p2026_06", "parse_events_p2026_06"]


async def snapshot(conn: asyncpg.Connection) -> dict:
    rows = await conn.fetch(
        """
        SELECT s.relname, s.n_live_tup, s.n_dead_tup, s.n_mod_since_analyze,
               s.last_vacuum, s.last_autovacuum, s.last_analyze, s.last_autoanalyze,
               c.reltuples::bigint AS reltuples
        FROM pg_stat_user_tables s
        JOIN pg_class c ON c.oid=s.relid
        WHERE s.relname = ANY($1::text[])
        ORDER BY s.relname
        """,
        SNAPSHOT_TABLES,
    )
    out = {}
    for r in rows:
        d = dict(r)
        for k, v in list(d.items()):
            if isinstance(v, dt.datetime):
                d[k] = v.isoformat()
        out[d["relname"]] = d
    return out


async def real_count(conn: asyncpg.Connection, tbl: str) -> int:
    return await conn.fetchval(f"SELECT count(*) FROM {tbl}")


async def main() -> None:
    c = await asyncpg.connect(dsn=DSN, timeout=30)
    started = time.monotonic()

    print(f"=== AIR #1 ANALYZE pass — started {dt.datetime.now(dt.timezone.utc).isoformat()} ===\n")

    print("[A] pre-snapshot…")
    pre = await snapshot(c)
    pre_real = {t: await real_count(c, t) for t in SNAPSHOT_TABLES}
    Path("scripts/analyze_pass_pre.json").write_text(
        json.dumps({"stats": pre, "real_count": pre_real}, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"    wrote scripts/analyze_pass_pre.json ({len(pre)} tables)")

    print("\n[C] ANALYZE pass (sequential, ShareUpdateExclusive lock per table)…")
    per_table_timing: dict[str, float] = {}
    for t in TARGETS:
        t0 = time.monotonic()
        try:
            await c.execute(f"ANALYZE {t}")
            elapsed = time.monotonic() - t0
            per_table_timing[t] = elapsed
            print(f"    ANALYZE {t:32s}  {elapsed*1000:7.1f} ms")
        except Exception as e:
            per_table_timing[t] = -1
            print(f"    ANALYZE {t:32s}  ERR: {e}")

    total = sum(t for t in per_table_timing.values() if t > 0)
    print(f"\n    total wall-clock: {total:.2f} s")

    print("\n[D] post-snapshot + diff…")
    post = await snapshot(c)
    post_real = {t: await real_count(c, t) for t in SNAPSHOT_TABLES}
    Path("scripts/analyze_pass_post.json").write_text(
        json.dumps({"stats": post, "real_count": post_real}, indent=2, default=str),
        encoding="utf-8",
    )
    print("    wrote scripts/analyze_pass_post.json")

    print("\n=== DIFF REPORT ===")
    print(f"  {'table':32s} {'pre_live':>10} {'post_live':>10} {'real':>10} "
          f"{'drift_before':>12} {'drift_after':>11}  last_autoana")
    drifted = 0
    fixed = 0
    for t in SNAPSHOT_TABLES:
        pre_live = pre.get(t, {}).get("n_live_tup", 0)
        post_live = post.get(t, {}).get("n_live_tup", 0)
        real = post_real[t]
        drift_pre = "n/a" if real == 0 else (
            f"{abs(pre_live - real) / real * 100:.1f}%"
        )
        drift_post = "n/a" if real == 0 else (
            f"{abs(post_live - real) / real * 100:.1f}%"
        )
        was_drift = real > 0 and abs(pre_live - real) / real > 0.5
        now_ok = real > 0 and abs(post_live - real) / real <= 0.05
        if was_drift:
            drifted += 1
            if now_ok:
                fixed += 1
        last_ana = post.get(t, {}).get("last_autoanalyze") or post.get(t, {}).get("last_analyze")
        flag = "[FIXED]" if was_drift and now_ok else ("[STILL]" if was_drift else "")
        print(f"  {t:32s} {pre_live:>10} {post_live:>10} {real:>10} "
              f"{drift_pre:>12} {drift_post:>11}  {last_ana}  {flag}")

    print(f"\n  summary: {fixed}/{drifted} tables with >50% drift now within 5%.")
    print(f"  total runtime: {time.monotonic() - started:.1f} s\n")

    await c.close()


if __name__ == "__main__":
    asyncio.run(main())
