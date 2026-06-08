"""REF — reference identification.

REF01 qualifiers we care about:
    G1   prior authorization     -> current_authorization_no
    9F   referral                -> current_referral_no
    F8   predetermination (dental)
    1J   Medicare HHA start of care
    D9   patient account number
    X4   CLIA (lab)
    EW   mammography cert
    EI   employer ID
    P4   project code
    F8   original ref / prior payer claim control number (sometimes)
    BB   prior payer claim control number (paid before)
    F9   line item reference

The auth/referral values populate ctx.current_* so the next CLM picks them
up. Other qualifiers are buffered into the claim's variant_data when a claim
is currently open.
"""

from __future__ import annotations

from collections.abc import Sequence

from rcm.parsing.context import ParseContext
from rcm.parsing.dispatchers import register_handler
from rcm.parsing.safe import safe_element


def handle_ref(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    qualifier = safe_element(elements, 1).upper()
    value = safe_element(elements, 2)
    if not qualifier or not value:
        return

    if qualifier == "G1":
        ctx.current_authorization_no = value
        if ctx.current_claim is not None:
            ctx.current_claim.authorization_number = value
        return

    if qualifier == "9F":
        ctx.current_referral_no = value
        if ctx.current_claim is not None:
            ctx.current_claim.referral_number = value
        return

    if qualifier in ("F8", "BB"):
        # Prior payer claim control number — used to link replacement chains
        ctx.current_previous_payer_claim_control_no = value
        if ctx.current_claim is not None:
            ctx.current_claim.previous_payer_claim_control_no = value
        return

    if ctx.current_claim is not None:
        # Stash other REFs into variant_data so they're auditable
        # (not promoted to a typed column in this phase)
        ctx.current_claim.variant_data.setdefault("references", {})
        ctx.current_claim.variant_data["references"][qualifier] = value


for variant in ("837P", "837I", "837D"):
    register_handler(variant, "REF", handle_ref)
# 835 also carries REFs (e.g. provider tax ID via REF*EV); register too
register_handler("835", "REF", handle_ref)
