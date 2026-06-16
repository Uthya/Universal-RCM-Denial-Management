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


# Diagnosis qualifiers — written to claim.diagnoses
_DX_QUALIFIERS: frozenset[str] = frozenset({"ABK", "ABF", "BJ", "BK", "BF", "APR", "PR"})

# Institutional (837I / X223) qualifiers that share the HI segment slot but
# are NOT diagnoses. We park them on variant_data instead of dropping/warning.
#   BG = condition code        BH = occurrence code (date)
#   BI = occurrence span code  BE = value code (amount)
#   DR = DRG code               TC = treatment code (rare)
_INST_QUALIFIER_BUCKET: dict[str, str] = {
    "BG": "condition_codes",
    "BH": "occurrence_codes",
    "BI": "occurrence_span_codes",
    "BE": "value_codes",
    "DR": "drg_codes",
    "TC": "treatment_codes",
}


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

        if qual in _DX_QUALIFIERS:
            # POA (Present on Admission) lives at composite index 8 (HIxx-9 spec)
            poa = composite_at(parts, 8) or None
            ctx.current_claim.diagnoses.append(DiagnosisRec(
                sequence_number=next_seq,
                diagnosis_code=code,
                diagnosis_type=qual,
                present_on_admission=poa,
            ))
            next_seq += 1
            continue

        if qual in _INST_QUALIFIER_BUCKET:
            # Institutional billing codes — park on variant_data so FE can see
            # them later. Date / amount sub-elements are preserved in the entry
            # for downstream consumers.
            bucket_key = _INST_QUALIFIER_BUCKET[qual]
            bucket = ctx.current_claim.variant_data.setdefault(bucket_key, [])
            entry: dict = {"code": code}
            date_or_amt = composite_at(parts, 3) or None
            if date_or_amt:
                entry["value"] = date_or_amt
            bucket.append(entry)
            continue

        ctx.add_event("validator_warning", segment_name="HI",
                      details={"unknown_qualifier": qual})


for variant in ("837P", "837I", "837D"):
    register_handler(variant, "HI", handle_hi)
