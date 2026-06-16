"""R0 pre-execution verification — three checks before any REFRESH.

  1. mv_claim_labels lineage reduction table
  2. Provider sparsity investigation across the funnel
  3. mv_claim_labels dry-run (exact SELECT + count + EXPLAIN ANALYZE)

Read-only. No writes. No REFRESH.
"""
from __future__ import annotations

import asyncio
import time

import asyncpg

DSN = "postgresql://postgres:P0stgreSQL%21Dev%23847@104.130.220.20:30432/rcm_denials"

# The exact body of mv_claim_labels per migration 0010, executed as a normal
# query (no CREATE / no REFRESH). NOTE: the MV definition does NOT filter on
# c.deleted_at — see check #1 lineage.
MV_CLAIM_LABELS_SQL = """
SELECT
    c.id                AS claim_id,
    c.service_variant,
    c.claim_subtype,
    c.payer_id,
    c.billing_provider_id,
    c.rendering_provider_id,
    c.patient_id,
    c.service_from_date,
    CASE
        WHEN bool_or(rc.claim_status_code = '4') THEN 1
        WHEN bool_or(rc.claim_status_code IN ('1','2','3','19','20')) THEN 0
        ELSE NULL
    END AS denied
FROM claims c
LEFT JOIN remittance_claims rc ON rc.claim_id = c.id
WHERE (c.frequency_code IS NULL OR c.frequency_code = '1')
  AND c.service_from_date IS NOT NULL
GROUP BY
    c.id, c.service_variant, c.claim_subtype,
    c.payer_id, c.billing_provider_id, c.rendering_provider_id,
    c.patient_id, c.service_from_date
HAVING (
    bool_or(rc.claim_status_code = '4') OR
    bool_or(rc.claim_status_code IN ('1','2','3','19','20'))
)
"""


