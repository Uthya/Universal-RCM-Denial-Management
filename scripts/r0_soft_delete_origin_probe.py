"""Read-only investigation: origin of the 260 soft-deleted claims that the
mv_claim_labels body would include.

Per the prior soft-delete impact report, the 260 deleted claims:
  - have 25.38% denial rate (vs 10.52% for active)
  - account for the entire claim volume of payers 7 and 13
  - skew toward 837P/healthcare variant

This probe identifies WHERE those rows came from so we can decide whether
they're (a) legitimate historical adjudications that should train the model
or (b) test/loader artefacts that should be excluded.

No writes. No REFRESH. No schema change.
"""
from __future__ import annotations

import asyncio
import json

import asyncpg

DSN = "postgresql://postgres:P0stgreSQL%21Dev%23847@104.130.220.20:30432/rcm_denials"

# The 260-row deleted cohort lives at the intersection of:
#   - mv_claim_labels body (frequency + service_from_date + HAVING)
#   - deleted_at IS NOT NULL
DELETED_COHORT_CTE = """
WITH deleted_cohort AS (
  SELECT c.*
  FROM claims c
  WHERE c.deleted_at IS NOT NULL
    AND (c.frequency_code IS NULL OR c.frequency_code = '1')
    AND c.service_from_date IS NOT NULL
    AND EXISTS (
      SELECT 1 FROM remittance_claims rc
      WHERE rc.claim_id = c.id
        AND (rc.claim_status_code = '4'
             OR rc.claim_status_code IN ('1','2','3','19','20'))
    )
)
"""


