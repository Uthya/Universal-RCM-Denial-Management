"""Part 2 of the mv_patient_claim_history investigation — sections 4-9 of
the original script, with the FILTER-on-window-function syntax fixed.
Read-only.
"""
from __future__ import annotations

import asyncio

import asyncpg

DSN = "postgresql://postgres:P0stgreSQL%21Dev%23847@104.130.220.20:30432/rcm_denials"


async def main() -> None:
    c = await asyncpg.connect(dsn=DSN, timeout=20)

    print("=" * 78)
    print("4. patient_seq impact — sample mixed patient (62, active=8, deleted=9)")
    print("=" * 78)
    detail = await c.fetch(
        """
        WITH ordered AS (
          SELECT id, service_from_date, deleted_at IS NOT NULL AS is_deleted,
                 row_number() OVER (PARTITION BY patient_id ORDER BY service_from_date, id) AS seq_with_deleted
          FROM claims
          WHERE patient_id = 62 AND service_from_date IS NOT NULL
        ),
        active_only AS (
          SELECT id,
                 row_number() OVER (PARTITION BY 1 ORDER BY service_from_date, id) AS seq_without_deleted
          FROM claims
          WHERE patient_id = 62 AND service_from_date IS NOT NULL AND deleted_at IS NULL
        )
        SELECT o.id, o.service_from_date, o.is_deleted, o.seq_with_deleted, a.seq_without_deleted
        FROM ordered o LEFT JOIN active_only a USING (id)
        ORDER BY o.service_from_date, o.id
        """
    )
    for d in detail:
        marker = "D" if d["is_deleted"] else " "
        sn = "—" if d["seq_without_deleted"] is None else str(d["seq_without_deleted"])
        print(f"  claim {d['id']:>5}  {d['service_from_date']}  [{marker}]  "
              f"seq_with_del={d['seq_with_deleted']}  seq_without_del={sn}")

    print()
    print("=" * 78)
    print("5. Aggregate inflation of Cat-G inputs across mv_pch eligible cohort")
    print("=" * 78)
    r = await c.fetchrow(
        """
        SELECT
          (SELECT count(*) FROM claims WHERE patient_id IS NOT NULL AND service_from_date IS NOT NULL) AS rows_with_del,
          (SELECT count(*) FROM claims WHERE patient_id IS NOT NULL AND service_from_date IS NOT NULL
                                       AND deleted_at IS NULL) AS rows_without_del,
          (SELECT sum(total_charge_amount) FROM claims
            WHERE patient_id IS NOT NULL AND service_from_date IS NOT NULL) AS chg_with_del,
          (SELECT sum(total_charge_amount) FROM claims
            WHERE patient_id IS NOT NULL AND service_from_date IS NOT NULL AND deleted_at IS NULL) AS chg_without_del
        """
    )
    drows = r["rows_with_del"] - r["rows_without_del"]
    dchg = float(r["chg_with_del"]) - float(r["chg_without_del"])
    print(f"  rows:    with_del={r['rows_with_del']}  without_del={r['rows_without_del']}  "
          f"inflation={drows} ({100.0*drows/max(r['rows_without_del'],1):.2f}%)")
    print(f"  charge $: with_del={float(r['chg_with_del']):>14.2f}  without_del={float(r['chg_without_del']):>14.2f}  "
          f"inflation=${dchg:.2f} ({100.0*dchg/max(float(r['chg_without_del']),1.0):.2f}%)")

    print()
    print("=" * 78)
    print("6. Most-affected patients (top 10 by deleted claim count)")
    print("=" * 78)
    rows = await c.fetch(
        """
        WITH per_patient AS (
          SELECT c.patient_id,
                 count(*) FILTER (WHERE c.deleted_at IS NULL)     AS n_active,
                 count(*) FILTER (WHERE c.deleted_at IS NOT NULL) AS n_deleted,
                 sum(c.total_charge_amount) FILTER (WHERE c.deleted_at IS NOT NULL) AS deleted_chg,
                 sum(c.total_charge_amount) AS total_chg
          FROM claims c
          WHERE c.patient_id IS NOT NULL AND c.service_from_date IS NOT NULL
          GROUP BY c.patient_id
        )
        SELECT * FROM per_patient WHERE n_deleted > 0
        ORDER BY n_deleted DESC, n_active DESC
        LIMIT 10
        """
    )
    print(f"  {'patient_id':>10}  {'active':>6}  {'deleted':>7}  {'del_chg':>10}  {'total_chg':>10}  {'shift%':>7}")
    for r in rows:
        shift = 100.0 * float(r["deleted_chg"] or 0) / max(float(r["total_chg"] or 1), 1.0)
        print(f"  {r['patient_id']:>10}  {r['n_active']:>6}  {r['n_deleted']:>7}  "
              f"{float(r['deleted_chg'] or 0):>10.2f}  {float(r['total_chg'] or 0):>10.2f}  {shift:>6.2f}%")

    print()
    print("=" * 78)
    print("7. Created / deleted timing of the mv_pch deleted cohort")
    print("=" * 78)
    r = await c.fetchrow(
        """
        SELECT min(created_at) AS first_c, max(created_at) AS last_c,
               min(deleted_at) AS first_d, max(deleted_at) AS last_d
        FROM claims
        WHERE deleted_at IS NOT NULL AND patient_id IS NOT NULL AND service_from_date IS NOT NULL
        """
    )
    print(f"  created span: {r['first_c']} -> {r['last_c']}")
    print(f"  deleted span: {r['first_d']} -> {r['last_d']}")

    print()
    print("=" * 78)
    print("8. Overlap with the mv_claim_labels 260-cohort")
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
                          AND (rc.claim_status_code = '4'
                               OR rc.claim_status_code IN ('1','2','3','19','20')))
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
          (SELECT count(*) FROM (SELECT id FROM mv_cl_deleted INTERSECT SELECT id FROM mv_pch_deleted) s) AS in_both,
          (SELECT count(*) FROM mv_pch_deleted WHERE id NOT IN (SELECT id FROM mv_cl_deleted)) AS pch_only
        """
    )
    print(f"  in mv_claim_labels-deleted:       {r['in_cl']}")
    print(f"  in mv_pch-deleted:                {r['in_pch']}")
    print(f"  in BOTH:                           {r['in_both']}")
    print(f"  in mv_pch only (new cohort):      {r['pch_only']}")

    print()
    print("=" * 78)
    print("9. Frequency-code distribution of the mv_pch-only deleted rows")
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
                          AND (rc.claim_status_code = '4'
                               OR rc.claim_status_code IN ('1','2','3','19','20')))
        )
        SELECT COALESCE(c.frequency_code, '(null)') AS f,
               count(*) AS n,
               count(*) FILTER (WHERE EXISTS (SELECT 1 FROM remittance_claims rc
                                              WHERE rc.claim_id = c.id)) AS with_remit
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
