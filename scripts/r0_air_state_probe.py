"""One-off probe to capture verified state for the R0 AIR.

Reports:
  A. MV existence + size + populated flag
  B. Live MV row counts
  C. UNIQUE indexes per MV (CONCURRENTLY support gate)
  D. Source table counts
  E. claims breakdown by frequency_code (mv_claim_labels filter)
  F. Expected adjudicated-original row count (mv_claim_labels target)
  G-H. Expected post-refresh distinct-key counts for each other MV
"""
from __future__ import annotations

import asyncio
import asyncpg

DSN = "postgresql://postgres:P0stgreSQL%21Dev%23847@104.130.220.20:30432/rcm_denials"


async def main() -> None:
    c = await asyncpg.connect(dsn=DSN, timeout=20)

    print("=== A. MV state (current) ===")
    mv_rows = await c.fetch(
        """
        SELECT mv.matviewname,
               pg_size_pretty(pg_total_relation_size(format('%I.%I', mv.schemaname, mv.matviewname)::regclass)) AS sz,
               mv.ispopulated
        FROM pg_matviews mv
        WHERE mv.schemaname = 'public'
        ORDER BY mv.matviewname
        """
    )
    for r in mv_rows:
        print(f"  {r['matviewname']:42s} sz={r['sz']:>10}  populated={r['ispopulated']}")

    print()
    print("=== B. MV row counts (live) ===")
    for r in mv_rows:
        cnt = await c.fetchval(f"SELECT count(*) FROM {r['matviewname']}")
        print(f"  {r['matviewname']:42s} rows={cnt}")

    print()
    print("=== C. UNIQUE indexes per MV (CONCURRENTLY support) ===")
    idx_rows = await c.fetch(
        """
        SELECT i.tablename, i.indexname, idx.indisunique
        FROM pg_indexes i
        JOIN pg_class cls ON cls.relname = i.indexname
        JOIN pg_index idx ON idx.indexrelid = cls.oid
        WHERE i.tablename LIKE 'mv_%'
        ORDER BY i.tablename, i.indexname
        """
    )
    cur = None
    for r in idx_rows:
        if cur != r["tablename"]:
            cur = r["tablename"]
            print(f"  {cur}:")
        u = "UNIQUE" if r["indisunique"] else "non-unique"
        print(f"      {r['indexname']:50s}  {u}")

    print()
    print("=== D. Source table counts ===")
    for t, where in [
        ("claims",            "WHERE deleted_at IS NULL"),
        ("remittance_claims", ""),
        ("claim_lines",       ""),
        ("diagnoses",         ""),
        ("patients",          ""),
        ("payers",            ""),
        ("providers",         ""),
        ("subscribers",       ""),
        ("claim_lifecycles",  ""),
    ]:
        n = await c.fetchval(f"SELECT count(*) FROM {t} {where}")
        print(f"  {t:24s} {n:>8}")

    print()
    print("=== E. claims breakdown by frequency_code (mv_claim_labels filter) ===")
    rows = await c.fetch(
        """
        SELECT COALESCE(frequency_code, '(null)') AS f, count(*)
        FROM claims WHERE deleted_at IS NULL
        GROUP BY f ORDER BY 2 DESC
        """
    )
    for r in rows:
        print(f"  freq={r['f']!r:10s}  {r['count']:>8}")

    print()
    print("=== F. Adjudicated original claims (mv_claim_labels expected size) ===")
    n = await c.fetchval(
        """
        SELECT count(DISTINCT c.id) FROM claims c
        JOIN remittance_claims rc ON rc.claim_id = c.id
        WHERE c.deleted_at IS NULL
          AND (c.frequency_code IS NULL OR c.frequency_code = '1')
          AND c.service_from_date IS NOT NULL
          AND (rc.claim_status_code = '4' OR rc.claim_status_code IN ('1','2','3','19','20'))
        """
    )
    print(f"  expected mv_claim_labels rows after refresh: {n}")

    print()
    print("=== G-H. Expected distinct-key counts per MV ===")
    queries = [
        (
            "mv_payer_denial_rates (payer, variant, subtype)",
            """
            SELECT count(*) FROM (
              SELECT DISTINCT c.payer_id, c.service_variant, c.claim_subtype
              FROM claims c JOIN remittance_claims rc ON rc.claim_id = c.id
              WHERE c.deleted_at IS NULL AND c.payer_id IS NOT NULL
                AND (c.frequency_code IS NULL OR c.frequency_code = '1')
                AND c.service_from_date IS NOT NULL
                AND (rc.claim_status_code = '4' OR rc.claim_status_code IN ('1','2','3','19','20'))
            ) s
            """,
        ),
        (
            "mv_payer_cpt_denial_rate (payer, variant, cpt)",
            """
            SELECT count(*) FROM (
              SELECT DISTINCT c.payer_id, c.service_variant, cl.procedure_code
              FROM claims c
              JOIN remittance_claims rc ON rc.claim_id = c.id
              JOIN claim_lines cl ON cl.claim_id = c.id
              WHERE c.deleted_at IS NULL AND c.payer_id IS NOT NULL
                AND (c.frequency_code IS NULL OR c.frequency_code = '1')
                AND c.service_from_date IS NOT NULL
                AND cl.procedure_code IS NOT NULL
                AND (rc.claim_status_code = '4' OR rc.claim_status_code IN ('1','2','3','19','20'))
            ) s
            """,
        ),
        (
            "mv_payer_dx_denial_rate (payer, variant, dx)",
            """
            SELECT count(*) FROM (
              SELECT DISTINCT c.payer_id, c.service_variant, d.diagnosis_code
              FROM claims c
              JOIN remittance_claims rc ON rc.claim_id = c.id
              JOIN diagnoses d ON d.claim_id = c.id
              WHERE c.deleted_at IS NULL AND c.payer_id IS NOT NULL
                AND (c.frequency_code IS NULL OR c.frequency_code = '1')
                AND c.service_from_date IS NOT NULL
                AND (rc.claim_status_code = '4' OR rc.claim_status_code IN ('1','2','3','19','20'))
            ) s
            """,
        ),
        (
            "mv_payer_pos_denial_rate (payer, variant, pos)",
            """
            SELECT count(*) FROM (
              SELECT DISTINCT c.payer_id, c.service_variant, cl.place_of_service
              FROM claims c
              JOIN remittance_claims rc ON rc.claim_id = c.id
              JOIN claim_lines cl ON cl.claim_id = c.id
              WHERE c.deleted_at IS NULL AND c.payer_id IS NOT NULL
                AND (c.frequency_code IS NULL OR c.frequency_code = '1')
                AND c.service_from_date IS NOT NULL
                AND cl.place_of_service IS NOT NULL
                AND (rc.claim_status_code = '4' OR rc.claim_status_code IN ('1','2','3','19','20'))
            ) s
            """,
        ),
        (
            "mv_cpt_dx_denial_rate (cpt, dx)",
            """
            SELECT count(*) FROM (
              SELECT DISTINCT cl.procedure_code, d.diagnosis_code
              FROM claims c
              JOIN remittance_claims rc ON rc.claim_id = c.id
              JOIN claim_lines cl ON cl.claim_id = c.id
              JOIN diagnoses d ON d.claim_id = c.id
              WHERE c.deleted_at IS NULL
                AND (c.frequency_code IS NULL OR c.frequency_code = '1')
                AND c.service_from_date IS NOT NULL
                AND cl.procedure_code IS NOT NULL
                AND (rc.claim_status_code = '4' OR rc.claim_status_code IN ('1','2','3','19','20'))
            ) s
            """,
        ),
        (
            "mv_provider_denial_profiles (billing_provider)",
            """
            SELECT count(DISTINCT c.billing_provider_id)
            FROM claims c
            JOIN remittance_claims rc ON rc.claim_id = c.id
            WHERE c.deleted_at IS NULL AND c.billing_provider_id IS NOT NULL
              AND (c.frequency_code IS NULL OR c.frequency_code = '1')
              AND c.service_from_date IS NOT NULL
              AND (rc.claim_status_code = '4' OR rc.claim_status_code IN ('1','2','3','19','20'))
            """,
        ),
        (
            "mv_provider_payer_denial_rate (billing_provider, payer)",
            """
            SELECT count(*) FROM (
              SELECT DISTINCT c.billing_provider_id, c.payer_id
              FROM claims c JOIN remittance_claims rc ON rc.claim_id = c.id
              WHERE c.deleted_at IS NULL AND c.billing_provider_id IS NOT NULL AND c.payer_id IS NOT NULL
                AND (c.frequency_code IS NULL OR c.frequency_code = '1')
                AND c.service_from_date IS NOT NULL
                AND (rc.claim_status_code = '4' OR rc.claim_status_code IN ('1','2','3','19','20'))
            ) s
            """,
        ),
        (
            "mv_provider_cpt_denial_rate (billing_provider, cpt)",
            """
            SELECT count(*) FROM (
              SELECT DISTINCT c.billing_provider_id, cl.procedure_code
              FROM claims c
              JOIN remittance_claims rc ON rc.claim_id = c.id
              JOIN claim_lines cl ON cl.claim_id = c.id
              WHERE c.deleted_at IS NULL AND c.billing_provider_id IS NOT NULL
                AND (c.frequency_code IS NULL OR c.frequency_code = '1')
                AND c.service_from_date IS NOT NULL
                AND cl.procedure_code IS NOT NULL
                AND (rc.claim_status_code = '4' OR rc.claim_status_code IN ('1','2','3','19','20'))
            ) s
            """,
        ),
        (
            "mv_drift_baselines (variant, subtype)",
            """
            SELECT count(*) FROM (
              SELECT DISTINCT c.service_variant, c.claim_subtype
              FROM claims c JOIN remittance_claims rc ON rc.claim_id = c.id
              WHERE c.deleted_at IS NULL
                AND (c.frequency_code IS NULL OR c.frequency_code = '1')
                AND c.service_from_date IS NOT NULL
                AND (rc.claim_status_code = '4' OR rc.claim_status_code IN ('1','2','3','19','20'))
            ) s
            """,
        ),
        (
            "mv_patient_claim_history (per claim, patient+date not null)",
            """
            SELECT count(*) FROM claims c
            WHERE c.deleted_at IS NULL
              AND c.patient_id IS NOT NULL
              AND c.service_from_date IS NOT NULL
            """,
        ),
        (
            "mv_lifecycle_outcomes (distinct original_claim_id)",
            "SELECT count(DISTINCT original_claim_id) FROM claim_lifecycles",
        ),
    ]
    for label, q in queries:
        n = await c.fetchval(q)
        print(f"  {label:60s} expected ~{n}")

    await c.close()


if __name__ == "__main__":
    asyncio.run(main())
