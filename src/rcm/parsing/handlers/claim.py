"""CLM — the claim header segment in 837P/I/D.

CLM is where we transition from header context (payer, subscriber, providers
already established via NM1/SBR/REF/DTP) into a new claim. We snapshot the
header context into a ClaimRec and reset line/diagnosis counters.

Lesson C3: ``service_from_date`` is initialized to None. Do NOT use
date.today() as a placeholder. The DTP*472 handler will fill it in if the
segment is present; Tier-2 validation drops the claim otherwise.
"""

from __future__ import annotations

from collections.abc import Sequence

from rcm.parsing.context import ClaimRec, ParseContext
from rcm.parsing.dispatchers import register_handler
from rcm.parsing.safe import composite_at, safe_decimal, safe_element, split_composite


def handle_clm(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    claim_number = safe_element(elements, 1)
    if not claim_number:
        ctx.add_error(
            segment="CLM",
            field="claim_number",
            message=f"CLM at pos {ctx.segment_position}: CLM01 (claim number) is empty",
        )
        return

    total_charge = safe_decimal(safe_element(elements, 2))
    if total_charge is None:
        ctx.add_error(
            segment="CLM",
            field="total_charge_amount",
            message=f"CLM at pos {ctx.segment_position}: CLM02 (total charge) is invalid or empty",
            claim_identifier=claim_number,
        )
        # We still create the record so downstream segments don't lose their
        # context — but persistence will skip claims with ERRORs.
        total_charge = None  # type: ignore[assignment]

    # CLM05 is a composite: facility_type : qualifier : frequency
    clm05_parts = split_composite(safe_element(elements, 5), ctx.delimiters.component)
    facility_type = composite_at(clm05_parts, 0) or None
    frequency_code = composite_at(clm05_parts, 2) or None

    # claim_subtype is provisionally set; routing.derive_subtype refines it
    # post-parse once we've seen all the lines, modifiers, and certifications.
    subtype = ctx.claim_subtype or "healthcare"
    variant = ctx.service_variant or "837P"

    object_index = len(ctx.claims)

    claim = ClaimRec(
        claim_number=claim_number,
        service_variant=variant,
        claim_subtype=subtype,
        total_charge_amount=total_charge,
        facility_type_code=facility_type,
        frequency_code=frequency_code,
        service_from_date=ctx.current_service_from_date,  # None unless DTP*472 already seen
        service_to_date=ctx.current_service_to_date,
        payer_canonical_name=ctx.current_payer_name,
        patient_member_id=ctx.current_patient_member_id,
        subscriber_member_id=ctx.current_subscriber_member_id,
        billing_provider_npi=ctx.current_billing_provider_npi,
        rendering_provider_npi=ctx.current_rendering_provider_npi,
        referring_provider_npi=ctx.current_referring_provider_npi,
        authorization_number=ctx.current_authorization_no,
        referral_number=ctx.current_referral_no,
        previous_payer_claim_control_no=ctx.current_previous_payer_claim_control_no,
        raw_claim_segment=raw,
        object_index=object_index,
    )

    # CLM07/08/09 — assignment / benefits / release indicators (flag only)
    if safe_element(elements, 7):
        claim.variant_data["assignment_code"] = safe_element(elements, 7)
    if safe_element(elements, 8):
        claim.variant_data["benefits_assignment"] = safe_element(elements, 8)
    if safe_element(elements, 9):
        claim.variant_data["release_of_info"] = safe_element(elements, 9)

    # Transfer file-level addresses captured BEFORE this claim (loop 2000A
    # billing-provider N3/N4) onto the claim's variant_data.
    if ctx._pending_addresses:
        claim.variant_data.setdefault("addresses", {}).update(ctx._pending_addresses)
        ctx._pending_addresses = {}

    ctx.claims.append(claim)
    ctx.current_claim = claim
    ctx.current_line_number = 0

    # Reset per-claim transient context (auth/referral persist across CLMs
    # only when re-set by REF, which is the spec behavior).
    ctx.current_authorization_no = None
    ctx.current_referral_no = None
    ctx.current_previous_payer_claim_control_no = None
    ctx.current_service_from_date = None
    ctx.current_service_to_date = None
    # NM1 entity binding doesn't carry across claim boundaries — the next
    # CLM opens a fresh set of loops.
    ctx.current_nm1_entity = None
    ctx._addr_street1 = None
    ctx._addr_street2 = None


for variant in ("837P", "837I", "837D"):
    register_handler(variant, "CLM", handle_clm)
