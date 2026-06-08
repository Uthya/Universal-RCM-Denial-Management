"""Claims Browser endpoints — Page 3 of the dev console.

Per CR-041:
    * Backend computes every count and joins (line_count, diagnosis_count,
      has_remittance). The frontend table renders only.
    * Read-only — claims_browser does not write to any table.
    * Empty-state safe — every list returns an empty Page when no rows match.

Endpoints (one per drill-in tab; the page hits whichever endpoint the user
opened so we don't pay for unread tabs):
    GET /api/dev/claims                     filterable + paginated list
    GET /api/dev/claims/{id}                overview tab (single-claim metadata)
    GET /api/dev/claims/{id}/lines          claim_lines for one claim
    GET /api/dev/claims/{id}/diagnoses      diagnoses for one claim
    GET /api/dev/claims/{id}/remits         remittance_claims (+ adjustment count) for one claim

Parse-events and raw-segments tabs reuse /api/dev/edi/files/{file_id}/{events,segments}
with the parent file_id (returned in the claim detail payload).
"""

from __future__ import annotations

import json

import asyncpg
from fastapi import APIRouter, HTTPException, Query

from rcm.core.config import settings
from rcm.schemas.dev import (
    ClaimDetailResponse,
    ClaimLineItem,
    ClaimLinesListResponse,
    ClaimsListItem,
    ClaimsListResponse,
    DiagnosesListResponse,
    DiagnosisItem,
    RemittanceClaimItem,
    RemittanceClaimsListResponse,
)

router = APIRouter()


async def _connect() -> asyncpg.Connection:
    try:
        return await asyncpg.connect(dsn=settings.sync_database_url(), timeout=8)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database unreachable: {type(exc).__name__}: {exc}",
        )


