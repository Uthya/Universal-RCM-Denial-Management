"""Read-only investigation: 326 soft-deleted claims that would enter
mv_patient_claim_history.

Parallels the mv_claim_labels investigation:
  - Origin (file_name patterns / batches / lifetimes)
  - Patient-level impact (how many patients affected; mixed vs fully-deleted)
  - Effect on patient_seq window numbering
  - Effect on Cat-G aggregate queries (counts, sums, max(service_from_date))

No writes. No REFRESH. No schema change.
"""
from __future__ import annotations

import asyncio

import asyncpg

DSN = "postgresql://postgres:P0stgreSQL%21Dev%23847@104.130.220.20:30432/rcm_denials"

# The 326-row deleted cohort that mv_patient_claim_history (current def) would
# include. Filter parallels the MV's WHERE clause + the deleted_at predicate.
DELETED_PCH_CTE = """
WITH deleted_pch AS (
  SELECT c.*
  FROM claims c
  WHERE c.deleted_at IS NOT NULL
    AND c.patient_id IS NOT NULL
    AND c.service_from_date IS NOT NULL
)
"""


async def main() -> None:
    c = await asyncpg.connect(dsn=DSN, timeout=20)

    # 0. Cohort size confirmation
    n = await c.fetchval(DELETED_PCH_CTE + "SELECT count(*) FROM deleted_pch")
    print(f"Cohort size confirmed: {n} soft-deleted claims would enter mv_patient_claim_history\n")

    # 1. Source files
    print("=" * 78)
    print("1. Source EDI files (which upload batches produced these claims?)")
    print("=" * 78)
    rows = await c.fetch(
        DELETED_PCH_CTE +
        "SELECT ef.id AS file_id, ef.file_name, ef.file_type::text, "
        "       ef.parse_status::text, ef.deleted_at IS NOT NULL AS file_deleted, "
        "       count(*) AS claim_count "
        "FROM deleted_pch dp JOIN edi_files ef ON ef.id = dp.edi_file_id "
        "GROUP BY ef.id, ef.file_name, ef.file_type, ef.parse_status, ef.deleted_at "
        "ORDER BY 6 DESC LIMIT 30"
    )
    print(f"  {'file_id':>8}  {'file_type':10s} {'parse':10s} {'f_del':5s} {'claims':>6}  file_name")
    for r in rows:
        fd = 'YES' if r['file_deleted'] else 'no'
        print(f"  {r['file_id']:>8}  {r['file_type']:10s} {r['parse_status']:10s} {fd:5s} {r['claim_count']:>6}  {r['file_name']}")

    # 1b. file_name family rollup
    print("\nFile-name family rollup (regex on filename prefix):")
    rows = await c.fetch(
        DELETED_PCH_CTE +
        "SELECT "
        "  CASE "
        "    WHEN ef.file_name LIKE 'QX500%' THEN 'QX500' "
        "    WHEN ef.file_name LIKE 'FV6%'   THEN 'FV6' "
        "    WHEN ef.file_name LIKE 'TR15%'  THEN 'TR15' "
        "    WHEN ef.file_name LIKE 'P10%'   THEN 'P10' "
        "    ELSE 'OTHER' "
        "  END AS family, "
        "  count(*) AS n "
        "FROM deleted_pch dp JOIN edi_files ef ON ef.id = dp.edi_file_id "
        "GROUP BY 1 ORDER BY 2 DESC"
    )
    for r in rows:
        print(f"  family={r['family']:>10s}  claims={r['n']:>5}")

    # 2. frequency_code distribution (this is the key difference vs mv_claim_labels)
    print()
    print("=" * 78)
    print("2. Frequency code distribution (mv_pch has NO frequency filter, unlike mv_claim_labels)")
    print("=" * 78)
    rows = await c.fetch(
        DELETED_PCH_CTE +
        "SELECT COALESCE(frequency_code, '(null)') AS f, count(*) FROM deleted_pch GROUP BY 1 ORDER BY 2 DESC"
    )
    for r in rows:
        print(f"  freq={r['f']!r:10s}  {r['count']:>4}")

    # 3. Patient-level impact
    print()
    print("=" * 78)
    print("3. Patient-level impact: how many patients touched, mixed vs all-deleted?")
    print("=" * 78)
    rows = await c.fetch(
        """
        WITH patient_status AS (
          SELECT c.patient_id,
                 sum(CASE WHEN c.deleted_at IS NULL THEN 1 ELSE 0 END) AS n_active,
                 sum(CASE WHEN c.deleted_at IS NOT NULL THEN 1 ELSE 0 END) AS n_deleted
          FROM claims c
          WHERE c.patient_id IS NOT NULL AND c.service_from_date IS NOT NULL
          GROUP BY c.patient_id
        )
        SELECT
          count(*) FILTER (WHERE n_deleted > 0) AS patients_with_any_deleted,
          count(*) FILTER (WHERE n_deleted > 0 AND n_active = 0) AS patients_all_deleted,
          count(*) FILTER (WHERE n_deleted > 0 AND n_active > 0) AS patients_mixed,
          count(*) AS total_patients_in_pch_eligible
        FROM patient_status
        """
    )
    r = rows[0]
    print(f"  total distinct patients (with patient_id + service_from_date): {r['total_patients_in_pch_eligible']}")
    print(f"  patients with at least one DELETED claim:                      {r['patients_with_any_deleted']}")
    print(f"  patients where ALL claims are deleted:                         {r['patients_all_deleted']}")
    print(f"  patients with MIXED active+deleted (highest concern):          {r['patients_mixed']}")

    # 3b. Distribution of deletion fractions among mixed patients
    print("\n  Distribution of n_deleted/total for the 'mixed' patients:")
    rows = await c.fetch(
        """
        WITH patient_status AS (
          SELECT c.patient_id,
                 sum(CASE WHEN c.deleted_at IS NULL THEN 1 ELSE 0 END) AS n_active,
                 sum(CASE WHEN c.deleted_at IS NOT NULL THEN 1 ELSE 0 END) AS n_deleted
          FROM claims c
          WHERE c.patient_id IS NOT NULL AND c.service_from_date IS NOT NULL
          GROUP BY c.patient_id
        )
        SELECT
          width_bucket(100.0 * n_deleted / (n_active + n_deleted), ARRAY[1, 25, 50, 75, 99]) AS bucket,
          count(*) AS n
        FROM patient_status
        WHERE n_deleted > 0 AND n_active > 0
        GROUP BY 1 ORDER BY 1
        """
    )
    bucket_labels = {1: "1-25%", 2: "25-50%", 3: "50-75%", 4: "75-99%", 5: ">=99%"}
    for r in rows:
        print(f"    deleted-fraction bucket {bucket_labels.get(r['bucket'], 'n/a')}: {r['n']} patients")

    # 4. patient_seq window-numbering impact
    print()
    print("=" * 78)
    print("4. patient_seq impact: does removing deleted claims renumber the window?")
    print("=" * 78)
    print("  Sample MIXED patients (showing rows from current MV definition):")
    rows = await c.fetch(
        """
        WITH mixed AS (
          SELECT c.patient_id,
                 sum(CASE WHEN c.deleted_at IS NULL THEN 1 ELSE 0 END) AS n_active,
                 sum(CASE WHEN c.deleted_at IS NOT NULL THEN 1 ELSE 0 END) AS n_deleted
          FROM claims c
          WHERE c.patient_id IS NOT NULL AND c.service_from_date IS NOT NULL
          GROUP BY c.patient_id
          HAVING sum(CASE WHEN c.deleted_at IS NULL THEN 1 ELSE 0 END) > 0
             AND sum(CASE WHEN c.deleted_at IS NOT NULL THEN 1 ELSE 0 END) > 0
        )
        SELECT m.patient_id, m.n_active, m.n_deleted
        FROM mixed m LIMIT 5
        """
    )
    if not rows:
        print("    (no mixed patients found — all soft-deleted claims belong to patients with no active claims)")
    else:
        for r in rows:
            pid = r["patient_id"]
            print(f"\n  patient_id={pid}  active={r['n_active']}  deleted={r['n_deleted']}")
            detail = await c.fetch(
                """
                SELECT id, service_from_date, deleted_at IS NOT NULL AS is_deleted,
                       row_number() OVER (PARTITION BY patient_id ORDER BY service_from_date) AS seq_with_deleted,
                       row_number() OVER (PARTITION BY patient_id ORDER BY service_from_date)
                         FILTER (WHERE deleted_at IS NULL) AS seq_without_deleted
                FROM claims
                WHERE patient_id = $1 AND service_from_date IS NOT NULL
                ORDER BY service_from_date, id
                """,
                pid,
            )
            for d in detail:
                deleted_marker = "D" if d["is_deleted"] else " "
                print(f"    claim {d['id']:>5} {d['service_from_date']} [{deleted_marker}] "
                      f"seq_with_del={d['seq_with_deleted']} seq_without_del={d['seq_without_deleted']}")

    # 5. Aggregate impact on the Cat-G window queries
    print()
    print("=" * 78)
    print("5. Aggregate impact on Category-G window queries")
    print("=" * 78)
    r = await c.fetchrow(
        """
        SELECT
          (SELECT count(*) FROM claims WHERE patient_id IS NOT NULL AND service_from_date IS NOT NULL) AS pch_with_deleted,
          (SELECT count(*) FROM claims WHERE patient_id IS NOT NULL AND service_from_date IS NOT NULL
                                            AND deleted_at IS NULL) AS pch_without_deleted,
          (SELECT sum(total_charge_amount) FROM claims WHERE patient_id IS NOT NULL AND service_from_date IS NOT NULL) AS sum_charge_with_deleted,
          (SELECT sum(total_charge_amount) FROM claims WHERE patient_id IS NOT NULL AND service_from_date IS NOT NULL
                                            AND deleted_at IS NULL) AS sum_charge_without_deleted
        """
    )
    print(f"  total rows         : with-deleted={r['pch_with_deleted']}   without-deleted={r['pch_without_deleted']}")
    print(f"  sum charge ($)     : with-deleted={r['sum_charge_with_deleted']:>14}   without-deleted={r['sum_charge_without_deleted']:>14}")
    delta_rows = r['pch_with_deleted'] - r['pch_without_deleted']
    delta_chg = float(r['sum_charge_with_deleted']) - float(r['sum_charge_without_deleted'])
    pct_rows = 100.0 * delta_rows / max(r['pch_without_deleted'], 1)
    pct_chg = 100.0 * delta_chg / max(float(r['sum_charge_without_deleted']), 1.0)
    print(f"  delta              : {delta_rows} rows ({pct_rows:.2f}% inflation)  ${delta_chg:.2f} ({pct_chg:.2f}% inflation)")

    # 6. Top-affected patients: how much does THEIR feature value shift?
    print()
    print("=" * 78)
    print("6. Sample: most-affected patients (highest absolute deleted-claim count)")
    print("=" * 78)
    rows = await c.fetch(
        """
        WITH patient_status AS (
          SELECT c.patient_id,
                 sum(CASE WHEN c.deleted_at IS NULL THEN 1 ELSE 0 END) AS n_active,
                 sum(CASE WHEN c.deleted_at IS NOT NULL THEN 1 ELSE 0 END) AS n_deleted,
                 sum(CASE WHEN c.deleted_at IS NOT NULL THEN c.total_charge_amount ELSE 0 END) AS deleted_charge,
                 sum(c.total_charge_amount) AS total_charge
          FROM claims c
          WHERE c.patient_id IS NOT NULL AND c.service_from_date IS NOT NULL
          GROUP BY c.patient_id
        )
        SELECT patient_id, n_active, n_deleted, deleted_charge, total_charge
        FROM patient_status
        WHERE n_deleted > 0
        ORDER BY n_deleted DESC LIMIT 10
        """
    )
    print(f"  {'patient_id':>10}  {'active':>6}  {'deleted':>7}  {'del_chg':>10}  {'total_chg':>10}  {'shift%':>7}")
    for r in rows:
        shift = 100.0 * float(r['deleted_charge']) / max(float(r['total_charge']), 1.0)
        print(f"  {r['patient_id']:>10}  {r['n_active']:>6}  {r['n_deleted']:>7}  {float(r['deleted_charge']):>10.2f}  {float(r['total_charge']):>10.2f}  {shift:>6.2f}%")

    # 7. Created/deleted timeline (do these line up with the QX500 sweep on 2026-06-09?)
    print()
    print("=" * 78)
    print("7. Timing — created and deleted windows")
    print("=" * 78)
    r = await c.fetchrow(
        DELETED_PCH_CTE +
        "SELECT min(created_at) AS first_created, max(created_at) AS last_created, "
        "       min(deleted_at) AS first_deleted, max(deleted_at) AS last_deleted "
        "FROM deleted_pch"
    )
    print(f"  created span: {r['first_created']} -> {r['last_created']}")
    print(f"  deleted span: {r['first_deleted']} -> {r['last_deleted']}")

    # 8. Are these claims the SAME 326 family as the mv_claim_labels deleted cohort?
    # (Some may overlap with the 260; some are additional because mv_pch has looser filter)
    print()
    print("=" * 78)
    print("8. Overlap with the mv_claim_labels deleted cohort (260 claims)")
    print("=" * 78)
    r = await c.fetchrow(
        """
        WITH mv_cl_deleted AS (
          SELECT c.id FROM claims c
          WHERE c.deleted_at IS NOT NULL
            AND (c.frequency_code IS NULL OR c.frequency_code = '1')
            AND c.service_from_date IS NOT NULL
            AND EXISTS (SELECT 1 FROM remittance_claims rc
                        WHERE rc.claim_id = c.id
                          AND (rc.claim_status_code = '4' OR rc.claim_status_code IN ('1','2','3','19','20')))
        ),
        mv_pch_deleted AS (
          SELECT c.id FROM claims c
          WHERE c.deleted_at IS NOT NULL
            AND c.patient_id IS NOT NULL
            AND c.service_from_date IS NOT NULL
        )
        SELECT
          (SELECT count(*) FROM mv_cl_deleted) AS in_cl,
          (SELECT count(*) FROM mv_pch_deleted) AS in_pch,
          (SELECT count(*) FROM mv_cl_deleted INTERSECT SELECT id FROM mv_pch_deleted) AS in_both,
          (SELECT count(*) FROM mv_pch_deleted WHERE id NOT IN (SELECT id FROM mv_cl_deleted)) AS pch_only,
          (SELECT count(*) FROM mv_cl_deleted WHERE id NOT IN (SELECT id FROM mv_pch_deleted)) AS cl_only
        """
    )
    print(f"  in mv_claim_labels (260):       {r['in_cl']}")
    print(f"  in mv_patient_claim_history:    {r['in_pch']}")
    print(f"  in BOTH:                         {r['in_both']}")
    print(f"  in mv_pch but NOT mv_claim_labels: {r['pch_only']}  (these are the new ones — likely replacements/voids or no-remit originals)")
    print(f"  in mv_claim_labels but NOT mv_pch: {r['cl_only']}  (would be claims without patient_id)")

    # 9. What frequency codes are unique to mv_pch-only deleted cohort?
    print()
    print("=" * 78)
    print("9. Frequency code of the mv_pch-only deleted rows (those not in mv_claim_labels)")
    print("=" * 78)
    rows = await c.fetch(
        """
        WITH mv_cl_deleted AS (
          SELECT c.id FROM claims c
          WHERE c.deleted_at IS NOT NULL
            AND (c.frequency_code IS NULL OR c.frequency_code = '1')
            AND c.service_from_date IS NOT NULL
            AND EXISTS (SELECT 1 FROM remittance_claims rc
                        WHERE rc.claim_id = c.id
                          AND (rc.claim_status_code = '4' OR rc.claim_status_code IN ('1','2','3','19','20')))
        )
        SELECT COALESCE(c.frequency_code, '(null)') AS f, count(*) AS n,
               sum(CASE WHEN EXISTS (SELECT 1 FROM remittance_claims rc WHERE rc.claim_id = c.id)
                        THEN 1 ELSE 0 END) AS with_remit
        FROM claims c
        WHERE c.deleted_at IS NOT NULL
          AND c.patient_id IS NOT NULL
          AND c.service_from_date IS NOT NULL
          AND c.id NOT IN (SELECT id FROM mv_cl_deleted)
        GROUP BY 1 ORDER BY 2 DESC
        """
    )
    for r in rows:
        print(f"  freq={r['f']!r:10s}  n={r['n']:>4}  with_remit={r['with_remit']}")

    await c.close()


if __name__ == "__main__":
    asyncio.run(main())
