"""One-off verification probe for AIR drafting.

Reports live state of:
  - procedure_codes / diagnosis_codes (rows, size, sample, indexes, columns)
  - claims / remittance_claims (live/dead tuples, sizes, autovacuum timestamps)
  - cluster-level autovacuum settings + per-table reloptions overrides
  - hot-path tables (edi_files, raw_segments, parse_events, prediction_log, code_masters)

Read-only. No writes. Connects to the remote dev cluster.
"""
from __future__ import annotations

import asyncio
import asyncpg

DSN = "postgresql://postgres:P0stgreSQL%21Dev%23847@104.130.220.20:30432/rcm_denials"


async def main() -> None:
    c = await asyncpg.connect(dsn=DSN, timeout=20)

    for tbl in ("procedure_codes", "diagnosis_codes"):
        print(f"=== {tbl.upper()} ===")
        meta = await c.fetchrow(
            f"""
            SELECT
              (SELECT count(*) FROM {tbl}) AS rows,
              pg_size_pretty(pg_total_relation_size('public.{tbl}'::regclass)) AS total_size,
              pg_size_pretty(pg_relation_size('public.{tbl}'::regclass))       AS heap_size,
              pg_size_pretty(pg_indexes_size('public.{tbl}'::regclass))        AS idx_size
            """
        )
        print(
            f"  rows={meta['rows']:>6}  "
            f"total={meta['total_size']:>10}  "
            f"heap={meta['heap_size']:>10}  "
            f"idx={meta['idx_size']:>10}"
        )

        idx = await c.fetch(
            """
            SELECT i.indexname, idx.indisunique, idx.indisprimary,
                   pg_get_indexdef(c2.oid) AS def
            FROM pg_indexes i
            JOIN pg_class c2 ON c2.relname = i.indexname
            JOIN pg_index idx ON idx.indexrelid = c2.oid
            WHERE i.tablename = $1
            ORDER BY i.indexname
            """,
            tbl,
        )
        print("  indexes:")
        for r in idx:
            tag = "PK" if r["indisprimary"] else ("UQ" if r["indisunique"] else "  ")
            print(f"    [{tag}] {r['indexname']}")
            print(f"         {r['def']}")

        try:
            sample = await c.fetch(f"SELECT * FROM {tbl} ORDER BY 1 LIMIT 3")
            if sample:
                print("  sample (first 3):")
                for r in sample:
                    print("   ", dict(r))
            else:
                print("  sample: (empty)")
        except Exception as e:
            print(f"  sample ERR: {e}")

        cols = await c.fetch(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = $1
            ORDER BY ordinal_position
            """,
            tbl,
        )
        print("  columns:")
        for col in cols:
            print(
                f"    {col['column_name']:32s} "
                f"{col['data_type']:20s} null={col['is_nullable']}"
            )
        print()

    print("=== TUPLE STATS (claims, remittance_claims) ===")
    rows = await c.fetch(
        """
        SELECT
          s.relname,
          s.n_live_tup, s.n_dead_tup, s.n_mod_since_analyze,
          s.last_vacuum, s.last_autovacuum,
          s.last_analyze, s.last_autoanalyze,
          s.vacuum_count, s.autovacuum_count, s.analyze_count, s.autoanalyze_count,
          pg_size_pretty(pg_total_relation_size(s.relid)) AS total_size,
          pg_size_pretty(pg_relation_size(s.relid))       AS heap_size,
          pg_size_pretty(pg_indexes_size(s.relid))        AS idx_size
        FROM pg_stat_user_tables s
        WHERE s.relname IN ('claims','remittance_claims')
        ORDER BY s.relname
        """
    )
    for r in rows:
        print(f"  {r['relname']}:")
        live = r["n_live_tup"]
        dead = r["n_dead_tup"]
        pct = f"{100.0 * dead / max(live, 1):.1f}%"
        print(f"    live={live:>10}  dead={dead:>10}  dead%={pct:>6}  mod_since_analyze={r['n_mod_since_analyze']:>10}")
        print(f"    total={r['total_size']:>10}  heap={r['heap_size']:>10}  idx={r['idx_size']:>10}")
        print(f"    last_vacuum     = {r['last_vacuum']}")
        print(f"    last_autovacuum = {r['last_autovacuum']}")
        print(f"    last_analyze    = {r['last_analyze']}")
        print(f"    last_autoanalyze= {r['last_autoanalyze']}")
        print(
            f"    counts: vac={r['vacuum_count']} auto_vac={r['autovacuum_count']} "
            f"ana={r['analyze_count']} auto_ana={r['autoanalyze_count']}"
        )
        print()

    print("=== PARTITION STATS (if any partitions of claims/remittance_claims) ===")
    parts = await c.fetch(
        """
        SELECT
          c.relname AS partition,
          pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size,
          s.n_live_tup, s.n_dead_tup,
          s.last_vacuum, s.last_autovacuum,
          s.last_analyze, s.last_autoanalyze
        FROM pg_class c
        JOIN pg_inherits i ON i.inhrelid = c.oid
        JOIN pg_class p ON p.oid = i.inhparent
        LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
        WHERE p.relname IN ('claims','remittance_claims')
        ORDER BY p.relname, c.relname
        """
    )
    if parts:
        for r in parts:
            print(
                f"  {r['partition']:40s} {r['total_size']:>10} "
                f"live={r['n_live_tup']} dead={r['n_dead_tup']}"
            )
            print(
                f"    last_vacuum={r['last_vacuum']} "
                f"last_autovacuum={r['last_autovacuum']}"
            )
            print(
                f"    last_analyze={r['last_analyze']} "
                f"last_autoanalyze={r['last_autoanalyze']}"
            )
    else:
        print("  (no partitions — claims/remittance_claims are heap tables)")
    print()

    print("=== AUTOVACUUM SETTINGS (cluster-wide) ===")
    settings = await c.fetch(
        """
        SELECT name, setting, unit
        FROM pg_settings
        WHERE name IN (
          'autovacuum','autovacuum_naptime',
          'autovacuum_vacuum_threshold','autovacuum_vacuum_scale_factor',
          'autovacuum_analyze_threshold','autovacuum_analyze_scale_factor',
          'autovacuum_vacuum_insert_threshold','autovacuum_vacuum_insert_scale_factor',
          'autovacuum_max_workers','autovacuum_work_mem','maintenance_work_mem',
          'autovacuum_vacuum_cost_delay','autovacuum_vacuum_cost_limit'
        )
        ORDER BY name
        """
    )
    for r in settings:
        u = r["unit"] or ""
        print(f"  {r['name']:46s} = {r['setting']} {u}")
    print()

    print("=== PER-TABLE AUTOVACUUM OVERRIDES (storage parameters) ===")
    rows = await c.fetch(
        """
        SELECT c.relname, c.relkind, c.reloptions
        FROM pg_class c
        WHERE c.relname IN (
          'claims','remittance_claims','edi_files','prediction_log',
          'request_log','raw_segments','parse_events','code_masters',
          'procedure_codes','diagnosis_codes'
        )
        AND c.relkind IN ('r','p')
        ORDER BY c.relname
        """
    )
    for r in rows:
        kind = "PART" if r["relkind"] == "p" else "HEAP"
        print(f"  [{kind}] {r['relname']:30s} reloptions={r['reloptions']}")
    print()

    print("=== HOT-PATH TABLES (edi_files, raw_segments, parse_events, prediction_log, request_log, code_masters) ===")
    rows = await c.fetch(
        """
        SELECT s.relname, s.n_live_tup, s.n_dead_tup,
               pg_size_pretty(pg_total_relation_size(s.relid)) AS total_size,
               s.last_vacuum, s.last_autovacuum,
               s.last_analyze, s.last_autoanalyze
        FROM pg_stat_user_tables s
        WHERE s.relname IN (
          'edi_files','raw_segments','parse_events','prediction_log',
          'request_log','code_masters'
        )
        ORDER BY s.relname
        """
    )
    for r in rows:
        print(
            f"  {r['relname']:18s} live={r['n_live_tup']:>8} "
            f"dead={r['n_dead_tup']:>8} size={r['total_size']:>10}"
        )
        print(
            f"    last_vac={r['last_vacuum']}  "
            f"last_autovac={r['last_autovacuum']}"
        )
        print(
            f"    last_ana={r['last_analyze']}  "
            f"last_autoana={r['last_autoanalyze']}"
        )

    await c.close()


if __name__ == "__main__":
    asyncio.run(main())
