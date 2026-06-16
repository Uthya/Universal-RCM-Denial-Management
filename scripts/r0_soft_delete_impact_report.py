"""Read-only investigation: impact of soft-deleted claims on mv_claim_labels.

Asks: of the 6,592 rows the dry-run produced, how many are soft-deleted,
and does removing them materially change class balance / payer mix /
provider mix?

No writes. No REFRESH.
"""
from __future__ import annotations

import asyncio
from collections import Counter

import asyncpg

DSN = "postgresql://postgres:P0stgreSQL%21Dev%23847@104.130.220.20:30432/rcm_denials"


# Same body as the MV, plus an extra column flagging soft-delete state on the
# claim. We materialize to a temp CTE so we can slice it many ways without
# re-running the aggregation.
LABELLED_CTE = """
WITH labelled AS (
  SELECT
    c.id                              AS claim_id,
    c.deleted_at IS NOT NULL          AS is_deleted,
    c.payer_id,
    c.billing_provider_id,
    c.service_variant,
    c.claim_subtype,
    CASE
        WHEN bool_or(rc.claim_status_code = '4') THEN 1
        WHEN bool_or(rc.claim_status_code IN ('1','2','3','19','20')) THEN 0
        ELSE NULL
    END AS denied
  FROM claims c
  LEFT JOIN remittance_claims rc ON rc.claim_id = c.id
  WHERE (c.frequency_code IS NULL OR c.frequency_code = '1')
    AND c.service_from_date IS NOT NULL
  GROUP BY c.id, c.deleted_at, c.payer_id, c.billing_provider_id,
           c.service_variant, c.claim_subtype
  HAVING (bool_or(rc.claim_status_code = '4')
          OR bool_or(rc.claim_status_code IN ('1','2','3','19','20')))
)
"""


def _pct(numerator: int, denominator: int) -> str:
    return "n/a" if denominator == 0 else f"{100.0 * numerator / denominator:.2f}%"


