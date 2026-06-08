"""PWK — paperwork / attachment.

Critical for therapy and DME claims that require plan-of-care or operative
notes. The presence/absence of this segment drives feature
``paperwork_missing_when_required`` in the FE pipeline.
"""

from __future__ import annotations

from collections.abc import Sequence

from rcm.parsing.context import ClaimAttachmentRec, ParseContext
from rcm.parsing.dispatchers import register_handler
from rcm.parsing.safe import safe_element


def handle_pwk(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    if ctx.current_claim is None:
        return
    report_type = safe_element(elements, 1)
    transmission = safe_element(elements, 2)
    if not report_type or not transmission:
        return
    # PWK05 qualifier (AC=attachment control number) + PWK06 value
    qual = safe_element(elements, 5)
    control_no = safe_element(elements, 6) if qual.upper() == "AC" else None

    ctx.current_claim.attachments.append(ClaimAttachmentRec(
        report_type_code=report_type,
        transmission_code=transmission,
        attachment_control_no=control_no or None,
    ))


for variant in ("837P", "837I", "837D"):
    register_handler(variant, "PWK", handle_pwk)
