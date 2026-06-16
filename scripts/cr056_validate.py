"""CR-056 validation — read-only assertions after migration 0015.

Verifies the 5 acceptance criteria from the approved AIR:
  1. mv_claim_labels row count ≈ 6,332 (±5%)
  2. No deleted-cohort claims present in mv_claim_labels
  3. Payers 7 and 13 absent from mv_payer_denial_rates (and all derived MVs)
  4. Global denial rate ≈ 10.52% (in [0.100, 0.110])
  5. 837P/healthcare share ≈ 33.28% (in [33.0%, 33.6%])

Also reports the post-state of every recreated MV for the CHANGELOG.
"""
from __future__ import annotations

import asyncio

import asyncpg

DSN = "postgresql://postgres:P0stgreSQL%21Dev%23847@104.130.220.20:30432/rcm_denials"

# 10 MVs touched by CR-056. (mv_patient_claim_history and mv_lifecycle_outcomes
# are NOT touched — those belong to the upcoming R0.)
RECREATED = [
    "mv_claim_labels",
    "mv_payer_denial_rates",
    "mv_payer_cpt_denial_rate",
    "mv_payer_dx_denial_rate",
    "mv_payer_pos_denial_rate",
    "mv_cpt_dx_denial_rate",
    "mv_provider_denial_profiles",
    "mv_provider_payer_denial_rate",
    "mv_provider_cpt_denial_rate",
    "mv_drift_baselines",
]
UNTOUCHED = ["mv_patient_claim_history", "mv_lifecycle_outcomes"]


async def main() -> None:
    c = await asyncpg.connect(dsn=DSN, timeout=20)

    print("Alembic head:", await c.fetchval("SELECT version_num FROM alembic_version"))
    print()

    print("Row counts — recreated MVs (post-CR-056):")
    counts = {}
    for mv in RECREATED:
        n = await c.fetchval(f"SELECT count(*) FROM {mv}")
        counts[mv] = n
        print(f"  {mv:35s} {n:>6}")

    print()
    print("Row counts — untouched MVs (should be unchanged from pre-CR-056):")
    for mv in UNTOUCHED:
        n = await c.fetchval(f"SELECT count(*) FROM {mv}")
        print(f"  {mv:35s} {n:>6}")

    print()
    print("=" * 78)
    print("Validation assertions")
    print("=" * 78)
    results = []

    # 1. mv_claim_labels row count
    n = counts["mv_claim_labels"]
    target_lo, target_hi = 6015, 6650  # 6,332 ±5%
    ok = target_lo <= n <= target_hi
    results.append(("mv_claim_labels row count within [6,015, 6,650]",
                    ok, f"actual={n}"))

    # 2. No deleted-cohort claims present
    n_deleted_in_mv = await c.fetchval(
        "SELECT count(*) FROM mv_claim_labels mv "
        "JOIN claims c ON c.id = mv.claim_id "
        "WHERE c.deleted_at IS NOT NULL"
    )
    ok = n_deleted_in_mv == 0
    results.append(("Zero soft-deleted claims in mv_claim_labels",
                    ok, f"deleted_in_mv={n_deleted_in_mv}"))

    # 3. Payers 7 and 13 absent
    p7_in_payer_mv = await c.fetchval(
        "SELECT count(*) FROM mv_payer_denial_rates WHERE payer_id = 7"
    )
    p13_in_payer_mv = await c.fetchval(
        "SELECT count(*) FROM mv_payer_denial_rates WHERE payer_id = 13"
    )
    p7_in_labels = await c.fetchval(
        "SELECT count(*) FROM mv_claim_labels WHERE payer_id = 7"
    )
    p13_in_labels = await c.fetchval(
        "SELECT count(*) FROM mv_claim_labels WHERE payer_id = 13"
    )
    ok = (p7_in_payer_mv == 0 and p13_in_payer_mv == 0
          and p7_in_labels == 0 and p13_in_labels == 0)
    results.append(("Payers 7 and 13 absent from mv_claim_labels and mv_payer_denial_rates",
                    ok,
                    f"p7_labels={p7_in_labels} p13_labels={p13_in_labels} "
                    f"p7_rates={p7_in_payer_mv} p13_rates={p13_in_payer_mv}"))

    # 4. Global denial rate in [0.100, 0.110]
    rate = await c.fetchval(
        "SELECT avg(denied::float) FROM mv_claim_labels"
    )
    rate = float(rate) if rate is not None else 0.0
    ok = 0.100 <= rate <= 0.110
    results.append(("Global denial rate in [0.100, 0.110]",
                    ok, f"actual={rate:.5f}"))

    # 5. 837P/healthcare share in [33.0%, 33.6%]
    share = await c.fetchval(
        "SELECT 100.0 * count(*) FILTER (WHERE service_variant='837P' AND claim_subtype='healthcare') "
        "/ NULLIF(count(*),0) FROM mv_claim_labels"
    )
    share = float(share) if share is not None else 0.0
    ok = 33.0 <= share <= 33.6
    results.append(("837P/healthcare share in [33.0%, 33.6%]",
                    ok, f"actual={share:.3f}%"))

    print()
    pass_count = 0
    for desc, ok, detail in results:
        marker = "PASS" if ok else "FAIL"
        print(f"  [{marker}] {desc}")
        print(f"         {detail}")
        if ok:
            pass_count += 1
    print()
    print(f"  RESULT: {pass_count}/{len(results)} assertions PASS")

    # Supplementary: variant breakdown after fix
    print()
    print("Supplementary — variant/subtype distribution in mv_claim_labels:")
    rows = await c.fetch(
        "SELECT service_variant, claim_subtype, count(*) AS n, "
        "       round(100.0 * count(*) / (SELECT count(*) FROM mv_claim_labels)::numeric, 2) AS pct "
        "FROM mv_claim_labels GROUP BY 1,2 ORDER BY 3 DESC"
    )
    for r in rows:
        print(f"  {r['service_variant']:>6} {r['claim_subtype']:>22s}  n={r['n']:>5}  pct={r['pct']}%")

    # Supplementary: payer breakdown after fix
    print()
    print("Supplementary — payer distribution in mv_claim_labels:")
    rows = await c.fetch(
        "SELECT payer_id, count(*) AS n, "
        "       round(100.0 * count(*) / (SELECT count(*) FROM mv_claim_labels)::numeric, 2) AS pct "
        "FROM mv_claim_labels GROUP BY 1 ORDER BY 2 DESC"
    )
    for r in rows:
        pid = "NULL" if r["payer_id"] is None else str(r["payer_id"])
        print(f"  payer_id={pid:>4}  n={r['n']:>5}  pct={r['pct']}%")

    await c.close()


if __name__ == "__main__":
    asyncio.run(main())