# ---------------------------------------------------------------------------
# GET /claims
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=ClaimsListResponse,
    summary="Paginated claims list with optional filters",
    description=(
        "Returns claims (newest first) with filters: service_variant, "
        "claim_subtype, payer_id, claim_status, and a case-insensitive "
        "substring match on claim_number. Each row includes denormalized "
        "payer_name + counts (lines, diagnoses, has_remittance) computed in "
        "SQL so the table needs no client-side joins."
    ),
)
async def claims_list(
    variant: str | None = Query(None, description="service_variant filter (837P / 837I / 837D)"),
    subtype: str | None = Query(None, description="claim_subtype filter"),
    payer_id: int | None = Query(None),
    claim_status: str | None = Query(None, description="claim_status enum filter"),
    q: str | None = Query(None, description="Substring match on claim_number"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ClaimsListResponse:
    where = ["c.deleted_at IS NULL"]
    params: list = []

    if variant:
        params.append(variant)
        where.append(f"c.service_variant = ${len(params)}")
    if subtype:
        params.append(subtype)
        where.append(f"c.claim_subtype = ${len(params)}")
    if payer_id is not None:
        params.append(payer_id)
        where.append(f"c.payer_id = ${len(params)}")
    if claim_status:
        params.append(claim_status)
        where.append(f"c.claim_status::text = ${len(params)}")
    if q:
        params.append(f"%{q}%")
        where.append(f"c.claim_number ILIKE ${len(params)}")

    where_sql = " AND ".join(where)
    c = await _connect()
    try:
        total = await c.fetchval(
            f"SELECT count(*) FROM claims c WHERE {where_sql}", *params,
        )
        params_with_paging = [*params, limit, offset]
        rows = await c.fetch(
            f"""
            SELECT
                c.id, c.claim_number, c.service_variant, c.claim_subtype,
                c.claim_status::text AS claim_status,
                c.total_charge_amount,
                to_char(c.service_from_date, 'YYYY-MM-DD') AS service_from_date,
                to_char(c.submission_date,    'YYYY-MM-DD') AS submission_date,
                c.payer_id, p.canonical_name AS payer_name,
                c.patient_id, c.edi_file_id,
                (SELECT count(*) FROM claim_lines  WHERE claim_id = c.id) AS line_count,
                (SELECT count(*) FROM diagnoses    WHERE claim_id = c.id) AS diagnosis_count,
                EXISTS (SELECT 1 FROM remittance_claims WHERE claim_id = c.id) AS has_remittance,
                to_char(c.created_at, 'YYYY-MM-DD"T"HH24:MI:SS') AS created_at
            FROM claims c
            LEFT JOIN payers p ON p.id = c.payer_id
            WHERE {where_sql}
            ORDER BY c.id DESC
            LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
            """,
            *params_with_paging,
        )
    finally:
        await c.close()

    items = [
        ClaimsListItem(
            id=int(r["id"]),
            claim_number=r["claim_number"],
            service_variant=r["service_variant"],
            claim_subtype=r["claim_subtype"],
            claim_status=r["claim_status"],
            total_charge_amount=float(r["total_charge_amount"]),
            service_from_date=r["service_from_date"],
            submission_date=r["submission_date"],
            payer_id=r["payer_id"],
            payer_name=r["payer_name"],
            patient_id=r["patient_id"],
            edi_file_id=int(r["edi_file_id"]),
            line_count=int(r["line_count"]),
            diagnosis_count=int(r["diagnosis_count"]),
            has_remittance=bool(r["has_remittance"]),
            created_at=r["created_at"],
        )
        for r in rows
    ]
    return ClaimsListResponse(items=items, total=int(total or 0), limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# GET /claims/{id}
# ---------------------------------------------------------------------------

@router.get(
    "/{claim_id}",
    response_model=ClaimDetailResponse,
    summary="Single-claim overview (tab 1 of detail page)",
    description=(
        "Returns the full claims row plus denormalized payer/patient/provider "
        "identifiers and counts that drive the other tabs' badges (line_count, "
        "diagnosis_count, remittance_count, raw_segment_count, parse_event_count)."
    ),
    responses={404: {"description": "Claim not found or soft-deleted"}},
)
async def claim_detail(claim_id: int) -> ClaimDetailResponse:
    c = await _connect()
    try:
        row = await c.fetchrow(
            """
            SELECT
                cl.id, cl.edi_file_id, cl.claim_number,
                cl.service_variant, cl.claim_subtype,
                cl.claim_status::text AS claim_status,
                cl.total_charge_amount,
                cl.facility_type_code, cl.frequency_code,
                to_char(cl.service_from_date, 'YYYY-MM-DD') AS service_from_date,
                to_char(cl.service_to_date,   'YYYY-MM-DD') AS service_to_date,
                to_char(cl.submission_date,   'YYYY-MM-DD') AS submission_date,

                cl.payer_id, py.canonical_name AS payer_name,
                cl.patient_id, pt.member_id AS patient_member_id,
                cl.subscriber_id,
                cl.billing_provider_id,
                bp.npi AS billing_provider_npi,
                cl.rendering_provider_id,
                rp.npi AS rendering_provider_npi,
                cl.referring_provider_id,

                cl.authorization_number, cl.referral_number,
                cl.previous_payer_claim_control_no,
                cl.variant_data, cl.raw_claim_segment,
                to_char(cl.created_at, 'YYYY-MM-DD"T"HH24:MI:SS') AS created_at
            FROM claims cl
            LEFT JOIN payers py    ON py.id = cl.payer_id
            LEFT JOIN patients pt  ON pt.id = cl.patient_id
            LEFT JOIN providers bp ON bp.id = cl.billing_provider_id
            LEFT JOIN providers rp ON rp.id = cl.rendering_provider_id
            WHERE cl.id = $1 AND cl.deleted_at IS NULL
            """,
            claim_id,
        )
        if row is None:
            raise HTTPException(404, detail=f"claim id={claim_id} not found")

        line_count = await c.fetchval(
            "SELECT count(*) FROM claim_lines WHERE claim_id = $1", claim_id,
        )
        diag_count = await c.fetchval(
            "SELECT count(*) FROM diagnoses WHERE claim_id = $1", claim_id,
        )
        remit_count = await c.fetchval(
            "SELECT count(*) FROM remittance_claims WHERE claim_id = $1", claim_id,
        )
        raw_seg_count = await c.fetchval(
            "SELECT count(*) FROM raw_segments WHERE claim_id = $1", claim_id,
        )
        parse_evt_count = await c.fetchval(
            """SELECT count(*) FROM parse_events
               WHERE edi_file_id = $1 AND claim_number = $2""",
            int(row["edi_file_id"]), row["claim_number"],
        )
    finally:
        await c.close()

    variant_data = row["variant_data"]
    if isinstance(variant_data, str):
        variant_data = json.loads(variant_data)

    return ClaimDetailResponse(
        id=int(row["id"]),
        edi_file_id=int(row["edi_file_id"]),
        claim_number=row["claim_number"],
        service_variant=row["service_variant"],
        claim_subtype=row["claim_subtype"],
        claim_status=row["claim_status"],
        total_charge_amount=float(row["total_charge_amount"]),
        facility_type_code=row["facility_type_code"],
        frequency_code=row["frequency_code"],
        service_from_date=row["service_from_date"],
        service_to_date=row["service_to_date"],
        submission_date=row["submission_date"],

        payer_id=row["payer_id"],
        payer_name=row["payer_name"],
        patient_id=row["patient_id"],
        patient_member_id=row["patient_member_id"],
        subscriber_id=row["subscriber_id"],
        billing_provider_id=row["billing_provider_id"],
        billing_provider_npi=row["billing_provider_npi"],
        rendering_provider_id=row["rendering_provider_id"],
        rendering_provider_npi=row["rendering_provider_npi"],
        referring_provider_id=row["referring_provider_id"],

        authorization_number=row["authorization_number"],
        referral_number=row["referral_number"],
        previous_payer_claim_control_no=row["previous_payer_claim_control_no"],
        variant_data=variant_data,
        raw_claim_segment=row["raw_claim_segment"],

        line_count=int(line_count or 0),
        diagnosis_count=int(diag_count or 0),
        remittance_count=int(remit_count or 0),
        raw_segment_count=int(raw_seg_count or 0),
        parse_event_count=int(parse_evt_count or 0),

        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# GET /claims/{id}/lines
# ---------------------------------------------------------------------------

@router.get(
    "/{claim_id}/lines",
    response_model=ClaimLinesListResponse,
    summary="claim_lines for one claim",
)
async def claim_lines(claim_id: int) -> ClaimLinesListResponse:
    c = await _connect()
    try:
        rows = await c.fetch(
            """
            SELECT
                id, line_number,
                procedure_code, modifier1, modifier2, modifier3, modifier4,
                billed_amount, units, units_basis,
                place_of_service,
                to_char(service_date, 'YYYY-MM-DD') AS service_date,
                diagnosis_pointers,
                revenue_code, hipps_code,
                tooth_number, tooth_surfaces,
                ndc_drug_code,
                raw_sv_segment
            FROM claim_lines
            WHERE claim_id = $1
            ORDER BY line_number
            """,
            claim_id,
        )
    finally:
        await c.close()

    items = [
        ClaimLineItem(
            id=int(r["id"]),
            line_number=int(r["line_number"]),
            procedure_code=r["procedure_code"],
            modifier1=r["modifier1"],
            modifier2=r["modifier2"],
            modifier3=r["modifier3"],
            modifier4=r["modifier4"],
            billed_amount=float(r["billed_amount"]) if r["billed_amount"] is not None else None,
            units=float(r["units"]) if r["units"] is not None else None,
            units_basis=r["units_basis"],
            place_of_service=r["place_of_service"],
            service_date=r["service_date"],
            diagnosis_pointers=list(r["diagnosis_pointers"]) if r["diagnosis_pointers"] else None,
            revenue_code=r["revenue_code"],
            hipps_code=r["hipps_code"],
            tooth_number=r["tooth_number"],
            tooth_surfaces=r["tooth_surfaces"],
            ndc_drug_code=r["ndc_drug_code"],
            raw_sv_segment=r["raw_sv_segment"],
        )
        for r in rows
    ]
    return ClaimLinesListResponse(items=items, total=len(items))


# ---------------------------------------------------------------------------
# GET /claims/{id}/diagnoses
# ---------------------------------------------------------------------------

@router.get(
    "/{claim_id}/diagnoses",
    response_model=DiagnosesListResponse,
    summary="Diagnoses for one claim",
)
async def claim_diagnoses(claim_id: int) -> DiagnosesListResponse:
    c = await _connect()
    try:
        rows = await c.fetch(
            """
            SELECT id, sequence_number, diagnosis_code, diagnosis_type,
                   diagnosis_qualifier, present_on_admission
            FROM diagnoses
            WHERE claim_id = $1
            ORDER BY sequence_number
            """,
            claim_id,
        )
    finally:
        await c.close()

    items = [
        DiagnosisItem(
            id=int(r["id"]),
            sequence_number=int(r["sequence_number"]),
            diagnosis_code=r["diagnosis_code"],
            diagnosis_type=r["diagnosis_type"],
            diagnosis_qualifier=r["diagnosis_qualifier"],
            present_on_admission=r["present_on_admission"],
        )
        for r in rows
    ]
    return DiagnosesListResponse(items=items, total=len(items))


# ---------------------------------------------------------------------------
# GET /claims/{id}/remits
# ---------------------------------------------------------------------------

@router.get(
    "/{claim_id}/remits",
    response_model=RemittanceClaimsListResponse,
    summary="Remittance claims (835 responses) for one claim",
)
async def claim_remits(claim_id: int) -> RemittanceClaimsListResponse:
    c = await _connect()
    try:
        rows = await c.fetch(
            """
            SELECT
                rc.id, rc.claim_status_code, rc.billed_amount, rc.paid_amount,
                rc.patient_responsibility_amount, rc.payer_claim_control_number,
                to_char(rc.remittance_date, 'YYYY-MM-DD') AS remittance_date,
                to_char(rc.payer_paid_date, 'YYYY-MM-DD') AS payer_paid_date,
                rc.edi_file_id,
                (SELECT count(*) FROM adjustments
                 WHERE remittance_claim_id = rc.id) AS adjustment_count,
                rc.raw_clp_segment
            FROM remittance_claims rc
            WHERE rc.claim_id = $1
            ORDER BY rc.id
            """,
            claim_id,
        )
    finally:
        await c.close()

    items = [
        RemittanceClaimItem(
            id=int(r["id"]),
            claim_status_code=r["claim_status_code"],
            billed_amount=float(r["billed_amount"]),
            paid_amount=float(r["paid_amount"]),
            patient_responsibility_amount=(
                float(r["patient_responsibility_amount"])
                if r["patient_responsibility_amount"] is not None else None
            ),
            payer_claim_control_number=r["payer_claim_control_number"],
            remittance_date=r["remittance_date"],
            payer_paid_date=r["payer_paid_date"],
            edi_file_id=r["edi_file_id"],
            adjustment_count=int(r["adjustment_count"]),
            raw_clp_segment=r["raw_clp_segment"],
        )
        for r in rows
    ]
    return RemittanceClaimsListResponse(items=items, total=len(items))
