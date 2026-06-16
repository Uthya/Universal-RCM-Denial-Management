"""Read-only: verify which MVs reference mv_claim_labels (would be dropped by
CASCADE), and which other MVs read directly from `claims` without filtering
on deleted_at (so we can decide whether mv_patient_claim_history needs a
parallel fix scoped to a separate AIR).
"""
from __future__ import annotations

import asyncio

import asyncpg

DSN = "postgresql://postgres:P0stgreSQL%21Dev%23847@104.130.220.20:30432/rcm_denials"


async def main() -> None:
    c = await asyncpg.connect(dsn=DSN, timeout=20)

    print("=" * 78)
    print("Dependency tree: which MVs would be dropped by")
    print("  DROP MATERIALIZED VIEW mv_claim_labels CASCADE")
    print("=" * 78)
    rows = await c.fetch(
        """
        SELECT DISTINCT
          dep_cls.relname AS dependent_mv
        FROM pg_depend d
        JOIN pg_rewrite rw ON rw.oid = d.objid
        JOIN pg_class dep_cls ON dep_cls.oid = rw.ev_class
        WHERE d.refobjid = 'mv_claim_labels'::regclass
          AND dep_cls.relkind = 'm'
          AND dep_cls.relname <> 'mv_claim_labels'
        ORDER BY dep_cls.relname
        """
    )
    print(f"  Direct dependents of mv_claim_labels: {len(rows)}")
    for r in rows:
        print(f"    - {r['dependent_mv']}")

    print()
    print("=" * 78)
    print("Live MV definition (pg_get_viewdef) — confirm WHERE clause")
    print("=" * 78)
    for mv in ("mv_claim_labels", "mv_patient_claim_history"):
        body = await c.fetchval(
            f"SELECT pg_get_viewdef('{mv}'::regclass, true)"
        )
        print(f"\n--- {mv} ---")
        print(body)

    print()
    print("=" * 78)
    print("Side-check: mv_patient_claim_history WOULD also include deleted claims")
    print("=" * 78)
    pch_total = await c.fetchval(
        "SELECT count(*) FROM claims "
        "WHERE patient_id IS NOT NULL AND service_from_date IS NOT NULL"
    )
    pch_active = await c.fetchval(
        "SELECT count(*) FROM claims "
        "WHERE patient_id IS NOT NULL AND service_from_date IS NOT NULL "
        "AND deleted_at IS NULL"
    )
    pch_deleted_in_scope = pch_total - pch_active
    print(f"  expected mv_patient_claim_history rows (current def): {pch_total}")
    print(f"  expected mv_patient_claim_history rows if filtered: {pch_active}")
    print(f"  soft-deleted rows it would currently include: {pch_deleted_in_scope}")

    print()
    print("=" * 78)
    print("Indexes on mv_claim_labels (for recreation)")
    print("=" * 78)
    rows = await c.fetch(
        """
        SELECT indexname, indexdef
        FROM pg_indexes WHERE tablename = 'mv_claim_labels'
        ORDER BY indexname
        """
    )
    for r in rows:
        print(f"  {r['indexname']}: {r['indexdef']}")

    print()
    print("=" * 78)
    print("Indexes on every dependent MV (for recreation after CASCADE)")
    print("=" * 78)
    dependent_names = [r["dependent_mv"] for r in await c.fetch(
        """
        SELECT DISTINCT dep_cls.relname AS dependent_mv
        FROM pg_depend d
        JOIN pg_rewrite rw ON rw.oid = d.objid
        JOIN pg_class dep_cls ON dep_cls.oid = rw.ev_class
        WHERE d.refobjid = 'mv_claim_labels'::regclass
          AND dep_cls.relkind = 'm'
          AND dep_cls.relname <> 'mv_claim_labels'
        ORDER BY dep_cls.relname
        """
    )]
    for mv in dependent_names:
        rows = await c.fetch(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename=$1 ORDER BY indexname",
            mv,
        )
        print(f"\n  {mv}:")
        for r in rows:
            print(f"    {r['indexname']}: {r['indexdef']}")

    await c.close()


if __name__ == "__main__":
    asyncio.run(main())