async def main() -> None:
    c = await asyncpg.connect(dsn=DSN, timeout=20)

    # 0. Cohort size confirmation
    n = await c.fetchval(DELETED_COHORT_CTE + "SELECT count(*) FROM deleted_cohort")
    print(f"Cohort size confirmed: {n} soft-deleted claims in mv_claim_labels eligible set\n")

    # 1. When created
    print("=" * 78)
    print("1. When were these claims CREATED?")
    print("=" * 78)
    rows = await c.fetch(
        DELETED_COHORT_CTE +
        "SELECT date_trunc('hour', created_at) AS bucket, count(*) "
        "FROM deleted_cohort GROUP BY 1 ORDER BY 1"
    )
    for r in rows:
        print(f"  created {r['bucket']}  -> {r['count']:>4}")
    span = await c.fetchrow(
        DELETED_COHORT_CTE +
        "SELECT min(created_at) AS first, max(created_at) AS last, "
        "EXTRACT(EPOCH FROM (max(created_at) - min(created_at)))/60 AS span_min "
        "FROM deleted_cohort"
    )
    print(f"  span: {span['first']} -> {span['last']}  ({span['span_min']:.1f} min)")

    # 2. When deleted
    print()
    print("=" * 78)
    print("2. When were these claims SOFT-DELETED?")
    print("=" * 78)
    rows = await c.fetch(
        DELETED_COHORT_CTE +
        "SELECT date_trunc('hour', deleted_at) AS bucket, count(*) "
        "FROM deleted_cohort GROUP BY 1 ORDER BY 1"
    )
    for r in rows:
        print(f"  deleted {r['bucket']}  -> {r['count']:>4}")
    span = await c.fetchrow(
        DELETED_COHORT_CTE +
        "SELECT min(deleted_at) AS first, max(deleted_at) AS last "
        "FROM deleted_cohort"
    )
    print(f"  span: {span['first']} -> {span['last']}")

    # 3. Time between create and delete (how long did each live?)
    print()
    print("=" * 78)
    print("3. How long did each claim live before being soft-deleted?")
    print("=" * 78)
    rows = await c.fetch(
        DELETED_COHORT_CTE +
        "SELECT "
        "  width_bucket(EXTRACT(EPOCH FROM (deleted_at - created_at))/60, "
        "               ARRAY[0,1,5,30,60,360,1440,10080]) AS bucket, "
        "  count(*) "
        "FROM deleted_cohort GROUP BY 1 ORDER BY 1"
    )
    bucket_labels = {
        1: "0-1 min",
        2: "1-5 min",
        3: "5-30 min",
        4: "30-60 min",
        5: "1-6 hrs",
        6: "6-24 hrs",
        7: "1-7 days",
        8: ">7 days",
    }
    for r in rows:
        print(f"  lifetime {bucket_labels.get(r['bucket'], 'n/a'):>10}  -> {r['count']:>4}")

    # 4. Which upload files / batches produced them
    print()
    print("=" * 78)
    print("4. Which EDI file (upload batch) produced these claims?")
    print("=" * 78)
    rows = await c.fetch(
        DELETED_COHORT_CTE +
        "SELECT ef.id AS edi_file_id, ef.file_name, ef.file_type::text, "
        "       ef.parse_status::text, ef.deleted_at IS NOT NULL AS file_deleted, "
        "       count(*) AS claim_count "
        "FROM deleted_cohort dc "
        "JOIN edi_files ef ON ef.id = dc.edi_file_id "
        "GROUP BY ef.id, ef.file_name, ef.file_type, ef.parse_status, ef.deleted_at "
        "ORDER BY 5 DESC LIMIT 30"
    )
    print(f"  {'file_id':>8}  {'file_type':10s} {'parse':10s} {'f_del':5s} {'claims':>6}  file_name")
    for r in rows:
        fd = 'YES' if r['file_deleted'] else 'no'
        print(f"  {r['edi_file_id']:>8}  {r['file_type']:10s} {r['parse_status']:10s} {fd:5s} {r['claim_count']:>6}  {r['file_name']}")

    # 5. By payer
    print()
    print("=" * 78)
    print("5. Payer breakdown of the 260 deleted claims")
    print("=" * 78)
    rows = await c.fetch(
        DELETED_COHORT_CTE +
        "SELECT dc.payer_id, p.canonical_name, count(*) AS n "
        "FROM deleted_cohort dc LEFT JOIN payers p ON p.id = dc.payer_id "
        "GROUP BY dc.payer_id, p.canonical_name ORDER BY 3 DESC"
    )
    for r in rows:
        pid = "NULL" if r['payer_id'] is None else str(r['payer_id'])
        nm = r['canonical_name'] or '(unknown)'
        print(f"  payer_id={pid:>6}  name={nm!r:30s}  claims={r['n']:>5}")

    # 6. Do active claims reuse the same claim_number values?
    print()
    print("=" * 78)
    print("6. Do any ACTIVE claims share claim_number with the deleted cohort?")
    print("=" * 78)
    overlap = await c.fetchval(
        DELETED_COHORT_CTE +
        "SELECT count(DISTINCT dc.claim_number) "
        "FROM deleted_cohort dc "
        "WHERE EXISTS (SELECT 1 FROM claims a "
        "              WHERE a.claim_number = dc.claim_number "
        "                AND a.deleted_at IS NULL)"
    )
    total_distinct = await c.fetchval(
        DELETED_COHORT_CTE +
        "SELECT count(DISTINCT claim_number) FROM deleted_cohort"
    )
    print(f"  distinct claim_numbers in deleted cohort: {total_distinct}")
    print(f"  of those, also present as ACTIVE claim:   {overlap}")
    if total_distinct:
        print(f"  re-upload coverage:                       {100.0*overlap/total_distinct:.1f}%")

    # Sample of overlapping claim numbers
    print()
    print("  Sample overlapping claim numbers (deleted vs active replacement):")
    rows = await c.fetch(
        DELETED_COHORT_CTE +
        "SELECT dc.claim_number, dc.id AS deleted_id, dc.payer_id AS d_payer, "
        "       a.id AS active_id, a.payer_id AS a_payer, "
        "       a.frequency_code AS a_freq, a.created_at AS a_created "
        "FROM deleted_cohort dc "
        "JOIN claims a ON a.claim_number = dc.claim_number AND a.deleted_at IS NULL "
        "LIMIT 10"
    )
    for r in rows:
        print(f"    cn={r['claim_number']:>20s}  deleted_id={r['deleted_id']:>6}(payer {r['d_payer']})  "
              f"-> active_id={r['active_id']:>6}(payer {r['a_payer']}, freq={r['a_freq']}, at={r['a_created']})")

    # 7. Do payers 7 and 13 appear in ANY active claim?
    print()
    print("=" * 78)
    print("7. Are payer_id 7 and 13 present in ACTIVE claims?")
    print("=" * 78)
    rows = await c.fetch(
        "SELECT payer_id, count(*) AS n_active, "
        "       count(CASE WHEN deleted_at IS NULL THEN 1 END) AS still_alive, "
        "       count(CASE WHEN deleted_at IS NOT NULL THEN 1 END) AS soft_deleted "
        "FROM claims WHERE payer_id IN (7, 13) GROUP BY payer_id ORDER BY payer_id"
    )
    for r in rows:
        print(f"  payer_id={r['payer_id']}  total={r['n_active']}  alive={r['still_alive']}  deleted={r['soft_deleted']}")
    p7 = await c.fetchval("SELECT canonical_name FROM payers WHERE id=7")
    p13 = await c.fetchval("SELECT canonical_name FROM payers WHERE id=13")
    print(f"  payers.id=7  canonical_name = {p7!r}")
    print(f"  payers.id=13 canonical_name = {p13!r}")

    # 8. What do the remittance_claims rows for the deleted cohort look like?
    print()
    print("=" * 78)
    print("8. Remittance rows for the deleted cohort — are they themselves deleted?")
    print("=" * 78)
    rows = await c.fetch(
        DELETED_COHORT_CTE +
        "SELECT rc.claim_status_code, count(*) AS n_remits, "
        "       count(DISTINCT rc.edi_file_id) AS distinct_835_files "
        "FROM deleted_cohort dc JOIN remittance_claims rc ON rc.claim_id = dc.id "
        "GROUP BY rc.claim_status_code ORDER BY 2 DESC"
    )
    for r in rows:
        print(f"  remit status_code={r['claim_status_code']!r:6s}  remit_rows={r['n_remits']:>4}  distinct_835_files={r['distinct_835_files']}")

    # Are the 835 files themselves still active?
    print()
    print("  835 files that produced the remits for the deleted cohort:")
    rows = await c.fetch(
        DELETED_COHORT_CTE +
        "SELECT ef.id, ef.file_name, ef.parse_status::text, "
        "       ef.deleted_at IS NOT NULL AS file_deleted, count(*) AS n "
        "FROM deleted_cohort dc "
        "JOIN remittance_claims rc ON rc.claim_id = dc.id "
        "JOIN edi_files ef ON ef.id = rc.edi_file_id "
        "GROUP BY ef.id, ef.file_name, ef.parse_status, ef.deleted_at "
        "ORDER BY 5 DESC LIMIT 20"
    )
    print(f"  {'file_id':>8}  {'parse':10s} {'f_del':5s} {'n_remits':>8}  file_name")
    for r in rows:
        fd = 'YES' if r['file_deleted'] else 'no'
        print(f"  {r['id']:>8}  {r['parse_status']:10s} {fd:5s} {r['n_remits']:>8}  {r['file_name']}")

    # 9. Are the deleted claims' EDI files themselves deleted?
    print()
    print("=" * 78)
    print("9. Were the source 837 files also soft-deleted, or only the claims?")
    print("=" * 78)
    rows = await c.fetch(
        DELETED_COHORT_CTE +
        "SELECT ef.deleted_at IS NOT NULL AS file_deleted, count(DISTINCT ef.id) AS n_files, count(*) AS n_claims "
        "FROM deleted_cohort dc JOIN edi_files ef ON ef.id = dc.edi_file_id "
        "GROUP BY 1"
    )
    for r in rows:
        print(f"  source file deleted? {r['file_deleted']}  files={r['n_files']}  claims_from_those_files={r['n_claims']}")

    # 10. Cross-check: did the SAME EDI file produce non-deleted claims too?
    print()
    print("=" * 78)
    print("10. Did the source EDI files of the deleted cohort produce ANY active claims?")
    print("=" * 78)
    rows = await c.fetch(
        DELETED_COHORT_CTE +
        "WITH source_files AS (SELECT DISTINCT edi_file_id FROM deleted_cohort) "
        "SELECT c.deleted_at IS NULL AS alive, count(*) "
        "FROM claims c WHERE c.edi_file_id IN (SELECT edi_file_id FROM source_files) "
        "GROUP BY 1 ORDER BY 1"
    )
    for r in rows:
        print(f"  same source file -> alive={r['alive']}  count={r['count']}")

    # 11. Frequency code distribution of deleted cohort (mostly originals already, but verify)
    print()
    print("=" * 78)
    print("11. Frequency code of the deleted cohort (sanity)")
    print("=" * 78)
    rows = await c.fetch(
        DELETED_COHORT_CTE +
        "SELECT COALESCE(frequency_code, '(null)') AS f, count(*) FROM deleted_cohort GROUP BY 1 ORDER BY 2 DESC"
    )
    for r in rows:
        print(f"  freq={r['f']!r:10s}  {r['count']:>4}")

    await c.close()


if __name__ == "__main__":
    asyncio.run(main())
