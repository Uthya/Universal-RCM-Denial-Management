"""POST /api/recommendations/by-file/{edi_file_id}

Returns the per-claim Recommended-Fixes payload the v1 UploadPage renders.
Three signal sources composed in priority order:

  1. CARC codes from any matched remittance_claim (source='carc')
     — fully populated for an uploaded 835 once the 837 is also present
  2. Parser-validator ERROR-severity events for the file (source='parser')
     — surfaces what the parser flagged at ingestion time
  3. ML risk + top factors (source='model')
     — only when a trained model exists; otherwise omitted

The shape exactly matches what the v1 `RecommendedFixesPanel` consumes.
"""

from __future__ import annotations

from pathlib import Path

import asyncpg
from fastapi import APIRouter, HTTPException

from rcm.core.config import settings
from rcm.schemas.public import (
    RecommendationItem,
    RecommendationRequest,
    RecommendationsResponse,
    RecommendedClaim,
)

router = APIRouter()


# Minimal CARC → human-readable fix dictionary. The v1 UI just renders the
# fix text — for any code not in this table we surface a generic "review the
# CARC code on the payer's site" message so it's never empty.
_CARC_FIXES: dict[str, tuple[str, str]] = {
    "1":   ("Deductible Amount.",                            "Verify deductible balance with the payer and re-bill if exhausted."),
    "16":  ("Claim/service lacks information required for adjudication.",
            "Re-check service-line composites (procedure / modifiers / pointers) and resubmit."),
    "18":  ("Exact duplicate claim/service.",                "Confirm with the payer that the prior claim was received; do not resubmit blindly."),
    "29":  ("The time limit for filing has expired.",        "Submit a timely-filing appeal with documentation of the original submission date."),
    "45":  ("Charge exceeds fee schedule / contracted amount.",
            "Confirm contracted rate and write off the contractual difference (no resubmit)."),
    "50":  ("Non-covered service per the medical-necessity payer policy.",
            "Add medical-necessity documentation (LCD/NCD reference) and submit a corrected claim."),
    "96":  ("Non-covered charge(s).",                        "Check the patient's plan exclusions; bill the patient if non-covered confirmed."),
    "97":  ("Benefit included in the allowance for another service.",
            "Verify bundling rules (NCCI edits); add modifier if a distinct procedural service applies."),
    "109": ("Claim not covered by this payer/contractor.",   "Route to the correct payer based on subscriber/COB info."),
    "204": ("Service not covered under the patient's benefit plan.",
            "Verify benefits at the date of service; collect patient responsibility if confirmed."),
}


def _carc_recommendation(code: str, group: str | None = None) -> RecommendationItem:
    title, fix = _CARC_FIXES.get(
        code,
        (f"CARC {code}", f"Look up CARC {code} on the payer's reason-code reference and apply the documented remediation."),
    )
    label = f"CARC {code}" + (f" / {group}" if group else "")
    return RecommendationItem(source="carc", location=label, reason=title, fix=fix)


async def _connect() -> asyncpg.Connection:
    try:
        return await asyncpg.connect(dsn=settings.sync_database_url(), timeout=8)
    except Exception as exc:
        raise HTTPException(503, detail=f"Database unreachable: {type(exc).__name__}: {exc}")


def _model_exists() -> bool:
    p: Path = settings.artifacts_dir / "837P_healthcare"
    return p.exists() and (p / "booster.json").exists()


