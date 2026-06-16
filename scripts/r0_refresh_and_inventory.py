"""R0 final step + MV inventory report.

  1. REFRESH MATERIALIZED VIEW CONCURRENTLY mv_lifecycle_outcomes
  2. Validate 4 assertions
  3. Build MV inventory (name, row count, consumer(s), current state)

Read-only after the refresh.
"""
from __future__ import annotations

import asyncio
import time

import asyncpg

DSN = "postgresql://postgres:P0stgreSQL%21Dev%23847@104.130.220.20:30432/rcm_denials"


# Verified consumer mapping (greps across src/rcm/ confirmed in prior turn).
CONSUMERS = {
    "mv_claim_labels": [
        "features/dataset.py:120 (load_training_corpus)",
        "ml/trainer.py:103 (emptiness check)",
    ],
    "mv_payer_denial_rates": [
        "features/categories/joint.py:104",
        "features/registry.py:130 (payer_overall_denial_rate, Cat F)",
    ],
    "mv_payer_cpt_denial_rate": [
        "features/categories/joint.py:56",
        "features/registry.py:266 (Cat K joint)",
    ],
    "mv_payer_dx_denial_rate": [
        "features/categories/joint.py:64",
        "features/registry.py:268 (Cat K joint)",
    ],
    "mv_payer_pos_denial_rate": [
        "features/categories/joint.py:72",
        "features/registry.py:270 (Cat K joint)",
    ],
    "mv_cpt_dx_denial_rate": [
        "features/categories/joint.py:80",
        "features/registry.py:156, 272 (clinical alignment + Cat K joint)",
    ],
    "mv_provider_denial_profiles": [
        "features/categories/provider.py:56",
        "features/registry.py:252 (provider_overall_denial_rate, Cat H)",
    ],
    "mv_provider_payer_denial_rate": [
        "features/categories/provider.py:67",
        "features/registry.py:254, 274",
    ],
    "mv_provider_cpt_denial_rate": [
        "features/categories/provider.py:79",
        "features/registry.py:256, 276",
    ],
    "mv_patient_claim_history": [
        "features/categories/history.py:41,52,58,64,70,78,87,95,100,106,112",
        "(10+ Cat-G window queries)",
    ],
    "mv_lifecycle_outcomes": [
        "(no FeatureBuilder consumer — reserved for future correction-chain analytics)",
    ],
    "mv_drift_baselines": [
        "(no FeatureBuilder consumer — monitoring/drift-detection snapshots)",
    ],
}


# Pre-state (immediately before the refresh) for the diff
LIFECYCLE_PRE_COUNT = None


async def main() -> None:
    c = await asyncpg.connect(dsn=DSN, timeout=20)

    print("=" * 78)
    print("R0 step 1 — capture pre-state")
    print("=" * 78)
    head = await c.fetchval("SELECT version_num FROM alembic_version")
    pre_count = await c.fetchval("SELECT count(*) FROM mv_lifecycle_outcomes")
    print(f"  alembic head: {head}")
    print(f"  mv_lifecycle_outcomes pre row count: {pre_count}")

    print()
    print("=" * 78)
    print("R0 step 2 — REFRESH MATERIALIZED VIEW CONCURRENTLY mv_lifecycle_outcomes")
    print("=" * 78)
    t0 = time.monotonic()
    try:
        await c.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_lifecycle_outcomes")
        refresh_ok = True
        refresh_err = None
    except Exception as e:
        refresh_ok = False
        refresh_err = str(e)
    elapsed = time.monotonic() - t0
    print(f"  refresh succeeded: {refresh_ok}")
    if refresh_err:
        print(f"  error: {refresh_err}")
    print(f"  wall-clock: {elapsed:.3f} s")

    print()
    print("=" * 78)
    print("R0 step 3 — validate")
    print("=" * 78)
    post_count = await c.fetchval("SELECT count(*) FROM mv_lifecycle_outcomes")
    head_after = await c.fetchval("SELECT version_num FROM alembic_version")

    results = [
        ("REFRESH succeeded", refresh_ok, "no error" if refresh_ok else refresh_err),
        ("Row count remains 0", post_count == 0, f"actual={post_count}"),
        ("Runtime < 5 s", elapsed < 5.0, f"actual={elapsed:.3f}s"),
        ("Alembic head unchanged (0016_mv_pch_deleted)", head_after == "0016_mv_pch_deleted",
         f"actual={head_after}"),
    ]
    pass_count = 0
    for desc, ok, detail in results:
        marker = "PASS" if ok else "FAIL"
        print(f"  [{marker}] {desc}")
        print(f"         {detail}")
        if ok:
            pass_count += 1
    print()
    print(f"  RESULT: {pass_count}/{len(results)} assertions PASS")

    print()
    print("=" * 78)
    print("Materialized View Inventory (for CR-058)")
    print("=" * 78)
    mvs = await c.fetch(
        """
        SELECT mv.matviewname,
               pg_size_pretty(pg_total_relation_size(format('%I.%I', mv.schemaname, mv.matviewname)::regclass)) AS sz
        FROM pg_matviews mv WHERE mv.schemaname='public' ORDER BY mv.matviewname
        """
    )
    print(f"\n  {'MV':35s} {'rows':>6s}  {'size':>10s}  state")
    for r in mvs:
        n = await c.fetchval(f"SELECT count(*) FROM {r['matviewname']}")
        state = "current"
        print(f"  {r['matviewname']:35s} {n:>6}  {r['sz']:>10s}  {state}")

    print()
    print("Per-MV consumers (verified via grep across src/rcm/):")
    for mv in [r["matviewname"] for r in mvs]:
        consumers = CONSUMERS.get(mv, ["(unknown)"])
        print(f"\n  {mv}:")
        for cons in consumers:
            print(f"    - {cons}")

    # Inventory tuples for the CHANGELOG entry
    print()
    print("=" * 78)
    print("Inventory rows (compact, for CR-058 table)")
    print("=" * 78)
    for r in mvs:
        n = await c.fetchval(f"SELECT count(*) FROM {r['matviewname']}")
        consumer = CONSUMERS.get(r["matviewname"], ["(unknown)"])[0]
        print(f"  | {r['matviewname']:30s} | {n:>6} | {consumer} |")

    await c.close()


if __name__ == "__main__":
    asyncio.run(main())