async def main() -> None:
    c = await asyncpg.connect(dsn=DSN, timeout=20)

    print("=" * 78)
    print("Check 1 — mv_claim_labels lineage reduction (stepwise funnel)")
    print("=" * 78)
    # The MV does NOT filter on deleted_at — using same filters as the MV.
    steps = [
        ("Total claims (incl. soft-deleted)",
         "SELECT count(*) FROM claims"),
        ("  └ claims WHERE deleted_at IS NULL",
         "SELECT count(*) FROM claims WHERE deleted_at IS NULL"),
        ("Claims surviving service_from_date IS NOT NULL",
         "SELECT count(*) FROM claims WHERE service_from_date IS NOT NULL"),
        ("  + surviving frequency_code IN ('1', NULL)  [MV WHERE clause]",
         "SELECT count(*) FROM claims WHERE service_from_date IS NOT NULL "
         "AND (frequency_code IS NULL OR frequency_code = '1')"),
        ("Of those, claims with ANY remittance_claims row",
         "SELECT count(DISTINCT c.id) FROM claims c "
         "JOIN remittance_claims rc ON rc.claim_id = c.id "
         "WHERE c.service_from_date IS NOT NULL "
         "AND (c.frequency_code IS NULL OR c.frequency_code = '1')"),
        ("Of those, claims with at least one remit status_code='4' (denied)",
         "SELECT count(DISTINCT c.id) FROM claims c "
         "JOIN remittance_claims rc ON rc.claim_id = c.id "
         "WHERE c.service_from_date IS NOT NULL "
         "AND (c.frequency_code IS NULL OR c.frequency_code = '1') "
         "AND rc.claim_status_code = '4'"),
        ("Of those, claims with at least one remit status_code IN (1,2,3,19,20) (paid-family)",
         "SELECT count(DISTINCT c.id) FROM claims c "
         "JOIN remittance_claims rc ON rc.claim_id = c.id "
         "WHERE c.service_from_date IS NOT NULL "
         "AND (c.frequency_code IS NULL OR c.frequency_code = '1') "
         "AND rc.claim_status_code IN ('1','2','3','19','20')"),
        ("FINAL: HAVING clause satisfied — claims with EITHER denial or paid-family status code",
         "SELECT count(DISTINCT c.id) FROM claims c "
         "JOIN remittance_claims rc ON rc.claim_id = c.id "
         "WHERE c.service_from_date IS NOT NULL "
         "AND (c.frequency_code IS NULL OR c.frequency_code = '1') "
         "AND (rc.claim_status_code = '4' "
         "     OR rc.claim_status_code IN ('1','2','3','19','20'))"),
    ]
    for label, q in steps:
        n = await c.fetchval(q)
        print(f"  {label:80s} {n:>8}")

    print()
    print("Detail — remit status_code distribution across the eligible original claims")
    rows = await c.fetch(
        """
        SELECT rc.claim_status_code, count(DISTINCT c.id) AS distinct_claims, count(*) AS remit_rows
        FROM claims c
        JOIN remittance_claims rc ON rc.claim_id = c.id
        WHERE c.service_from_date IS NOT NULL
          AND (c.frequency_code IS NULL OR c.frequency_code = '1')
        GROUP BY rc.claim_status_code ORDER BY 2 DESC
        """
    )
    for r in rows:
        print(f"  status_code={r['claim_status_code']!r:6s}  distinct_claims={r['distinct_claims']:>6}  remit_rows={r['remit_rows']:>6}")

    print()
    print("=" * 78)
    print("Check 2 — Provider sparsity investigation")
    print("=" * 78)

    print("\nProvider distribution in claims (all claims, NOT yet filtered):")
    rows = await c.fetch(
        """
        SELECT billing_provider_id IS NOT NULL AS has_provider,
               count(*) AS n,
               count(DISTINCT billing_provider_id) AS distinct_providers
        FROM claims GROUP BY 1 ORDER BY 1
        """
    )
    for r in rows:
        print(f"  has_billing_provider_id={r['has_provider']}  rows={r['n']:>6}  distinct_providers={r['distinct_providers']}")

    print("\nTop providers by claim count (all claims):")
    rows = await c.fetch(
        """
        SELECT billing_provider_id, count(*) AS n
        FROM claims WHERE billing_provider_id IS NOT NULL
        GROUP BY 1 ORDER BY 2 DESC LIMIT 25
        """
    )
    for r in rows:
        print(f"  provider_id={r['billing_provider_id']:>4}  claims={r['n']:>6}")

    print("\nProvider distribution after mv_claim_labels WHERE-clause filters:")
    rows = await c.fetch(
        """
        SELECT count(DISTINCT billing_provider_id) AS distinct_providers,
               count(DISTINCT CASE WHEN billing_provider_id IS NOT NULL THEN id END) AS claims_with_provider,
               count(DISTINCT id) AS total_eligible_claims
        FROM claims
        WHERE service_from_date IS NOT NULL
          AND (frequency_code IS NULL OR frequency_code = '1')
        """
    )
    for r in rows:
        print(f"  distinct_providers_in_eligible_claims = {r['distinct_providers']}")
        print(f"  eligible_claims_with_provider          = {r['claims_with_provider']}")
        print(f"  total_eligible_claims                  = {r['total_eligible_claims']}")

    print("\nProvider distribution after MV HAVING filter applied (= mv_claim_labels final cohort):")
    rows = await c.fetch(
        """
        SELECT count(DISTINCT c.billing_provider_id) AS distinct_providers,
               count(DISTINCT CASE WHEN c.billing_provider_id IS NOT NULL THEN c.id END) AS claims_with_provider,
               count(DISTINCT c.id) AS total_final_claims
        FROM claims c
        JOIN remittance_claims rc ON rc.claim_id = c.id
        WHERE c.service_from_date IS NOT NULL
          AND (c.frequency_code IS NULL OR c.frequency_code = '1')
          AND (rc.claim_status_code = '4'
               OR rc.claim_status_code IN ('1','2','3','19','20'))
        """
    )
    for r in rows:
        print(f"  distinct_providers_in_mv_cohort  = {r['distinct_providers']}")
        print(f"  cohort_claims_with_provider      = {r['claims_with_provider']}")
        print(f"  total_cohort_claims              = {r['total_final_claims']}")

    print("\nProviders table cross-check — which provider IDs exist there vs in claims?")
    only_in_table = await c.fetchval(
        """
        SELECT count(*) FROM providers
        WHERE id NOT IN (SELECT DISTINCT billing_provider_id FROM claims WHERE billing_provider_id IS NOT NULL)
        """
    )
    in_both = await c.fetchval(
        """
        SELECT count(*) FROM providers
        WHERE id IN (SELECT DISTINCT billing_provider_id FROM claims WHERE billing_provider_id IS NOT NULL)
        """
    )
    in_claims_not_table = await c.fetchval(
        """
        SELECT count(DISTINCT billing_provider_id) FROM claims
        WHERE billing_provider_id IS NOT NULL
          AND billing_provider_id NOT IN (SELECT id FROM providers)
        """
    )
    print(f"  providers in providers table only (orphaned)             = {only_in_table}")
    print(f"  providers in BOTH providers + claims                     = {in_both}")
    print(f"  providers referenced by claims but missing from table    = {in_claims_not_table}")

    print()
    print("=" * 78)
    print("Check 3 — mv_claim_labels dry-run (execute the exact MV body as SELECT)")
    print("=" * 78)

    print("\nRunning the MV body SELECT…")
    t0 = time.monotonic()
    rows = await c.fetch(MV_CLAIM_LABELS_SQL)
    elapsed_ms = (time.monotonic() - t0) * 1000
    print(f"  rows returned: {len(rows)}")
    print(f"  wall-clock:    {elapsed_ms:.1f} ms")

    print("\nDenied distribution in the returned rows:")
    denied_counts: dict = {}
    for r in rows:
        d = r["denied"]
        denied_counts[d] = denied_counts.get(d, 0) + 1
    for k, v in sorted(denied_counts.items(), key=lambda x: (x[0] is None, x[0])):
        print(f"  denied={k!r:6s}  count={v}")

    print("\nEXPLAIN (ANALYZE, BUFFERS, SETTINGS, FORMAT TEXT) of the MV body:")
    plan = await c.fetch(
        "EXPLAIN (ANALYZE, BUFFERS, SETTINGS, FORMAT TEXT) " + MV_CLAIM_LABELS_SQL
    )
    for line in plan:
        # asyncpg returns a Record per text line — key is 'QUERY PLAN'
        print("  " + (line[0] if line[0] is not None else ""))

    await c.close()


if __name__ == "__main__":
    asyncio.run(main())
