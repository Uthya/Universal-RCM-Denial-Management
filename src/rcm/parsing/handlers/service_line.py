"""SV1 (837P) / SV2 (837I) / SV3 (837D) — service line segments.

All three share a similar shape: a composite procedure element (qualifier +
code + 1..4 modifiers + description), a charge amount, units, POS, and
diagnosis pointers. Per-variant fields (revenue_code for SV2, tooth_number
linkage for SV3) are handled below.
"""

from __future__ import annotations

from collections.abc import Sequence

from rcm.parsing.context import ClaimLineRec, ParseContext
from rcm.parsing.dispatchers import register_handler
from rcm.parsing.safe import composite_at, safe_decimal, safe_element, safe_int, split_composite


def _parse_procedure_composite(value: str, component_sep: str) -> tuple[
    str | None, str | None, str | None, str | None, str | None, str | None
]:
    """Return (qualifier, code, mod1, mod2, mod3, mod4)."""
    parts = split_composite(value, component_sep)
    return (
        composite_at(parts, 0) or None,
        composite_at(parts, 1) or None,
        composite_at(parts, 2) or None,
        composite_at(parts, 3) or None,
        composite_at(parts, 4) or None,
        composite_at(parts, 5) or None,
    )


def _parse_diagnosis_pointers(value: str, component_sep: str) -> list[int]:
    """SV107 is up to 4 sub-elements pointing to HI sequence numbers."""
    if not value:
        return []
    parts = split_composite(value, component_sep)
    out: list[int] = []
    for p in parts:
        n = safe_int(p)
        if n is not None:
            out.append(n)
    return out


def _next_line_number(ctx: ParseContext) -> int:
    ctx.current_line_number += 1
    return ctx.current_line_number


def handle_sv1(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    if ctx.current_claim is None:
        ctx.add_error(segment="SV1", field="claim_context",
                      message="SV1 encountered with no current claim",
                      severity="ERROR")
        return

    qual, code, m1, m2, m3, m4 = _parse_procedure_composite(
        safe_element(elements, 1), ctx.delimiters.component,
    )
    line = ClaimLineRec(
        line_number=_next_line_number(ctx),
        procedure_code=code,
        procedure_code_qualifier=qual,
        modifier1=m1,
        modifier2=m2,
        modifier3=m3,
        modifier4=m4,
        billed_amount=safe_decimal(safe_element(elements, 2)),
        units_basis=safe_element(elements, 3) or None,
        units=safe_decimal(safe_element(elements, 4)),
        place_of_service=safe_element(elements, 5) or None,
        diagnosis_pointers=_parse_diagnosis_pointers(
            safe_element(elements, 7), ctx.delimiters.component,
        ),
        raw_sv_segment=raw,
    )
    # SV109 emergency indicator, SV111 EPSDT, SV112 family planning
    for idx, key in ((9, "emergency_indicator"), (11, "epsdt_indicator"),
                     (12, "family_planning_indicator")):
        v = safe_element(elements, idx)
        if v:
            line.line_data[key] = v
    ctx.current_claim.lines.append(line)


def handle_sv2(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    if ctx.current_claim is None:
        ctx.add_error(segment="SV2", field="claim_context",
                      message="SV2 encountered with no current claim",
                      severity="ERROR")
        return

    qual, code, m1, m2, m3, m4 = _parse_procedure_composite(
        safe_element(elements, 2), ctx.delimiters.component,
    )

    revenue_code = safe_element(elements, 1) or None
    # HIPPS codes can be carried in SV202-2 when qualifier is 'HP'
    hipps = code if qual == "HP" else None

    line = ClaimLineRec(
        line_number=_next_line_number(ctx),
        revenue_code=revenue_code,
        procedure_code=code if qual != "HP" else None,
        procedure_code_qualifier=qual,
        modifier1=m1,
        modifier2=m2,
        modifier3=m3,
        modifier4=m4,
        hipps_code=hipps,
        billed_amount=safe_decimal(safe_element(elements, 3)),
        units_basis=safe_element(elements, 4) or None,
        units=safe_decimal(safe_element(elements, 5)),
        raw_sv_segment=raw,
    )
    # SV207 non-covered charge
    nc = safe_decimal(safe_element(elements, 7))
    if nc is not None:
        line.line_data["non_covered_charge"] = str(nc)
    ctx.current_claim.lines.append(line)


def handle_sv3(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    if ctx.current_claim is None:
        ctx.add_error(segment="SV3", field="claim_context",
                      message="SV3 encountered with no current claim",
                      severity="ERROR")
        return

    qual, code, m1, m2, m3, m4 = _parse_procedure_composite(
        safe_element(elements, 1), ctx.delimiters.component,
    )
    line = ClaimLineRec(
        line_number=_next_line_number(ctx),
        procedure_code=code,
        procedure_code_qualifier=qual or "AD",
        modifier1=m1,
        modifier2=m2,
        modifier3=m3,
        modifier4=m4,
        billed_amount=safe_decimal(safe_element(elements, 2)),
        place_of_service=safe_element(elements, 3) or None,
        # SV305 procedure count
        units=safe_decimal(safe_element(elements, 5)),
        raw_sv_segment=raw,
    )
    ctx.current_claim.lines.append(line)


register_handler("837P", "SV1", handle_sv1)
register_handler("837I", "SV2", handle_sv2)
register_handler("837D", "SV3", handle_sv3)

# Some files mix lines; allow each handler to fire on its native variant only
