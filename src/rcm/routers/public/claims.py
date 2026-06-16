"""GET /api/claims/  +  GET /api/claims/{id}

The list endpoint mirrors what the v1 ClaimsPage expects (skip/limit/status/
sort_by/sort_dir). The detail endpoint inlines claim_lines / diagnoses /
remittance_claims (with adjustments + remark_codes) so ClaimDetailPage can
render in one call.
"""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, HTTPException, Query

from rcm.core.config import settings
from rcm.schemas.public import (
    AdjustmentItem,
    ClaimDetailResponse,
    ClaimLineItem,
    ClaimListItem,
    ClaimsListResponse,
    DiagnosisItem,
    RemarkCodeItem,
    RemittanceClaimItem,
)

router = APIRouter()


async def _connect() -> asyncpg.Connection:
    try:
        return await asyncpg.connect(dsn=settings.sync_database_url(), timeout=8)
    except Exception as exc:
        raise HTTPException(503, detail=f"Database unreachable: {type(exc).__name__}: {exc}")


_SORT_WHITELIST = {
    "claim_number": "c.claim_number",
    "payer_name": "p.canonical_name",
    "total_charge_amount": "c.total_charge_amount",
    "claim_status": "c.claim_status",
    "service_from_date": "c.service_from_date",
    "created_at": "c.created_at",
}


@router.get("/", response_model=ClaimsListResponse)
async def list_claims(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    status: str | None = Query(None),
    sort_by: str = Query("created_at"),
    sort_dir: str = Query("desc"),
) -> ClaimsListResponse:
    sort_col = _SORT_WHITELIST.get(sort_by, "c.created_at")
    sort_dir_sql = "ASC" if sort_dir.lower() == "asc" else "DESC"

    where = ["c.deleted_at IS NULL"]
    params: list = []
    if status:
        params.append(status)
        where.append(f"c.claim_status::text = ${len(params)}")
    where_sql = " AND ".join(where)

    c = await _connect()
    try:
        total = await c.fetchval(
            f"SELECT count(*) FROM claims c WHERE {where_sql}", *params,
        )
        params_paged = [*params, limit, skip]
        rows = await c.fetch(
            f"""
            SELECT
                c.id, c.claim_number,
                p.canonical_name AS payer_name,
                c.total_charge_amount,
                c.claim_status::text AS claim_status,
                to_char(c.service_from_date, 'YYYY-MM-DD') AS service_from_date,
                to_char(c.created_at, 'YYYY-MM-DD"T"HH24:MI:SS') AS created_at,
                pt.member_id AS patient_member_id
            FROM claims c
            LEFT JOIN payers p   ON p.id  = c.payer_id
            LEFT JOIN patients pt ON pt.id = c.patient_id
            WHERE {where_sql}
            ORDER BY {sort_col} {sort_dir_sql} NULLS LAST, c.id DESC
            LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
            """,
            *params_paged,
        )
    finally:
        await c.close()

    items = [
        ClaimListItem(
            id=int(r["id"]),
            claim_number=r["claim_number"],
            payer_name=r["payer_name"],
            total_charge_amount=float(r["total_charge_amount"]),
            claim_status=r["claim_status"],
            service_from_date=r["service_from_date"],
            created_at=r["created_at"],
            patient_member_id=r["patient_member_id"],
        )
        for r in rows
    ]
    return ClaimsListResponse(items=items, total=int(total or 0))


