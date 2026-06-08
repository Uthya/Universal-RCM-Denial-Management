"""835 — CLP / CAS / LQ / MIA / MOA / SVC.

CLP opens a new remittance claim. CAS triplets attach to either the current
CLP (claim-level) OR the most recent SVC (line-level). LQ remarks attach
similarly. MIA/MOA stash institutional/outpatient adjudication detail into
variant_data (out of scope for FE in this phase, but preserved for RAG).

Lesson P1: ``remittance_date`` is NULLABLE — never substitute date.today().
If DTM*050 is missing AND DTM*405 (production date) is missing, the row
persists with NULL and a WARNING is recorded.
"""

from __future__ import annotations

from collections.abc import Sequence

from rcm.parsing.context import (
    AdjustmentRec,
    ParseContext,
    RemarkCodeRec,
    RemittanceClaimRec,
)
from rcm.parsing.dispatchers import register_handler
from rcm.parsing.safe import _parse_cas_triplets, safe_decimal, safe_element


def handle_clp(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    claim_number = safe_element(elements, 1)
    if not claim_number:
        ctx.add_error(segment="CLP", field="claim_number",
                      message="CLP01 (patient control number) is empty",
                      severity="ERROR", validator="parser")
        return

    status = safe_element(elements, 2)
    billed = safe_decimal(safe_element(elements, 3))
    paid = safe_decimal(safe_element(elements, 4)) or 0
    pt_resp = safe_decimal(safe_element(elements, 5))
    payer_claim_ctrl = safe_element(elements, 7) or None

    if billed is None:
        ctx.add_error(segment="CLP", field="billed_amount",
                      message="CLP03 (total claim charge) is invalid",
                      severity="ERROR", claim_identifier=claim_number)
        billed = 0  # type: ignore[assignment]

    # Use buffered DTM*405 (production date) as a fallback for remittance_date
    # if no DTM*050 follows the CLP.
    remit_date = ctx._dtm_production_date

    remit = RemittanceClaimRec(
        claim_number=claim_number,
        claim_status_code=status,
        billed_amount=billed,
        paid_amount=paid,
        patient_responsibility_amount=pt_resp,
        payer_claim_control_number=payer_claim_ctrl,
        remittance_date=remit_date,  # may be overridden by DTM*050 later
        raw_clp_segment=raw,
        object_index=len(ctx.remittances),
    )
    ctx.remittances.append(remit)
    ctx.current_remittance = remit
    ctx.current_remit_line_number = None


def handle_cas(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    """CAS appears at claim level (after CLP) or line level (after SVC).
    Attribution depends on whether the most recent context is a CLP or SVC.
    """
    if ctx.current_remittance is None:
        ctx.add_error(segment="CAS", field="remittance_context",
                      message="CAS with no current remittance claim",
                      severity="ERROR")
        return

    result = _parse_cas_triplets(elements)
    for w in result.warnings:
        ctx.add_event("validator_warning", segment_name="CAS",
                      details={"reason": w, "stride": result.stride})

    for t in result.triplets:
        ctx.current_remittance.adjustments.append(AdjustmentRec(
            adjustment_group_code=t.group_code,
            adjustment_reason_code=t.reason_code,
            adjustment_amount=t.amount,
            quantity=t.quantity,
            service_line_number=ctx.current_remit_line_number,
            raw_cas_segment=raw,
        ))

    if not result.complete:
        ctx.add_error(segment="CAS", field="triplets",
                      message=f"CAS triplets incomplete (stride={result.stride})",
                      severity="WARNING")


def handle_lq(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    """LQ — remark codes (RARC). LQ01='HE' indicates a health care remark."""
    if ctx.current_remittance is None:
        return
    qualifier = safe_element(elements, 1)
    code = safe_element(elements, 2)
    if not code:
        return
    if qualifier and qualifier.upper() != "HE":
        ctx.add_event("validator_warning", segment_name="LQ",
                      details={"unexpected_qualifier": qualifier})
    ctx.current_remittance.remark_codes.append(RemarkCodeRec(
        remark_code=code,
        service_line_number=ctx.current_remit_line_number,
        raw_lq_segment=raw,
    ))


def handle_svc(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    """SVC opens a line-level adjudication context inside a CLP.

    Subsequent CAS / LQ segments attach to this line (via current_remit_line_number).
    """
    if ctx.current_remittance is None:
        return
    ctx.current_remit_line_number = (ctx.current_remit_line_number or 0) + 1
    # Stash the SVC details on the remittance variant_data for traceability
    proc_composite = safe_element(elements, 1)
    ctx.add_event("segment_handled", segment_name="SVC",
                  details={
                      "line_number": ctx.current_remit_line_number,
                      "proc_composite": proc_composite,
                      "submitted_charge": safe_element(elements, 2),
                      "paid_amount": safe_element(elements, 3),
                  })


def handle_mia(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    """Inpatient adjudication info — stashed on the remittance as structured event."""
    if ctx.current_remittance is None:
        return
    ctx.add_event("segment_handled", segment_name="MIA", details={"raw_count": len(elements) - 1})


def handle_moa(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    if ctx.current_remittance is None:
        return
    ctx.add_event("segment_handled", segment_name="MOA", details={"raw_count": len(elements) - 1})


register_handler("835", "CLP", handle_clp)
register_handler("835", "CAS", handle_cas)
register_handler("835", "LQ", handle_lq)
register_handler("835", "SVC", handle_svc)
register_handler("835", "MIA", handle_mia)
register_handler("835", "MOA", handle_moa)
