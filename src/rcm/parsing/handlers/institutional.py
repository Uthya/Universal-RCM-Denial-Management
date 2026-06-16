"""837I-specific segments that don't fit in any other module.

Currently:
    CL1 — institutional claim info (admission type, admission source, patient status)

Per the X223 IG loop 2300:
    CL101 = admission type code
        1=Emergency / 2=Urgent / 3=Elective / 4=Newborn / 5=Trauma / 9=Information unavailable
    CL102 = admission source code
        1=Non-healthcare facility / 2=Clinic / 4=Transfer from hospital / 5=SNF / 6=HHA / 7=ER / etc.
    CL103 = patient status code
        01=Discharged home / 02=Discharged to short-term hospital / 03=Discharged to SNF / ...
        20=Expired / 30=Still patient / etc.

These three are the most-quoted institutional features for denial models —
inpatient claims without a valid admission_type often get rejected at intake.
We stash them under `claim.variant_data['institutional']` so persistence
preserves them in the JSONB column without a schema change.
"""

from __future__ import annotations

from collections.abc import Sequence

from rcm.parsing.context import ParseContext
from rcm.parsing.dispatchers import register_handler
from rcm.parsing.safe import safe_element


def handle_cl1(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    if ctx.current_claim is None:
        # Stray CL1 — not a fatal error; surface as warning, no record created
        ctx.add_event("validator_warning", segment_name="CL1",
                      details={"reason": "CL1 outside of any open claim"})
        return

    admission_type = safe_element(elements, 1) or None
    admission_source = safe_element(elements, 2) or None
    patient_status = safe_element(elements, 3) or None

    inst = ctx.current_claim.variant_data.setdefault("institutional", {})
    if admission_type:    inst["admission_type_code"] = admission_type
    if admission_source:  inst["admission_source_code"] = admission_source
    if patient_status:    inst["patient_status_code"] = patient_status

    ctx.add_event("segment_handled", segment_name="CL1",
                  details={"admission_type": admission_type,
                           "admission_source": admission_source,
                           "patient_status": patient_status})


# CL1 is X223 (837I) only. Spec puts it in loop 2300 for institutional claims.
register_handler("837I", "CL1", handle_cl1)
