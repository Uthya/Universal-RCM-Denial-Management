"""CR-057 validation — read-only assertions after migration 0016.

  1. mv_patient_claim_history row count = 20,879
  2. Zero soft-deleted claims present in mv_patient_claim_history
  3. Distinct patient count = 10,000 (= 10,200 - 200 fully-deleted patients)
  4. patient_seq correctness for sampled mixed patient (id=62) = 8 (was 17)
  5. Aggregate charge sum = $16,849,568.00 ± 1%
  6. (added by user) Count of ACTIVE claims whose Cat-G feature values
     would change after the correction — i.e., active claims belonging to
     the 50 mixed patients
"""
from __future__ import annotations

import asyncio

import asyncpg

DSN = "postgresql://postgres:P0stgreSQL%21Dev%23847@104.130.220.20:30432/rcm_denials"


async def main() -> None:
    c = await asyncpg.connect(dsn=DSN, timeout=20)

    print("Alembic head:", await c.fetchval("SELECT version_num FROM alembic_version"))
    print()

    results = []

    # 1. Row count
    n = await c.fetchval("SELECT count(*) FROM mv_patient_claim_history")
    ok = n == 20879
    results.append(("mv_patient_claim_history row count = 20,879",
                    ok, f"actual={n}"))

    # 2. Zero soft-deleted claims present
    n_deleted = await c.fetchval(
        "SELECT count(*) FROM mv_patient_claim_history mv "
        "JOIN claims c ON c.id = mv.claim_id "
        "WHERE c.deleted_at IS NOT NULL"
    )
    ok = n_deleted == 0
    results.append(("Zero soft-deleted claims in mv_patient_claim_history",
                    ok, f"deleted_in_mv={n_deleted}"))

    # 3. Distinct patient count
    n_patients = await c.fetchval(
        "SELECT count(DISTINCT patient_id) FROM mv_patient_claim_history"
    )
    ok = n_patients == 10000
    results.append(("Distinct patient count = 10,000",
                    ok, f"actual={n_patients}"))

    # 4. patient_seq correctness for mixed patient id=62
    max_seq_62 = await c.fetchval(
        "SELECT max(patient_seq) FROM mv_patient_claim_history WHERE patient_id = 62"
    )
    ok = max_seq_62 == 8
    results.append(("patient_seq for patient_id=62 max = 8 (was 17 in defective MV)",
                    ok, f"actual={max_seq_62}"))

    # 5. Aggregate charge sum within 1% of $16,849,568.00
    total_charge = await c.fetchval(
        "SELECT sum(total_charge_amount) FROM mv_patient_claim_history"
    )
    total_charge = float(total_charge) if total_charge is not None else 0.0
    target = 16849568.00
    tol = target * 0.01
    ok = abs(total_charge - target) <= tol
    results.append((f"Aggregate charge sum within 1% of ${target:.2f}",
                    ok, f"actual=${total_charge:.2f} delta=${total_charge - target:+.2f}"))

    # 6. (NEW) Active claims belonging to mixed patients — these are the
    # active claims whose Cat-G feature values are now corrected
    affected_active = await c.fetchval(
        """
        SELECT count(*) FROM claims c
        WHERE c.deleted_at IS NULL
          AND c.patient_id IS NOT NULL
          AND c.service_from_date IS NOT NULL
          AND c.patient_id IN (
            SELECT patient_id FROM claims
            WHERE patient_id IS NOT NULL AND service_from_date IS NOT NULL
            GROUP BY patient_id
            HAVING count(*) FILTER (WHERE deleted_at IS NULL) > 0
               AND count(*) FILTER (WHERE deleted_at IS NOT NULL) > 0
          )
        """
    )
    # Also count distinct mixed patients for context.
    mixed_patients = await c.fetchval(
        """
        SELECT count(*) FROM (
          SELECT patient_id FROM claims
          WHERE patient_id IS NOT NULL AND service_from_date IS NOT NULL
          GROUP BY patient_id
          HAVING count(*) FILTER (WHERE deleted_at IS NULL) > 0
             AND count(*) FILTER (WHERE deleted_at IS NOT NULL) > 0
        ) s
        """
    )
    results.append((
        "ACTIVE claims whose Cat-G feature values change due to CR-057 "
        "(active claims belonging to 'mixed' patients)",
        affected_active > 0,
        f"affected_active_claims={affected_active}  mixed_patients={mixed_patients}"
    ))

    print("=" * 78)
    print("Validation assertions")
    print("=" * 78)
    pass_count = 0
    for desc, ok, detail in results:
        marker = "PASS" if ok else "FAIL"
        print(f"  [{marker}] {desc}")
        print(f"         {detail}")
        if ok:
            pass_count += 1
    print()
    print(f"  RESULT: {pass_count}/{len(results)} assertions PASS")

    # Supplementary distributions for the CHANGELOG entry.
    print()
    print("Supplementary — Cat-G consumer impact summary:")
    print(f"  total active claims with patient_id+date:     {await c.fetchval('SELECT count(*) FROM mv_patient_claim_history')}")
    print(f"  distinct patients in mv_pch:                  {n_patients}")
    print(f"  patients with corrected history (mixed):      {mixed_patients}")
    print(f"  active claims with corrected Cat-G features:  {affected_active}")

    print()
    print("Supplementary — patient_seq integrity sample (patient 62):")
    rows = await c.fetch(
        "SELECT claim_id, service_from_date, patient_seq FROM mv_patient_claim_history "
        "WHERE patient_id = 62 ORDER BY patient_seq"
    )
    for r in rows:
        print(f"  claim {r['claim_id']:>5}  {r['service_from_date']}  seq={r['patient_seq']}")

    await c.close()


if __name__ == "__main__":
    asyncio.run(main())
