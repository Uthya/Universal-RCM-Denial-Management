"""HI — health care diagnosis codes.

Each HI segment can carry up to 12 composite diagnosis sub-elements. The
first sub-element of each composite is a qualifier (ABK=principal, ABF=other,
BJ=admitting, BK/BF=legacy ICD-9, APR=patient reason).

Sequence numbers are assigned in the order the diagnoses appear across all
HI segments for a claim — the order is what SV107 diagnosis_pointers index
into.
"""

from __future__ import annotations

from collections.abc import Sequence

from rcm.parsing.context import DiagnosisRec, ParseContext
from rcm.parsing.dispatchers import register_handler
from rcm.parsing.safe import composite_at, safe_element, split_composite


# Spec qualifier whitelist — anything else is unknown and skipped with a warning
_DX_QUALIFIERS: frozenset[str] = frozenset({"ABK", "ABF", "BJ", "BK", "BF", "APR", "PR"})


def handle_hi(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    if ctx.current_claim is None:
        ctx.add_error(segment="HI", field="claim_context",
                      message="HI encountered with no current claim",
                      severity="ERROR")
        return

    next_seq = len(ctx.current_claim.diagnoses) + 1
    # Up to 12 composites at HI01..HI12
    for i in range(1, 13):
        comp = safe_element(elements, i)
        if not comp:
            continue
        parts = split_composite(comp, ctx.delimiters.component)
        qual = composite_at(parts, 0).upper()
        code = composite_at(parts, 1)
        if not qual or not code:
            continue
        if qual not in _DX_QUALIFIERS:
            ctx.add_event("validator_warning", segment_name="HI",
                          details={"unknown_qualifier": qual})
            continue
        # POA (Present on Admission) lives at composite index 8 (HIxx-9 spec)
        poa = composite_at(parts, 8) or None

        ctx.current_claim.diagnoses.append(DiagnosisRec(
            sequence_number=next_seq,
            diagnosis_code=code,
            diagnosis_type=qual,
            present_on_admission=poa,
        ))
        next_seq += 1


for variant in ("837P", "837I", "837D"):
    register_handler(variant, "HI", handle_hi)