async def main() -> None:
    c = await asyncpg.connect(dsn=DSN, timeout=20)

    # 1. Confirm total
    total = await c.fetchval(LABELLED_CTE + "SELECT count(*) FROM labelled")
    print("=" * 78)
    print("1. Total rows in mv_claim_labels dry-run")
    print("=" * 78)
    print(f"  total = {total}")

    # 2. Soft-delete breakdown
    rows = await c.fetch(
        LABELLED_CTE +
        "SELECT is_deleted, count(*) AS n, "
        "       sum(CASE WHEN denied=1 THEN 1 ELSE 0 END) AS denied_n, "
        "       sum(CASE WHEN denied=0 THEN 1 ELSE 0 END) AS paid_n "
        "FROM labelled GROUP BY is_deleted ORDER BY is_deleted"
    )
    n_active = n_deleted = denied_active = denied_deleted = paid_active = paid_deleted = 0
    for r in rows:
        if r["is_deleted"]:
            n_deleted = r["n"]; denied_deleted = r["denied_n"]; paid_deleted = r["paid_n"]
        else:
            n_active = r["n"];  denied_active  = r["denied_n"]; paid_active  = r["paid_n"]

    print()
    print("=" * 78)
    print("2. Soft-delete breakdown of the 6,592 rows")
    print("=" * 78)
    print(f"  active   (deleted_at IS NULL):     {n_active:>5}  ({_pct(n_active, total)})")
    print(f"  deleted  (deleted_at IS NOT NULL): {n_deleted:>5}  ({_pct(n_deleted, total)})")

    print()
    print("=" * 78)
    print("3. Denial rate among DELETED claims (in the labelled set)")
    print("=" * 78)
    print(f"  denied   = {denied_deleted}")
    print(f"  paid     = {paid_deleted}")
    print(f"  denial rate = {_pct(denied_deleted, n_deleted)}")

    print()
    print("=" * 78)
    print("4. Denial rate among ACTIVE claims (in the labelled set)")
    print("=" * 78)
    print(f"  denied   = {denied_active}")
    print(f"  paid     = {paid_active}")
    print(f"  denial rate = {_pct(denied_active, n_active)}")

    print()
    print("=" * 78)
    print("5. Material-change analysis: with deleted vs without deleted")
    print("=" * 78)

    # 5a. Class balance
    overall_denial = (denied_active + denied_deleted) / max(total, 1)
    active_only_denial = denied_active / max(n_active, 1)
    delta_pp = (overall_denial - active_only_denial) * 100
    print()
    print("5a. Class balance (denial rate)")
    print(f"  with deleted:    {_pct(denied_active + denied_deleted, total)}")
    print(f"  without deleted: {_pct(denied_active, n_active)}")
    print(f"  shift:           {delta_pp:+.3f} percentage points")

    # 5b. Payer distributions — top N payers each way
    print()
    print("5b. Payer distribution (claim share by payer_id)")
    rows_with = await c.fetch(
        LABELLED_CTE +
        "SELECT payer_id, count(*) AS n FROM labelled "
        "GROUP BY payer_id ORDER BY 2 DESC"
    )
    rows_without = await c.fetch(
        LABELLED_CTE +
        "SELECT payer_id, count(*) AS n FROM labelled WHERE NOT is_deleted "
        "GROUP BY payer_id ORDER BY 2 DESC"
    )
    map_with = {r["payer_id"]: r["n"] for r in rows_with}
    map_without = {r["payer_id"]: r["n"] for r in rows_without}
    all_payers = sorted(set(map_with) | set(map_without), key=lambda k: -map_with.get(k, 0))
    total_with = sum(map_with.values())
    total_without = sum(map_without.values())
    print(f"  {'payer_id':>10}  {'with':>8} {'with %':>8}  {'without':>8} {'without %':>10}  {'delta_pp':>10}")
    max_shift_pp = 0.0
    for p in all_payers:
        w = map_with.get(p, 0)
        wo = map_without.get(p, 0)
        wpct = 100.0 * w / max(total_with, 1)
        wopct = 100.0 * wo / max(total_without, 1)
        d = wopct - wpct
        max_shift_pp = max(max_shift_pp, abs(d))
        pid = "NULL" if p is None else str(p)
        print(f"  {pid:>10}  {w:>8} {wpct:>7.2f}%  {wo:>8} {wopct:>9.2f}%  {d:>+9.3f}pp")
    print(f"  max abs shift across payers: {max_shift_pp:.3f}pp")

    # 5c. Provider distributions
    print()
    print("5c. Provider distribution (claim share by billing_provider_id)")
    rows_with = await c.fetch(
        LABELLED_CTE +
        "SELECT billing_provider_id, count(*) AS n FROM labelled "
        "GROUP BY billing_provider_id ORDER BY 2 DESC"
    )
    rows_without = await c.fetch(
        LABELLED_CTE +
        "SELECT billing_provider_id, count(*) AS n FROM labelled WHERE NOT is_deleted "
        "GROUP BY billing_provider_id ORDER BY 2 DESC"
    )
    map_with = {r["billing_provider_id"]: r["n"] for r in rows_with}
    map_without = {r["billing_provider_id"]: r["n"] for r in rows_without}
    all_provs = sorted(set(map_with) | set(map_without), key=lambda k: -map_with.get(k, 0))
    total_with = sum(map_with.values())
    total_without = sum(map_without.values())
    print(f"  {'provider_id':>14}  {'with':>8} {'with %':>8}  {'without':>8} {'without %':>10}  {'delta_pp':>10}")
    max_shift_pp_prov = 0.0
    for p in all_provs:
        w = map_with.get(p, 0)
        wo = map_without.get(p, 0)
        wpct = 100.0 * w / max(total_with, 1)
        wopct = 100.0 * wo / max(total_without, 1)
        d = wopct - wpct
        max_shift_pp_prov = max(max_shift_pp_prov, abs(d))
        pid = "NULL" if p is None else str(p)
        print(f"  {pid:>14}  {w:>8} {wpct:>7.2f}%  {wo:>8} {wopct:>9.2f}%  {d:>+9.3f}pp")
    print(f"  max abs shift across providers: {max_shift_pp_prov:.3f}pp")

    # 5d. Variant/subtype breakdown — sanity check
    print()
    print("5d. Variant/subtype distribution")
    rows_with = await c.fetch(
        LABELLED_CTE +
        "SELECT service_variant, claim_subtype, count(*) AS n FROM labelled "
        "GROUP BY 1,2 ORDER BY 3 DESC"
    )
    rows_without = await c.fetch(
        LABELLED_CTE +
        "SELECT service_variant, claim_subtype, count(*) AS n FROM labelled WHERE NOT is_deleted "
        "GROUP BY 1,2 ORDER BY 3 DESC"
    )
    map_with = {(r["service_variant"], r["claim_subtype"]): r["n"] for r in rows_with}
    map_without = {(r["service_variant"], r["claim_subtype"]): r["n"] for r in rows_without}
    keys = sorted(set(map_with) | set(map_without), key=lambda k: -map_with.get(k, 0))
    total_with = sum(map_with.values())
    total_without = sum(map_without.values())
    print(f"  {'variant':>10} {'subtype':>20}  {'with':>8} {'with %':>8}  {'without':>8} {'without %':>10}  {'delta_pp':>10}")
    max_shift_pp_var = 0.0
    for v, s in keys:
        w = map_with.get((v, s), 0)
        wo = map_without.get((v, s), 0)
        wpct = 100.0 * w / max(total_with, 1)
        wopct = 100.0 * wo / max(total_without, 1)
        d = wopct - wpct
        max_shift_pp_var = max(max_shift_pp_var, abs(d))
        print(f"  {v:>10} {s:>20}  {w:>8} {wpct:>7.2f}%  {wo:>8} {wopct:>9.2f}%  {d:>+9.3f}pp")
    print(f"  max abs shift across variant/subtype: {max_shift_pp_var:.3f}pp")

    print()
    print("=" * 78)
    print("Headline numbers (collected for the report)")
    print("=" * 78)
    print(f"  total_rows                     = {total}")
    print(f"  deleted_rows                   = {n_deleted}  ({_pct(n_deleted, total)})")
    print(f"  active_rows                    = {n_active}  ({_pct(n_active, total)})")
    print(f"  denial_rate_deleted            = {_pct(denied_deleted, n_deleted)}")
    print(f"  denial_rate_active             = {_pct(denied_active, n_active)}")
    print(f"  denial_rate_overall            = {_pct(denied_active + denied_deleted, total)}")
    print(f"  class_balance_shift_pp         = {delta_pp:+.3f}")
    print(f"  max_payer_distribution_shift   = {max_shift_pp:.3f}pp")
    print(f"  max_provider_distribution_shift= {max_shift_pp_prov:.3f}pp")
    print(f"  max_variant_distribution_shift = {max_shift_pp_var:.3f}pp")

    await c.close()


if __name__ == "__main__":
    asyncio.run(main())
