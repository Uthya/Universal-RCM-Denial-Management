"""TOO / DN1 / DN2 — dental tooth + orthodontic info.

TOO attaches a tooth number + surface composite to the most recent service
line. DN1 carries orthodontic treatment counts (total/remaining months);
DN2 carries tooth status (missing/extracted).
"""

from __future__ import annotations

from collections.abc import Sequence

from rcm.parsing.context import ClaimCertRec, ParseContext
from rcm.parsing.dispatchers import register_handler
from rcm.parsing.safe import composite_at, safe_element, safe_int, split_composite


def handle_too(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    if ctx.current_claim is None or not ctx.current_claim.lines:
        return
    line = ctx.current_claim.lines[-1]
    # TOO01 qualifier (JP/JO) ignored — we trust the tooth number value
    line.tooth_number = safe_element(elements, 2) or None
    # TOO03 composite of up to 5 surface codes (M/D/B/L/O/I)
    surfaces_composite = safe_element(elements, 3)
    if surfaces_composite:
        parts = split_composite(surfaces_composite, ctx.delimiters.component)
        # Concatenate into a single string for the column (e.g. "MOD")
        line.tooth_surfaces = "".join(composite_at(parts, i) for i in range(len(parts)))


def handle_dn1(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    if ctx.current_claim is None:
        return
    total = safe_int(safe_element(elements, 1))
    remaining = safe_int(safe_element(elements, 2))
    treatment_code = safe_element(elements, 3)
    ctx.current_claim.certifications.append(ClaimCertRec(
        certification_type="orthodontic",
        raw_segment=raw,
        structured_data={
            "total_months_of_treatment": total,
            "remaining_months_of_treatment": remaining,
            "treatment_code": treatment_code,
        },
    ))


def handle_dn2(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    if ctx.current_claim is None:
        return
    status_code = safe_element(elements, 1)
    tooth_number = safe_element(elements, 2)
    ctx.current_claim.variant_data.setdefault("tooth_status", []).append({
        "code": status_code,
        "tooth_number": tooth_number,
    })


register_handler("837D", "TOO", handle_too)
register_handler("837D", "DN1", handle_dn1)
register_handler("837D", "DN2", handle_dn2)