@router.get("/{claim_id}", response_model=ClaimDetailResponse)
async def claim_detail(claim_id: int) -> ClaimDetailResponse:
    c = await _connect()
    try:
        row = await c.fetchrow(
            """
            SELECT
                cl.id, cl.claim_number,
                p.canonical_name AS payer_name,
                pt.member_id AS patient_member_id,
                cl.total_charge_amount,
                cl.claim_status::text AS claim_status,
                to_char(cl.service_from_date, 'YYYY-MM-DD') AS service_from_date,
                to_char(cl.service_to_date,   'YYYY-MM-DD') AS service_to_date,
                cl.facility_type_code,
                cl.previous_payer_claim_control_no
            FROM claims cl
            LEFT JOIN payers p   ON p.id  = cl.payer_id
            LEFT JOIN patients pt ON pt.id = cl.patient_id
            WHERE cl.id = $1 AND cl.deleted_at IS NULL
            """,
            claim_id,
        )
        if row is None:
            raise HTTPException(404, detail=f"Claim {claim_id} not found")

        line_rows = await c.fetch(
            """
            SELECT id, line_number, procedure_code, modifier1, modifier2,
                   billed_amount, units,
                   to_char(service_date, 'YYYY-MM-DD') AS service_date,
                   place_of_service
            FROM claim_lines WHERE claim_id = $1 ORDER BY line_number
            """,
            claim_id,
        )
        diag_rows = await c.fetch(
            """
            SELECT id, sequence_number, diagnosis_code, diagnosis_type
            FROM diagnoses WHERE claim_id = $1 ORDER BY sequence_number
            """,
            claim_id,
        )
        remit_rows = await c.fetch(
            """
            SELECT id, claim_status_code, billed_amount, paid_amount,
                   to_char(remittance_date, 'YYYY-MM-DD') AS remittance_date,
                   payer_claim_control_number
            FROM remittance_claims WHERE claim_id = $1 ORDER BY id
            """,
            claim_id,
        )

        remits: list[RemittanceClaimItem] = []
        for rr in remit_rows:
            adj_rows = await c.fetch(
                """
                SELECT id, adjustment_group_code, adjustment_reason_code, adjustment_amount
                FROM adjustments WHERE remittance_claim_id = $1 ORDER BY id
                """,
                int(rr["id"]),
            )
            rmk_rows = await c.fetch(
                """
                SELECT id, remark_code FROM remark_codes
                WHERE remittance_claim_id = $1 ORDER BY id
                """,
                int(rr["id"]),
            )
            remits.append(RemittanceClaimItem(
                id=int(rr["id"]),
                claim_status_code=rr["claim_status_code"],
                billed_amount=float(rr["billed_amount"]),
                paid_amount=float(rr["paid_amount"]),
                remittance_date=rr["remittance_date"],
                payer_claim_control_number=rr["payer_claim_control_number"],
                adjustments=[AdjustmentItem(
                    id=int(a["id"]),
                    adjustment_group_code=a["adjustment_group_code"],
                    adjustment_reason_code=a["adjustment_reason_code"],
                    adjustment_amount=float(a["adjustment_amount"]),
                ) for a in adj_rows],
                remark_codes=[RemarkCodeItem(
                    id=int(rm["id"]), remark_code=rm["remark_code"],
                ) for rm in rmk_rows],
            ))
    finally:
        await c.close()

    return ClaimDetailResponse(
        id=int(row["id"]),
        claim_number=row["claim_number"],
        payer_name=row["payer_name"],
        patient_member_id=row["patient_member_id"],
        total_charge_amount=float(row["total_charge_amount"]),
        claim_status=row["claim_status"],
        service_from_date=row["service_from_date"],
        service_to_date=row["service_to_date"],
        facility_type_code=row["facility_type_code"],
        previous_payer_claim_control_no=row["previous_payer_claim_control_no"],
        claim_lines=[ClaimLineItem(
            id=int(l["id"]), line_number=int(l["line_number"]),
            procedure_code=l["procedure_code"], modifier1=l["modifier1"],
            modifier2=l["modifier2"],
            billed_amount=float(l["billed_amount"]) if l["billed_amount"] is not None else None,
            units=float(l["units"]) if l["units"] is not None else None,
            service_date=l["service_date"], place_of_service=l["place_of_service"],
        ) for l in line_rows],
        diagnoses=[DiagnosisItem(
            id=int(d["id"]), sequence_number=int(d["sequence_number"]),
            diagnosis_code=d["diagnosis_code"], diagnosis_type=d["diagnosis_type"],
        ) for d in diag_rows],
        remittance_claims=remits,
    )