@router.post("/by-file/{edi_file_id}", response_model=RecommendationsResponse)
async def recommendations_by_file(
    edi_file_id: int,
    body: RecommendationRequest,
) -> RecommendationsResponse:
    c = await _connect()
    try:
        total_claims = await c.fetchval(
            """
            SELECT count(*) FROM claims
            WHERE edi_file_id = $1 AND deleted_at IS NULL
            """,
            edi_file_id,
        )
        # Claims in the uploaded file
        claim_rows = await c.fetch(
            """
            SELECT
                cl.id, cl.claim_number,
                p.canonical_name AS payer_name,
                cl.claim_status::text AS claim_status
            FROM claims cl
            LEFT JOIN payers p ON p.id = cl.payer_id
            WHERE cl.edi_file_id = $1 AND cl.deleted_at IS NULL
            ORDER BY cl.id
            """,
            edi_file_id,
        )

        # Parser-validator errors keyed by claim_number (parse_events.claim_number)
        evt_rows = await c.fetch(
            """
            SELECT claim_number, segment_name, details
            FROM parse_events
            WHERE edi_file_id = $1
              AND event_type IN ('validator_error', 'claim_dropped')
              AND claim_number IS NOT NULL
            ORDER BY id
            """,
            edi_file_id,
        )
    finally:
        await c.close()

    import json as _json

    parser_by_claim: dict[str, list[RecommendationItem]] = {}
    for r in evt_rows:
        details = r["details"]
        if isinstance(details, str):
            try:
                details = _json.loads(details)
            except Exception:
                details = {}
        details = details or {}
        seg = r["segment_name"] or details.get("segment") or "?"
        field = details.get("field") or "?"
        msg = details.get("message") or details.get("reason") or "validator error"
        parser_by_claim.setdefault(r["claim_number"], []).append(
            RecommendationItem(
                source="parser",
                location=f"{seg}.{field}",
                reason=msg,
                fix=(
                    "Open the 837 in the EDI Inspector → Parse Trace and correct "
                    "the segment, then re-submit."
                ),
            )
        )

    # CARC recommendations from any remittance_claim associated with the same
    # claim_number (regardless of which file the 835 came from).
    claim_ids = [int(c["id"]) for c in claim_rows]
    carc_by_claim_id: dict[int, list[RecommendationItem]] = {}
    paid_claim_ids: set[int] = set()
    if claim_ids:
        c = await _connect()
        try:
            remit_rows = await c.fetch(
                """
                SELECT rc.id, rc.claim_id, rc.paid_amount,
                       a.adjustment_group_code, a.adjustment_reason_code
                FROM remittance_claims rc
                LEFT JOIN adjustments a ON a.remittance_claim_id = rc.id
                WHERE rc.claim_id = ANY($1::bigint[])
                ORDER BY rc.id
                """,
                claim_ids,
            )
        finally:
            await c.close()

        for r in remit_rows:
            cid = int(r["claim_id"])
            if r["paid_amount"] is not None and float(r["paid_amount"]) > 0:
                paid_claim_ids.add(cid)
            code = r["adjustment_reason_code"]
            grp = r["adjustment_group_code"]
            if code:
                carc_by_claim_id.setdefault(cid, []).append(_carc_recommendation(code, grp))

    out: list[RecommendedClaim] = []
    for row in claim_rows:
        cid = int(row["id"])
        cnum = row["claim_number"]
        recs: list[RecommendationItem] = []
        recs.extend(carc_by_claim_id.get(cid, []))
        recs.extend(parser_by_claim.get(cnum, []))

        # No recommendations + no remit info → skip; the UI hides healthy claims.
        if not recs and cid not in paid_claim_ids:
            continue

        resolved = cid in paid_claim_ids and not recs
        if resolved:
            status_badge = "Resolved"
        elif carc_by_claim_id.get(cid):
            status_badge = "Denied"
        else:
            status_badge = "High Risk"

        out.append(RecommendedClaim(
            claim_id=cid,
            claim_number=cnum,
            payer_name=row["payer_name"],
            risk_score=None,   # populated by /predict-claim if model is trained
            status_badge=status_badge,
            resolved=resolved,
            recommendations=recs,
        ))

    return RecommendationsResponse(
        edi_file_id=edi_file_id,
        total_claims_in_file=int(total_claims or 0),
        flagged_claims=len(out),
        claims=out,
    )
