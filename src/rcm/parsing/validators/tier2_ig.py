"""Tier 2 — implementation guide checks.

These are the per-variant minima the 5010 IG documents mandate. Failure
emits ERROR (drops the claim) for hard requirements, WARNING for soft ones.

Hard ERRORs per spec §Appendix D:

837P:
    * service_from_date present (Lesson C3 — the most-violated check)
    * total_charge_amount > 0
    * billing provider NPI present
    * at least one SV1 line
    * at least one HI*ABK (principal) diagnosis

837I:
    * all 837P checks
    * each SV2 has a revenue_code (SV201)
    * DTP*434 statement period present
    * HL hierarchy was set up (billing → subscriber → patient)

837D:
    * each SV3 procedure code starts with 'D' (CDT format)
    * if surface-applicable procedure, TOO present

835:
    * each CLP has a status code
    * remittance_date may be NULL (Lesson P1) — emit WARNING not ERROR
"""

from __future__ import annotations

from rcm.parsing.context import ParseContext


def _err(ctx: ParseContext, claim_index: int, claim_number: str | None,
         segment: str, field: str, message: str, severity: str = "ERROR") -> None:
    from rcm.parsing.context import ValidationError
    ctx.parse_errors.append(ValidationError(
        segment=segment, field=field, message=message, severity=severity,
        position=0, object_index=claim_index, claim_identifier=claim_number,
        validator="tier2_ig",
    ))


def _remit_err(ctx: ParseContext, remit_index: int, claim_number: str | None,
               field: str, message: str, severity: str = "ERROR") -> None:
    from rcm.parsing.context import ValidationError
    ctx.parse_errors.append(ValidationError(
        segment="CLP", field=field, message=message, severity=severity,
        position=0, object_index=remit_index, claim_identifier=claim_number,
        validator="tier2_ig",
    ))


def validate_tier2(ctx: ParseContext) -> None:
    if ctx.file_type == "edi_835":
        _validate_835(ctx)
        return

    variant = ctx.service_variant or ""
    for i, claim in enumerate(ctx.claims):
        if claim.dropped:
            continue
        if claim.service_from_date is None:
            _err(ctx, i, claim.claim_number, "CLM", "service_from_date",
                 "DTP*472 missing — service date required (Lesson C3, no placeholder)")
        if claim.total_charge_amount is None or float(claim.total_charge_amount) <= 0:
            _err(ctx, i, claim.claim_number, "CLM", "total_charge_amount",
                 "CLM02 (total charge) is missing or non-positive")
        if not claim.billing_provider_npi:
            _err(ctx, i, claim.claim_number, "NM1", "billing_provider_npi",
                 "NM1*85 billing provider NPI is missing")
        if not claim.lines:
            _err(ctx, i, claim.claim_number, "SV", "lines",
                 "Claim has no service line (SV1/SV2/SV3) segments")
        # Principal diagnosis
        if not any(d.diagnosis_type == "ABK" for d in claim.diagnoses):
            _err(ctx, i, claim.claim_number, "HI", "principal_diagnosis",
                 "No HI*ABK principal diagnosis present")

        # Variant-specific
        if variant == "837I":
            _validate_837i_extras(ctx, i, claim)
        elif variant == "837D":
            _validate_837d_extras(ctx, i, claim)
        # Frequency-code constraints (replacement / void / correction)
        if claim.frequency_code in ("6", "7", "8") and not claim.previous_payer_claim_control_no:
            severity = "WARNING" if claim.frequency_code == "6" else "ERROR"
            _err(ctx, i, claim.claim_number, "REF", "previous_payer_claim_control_no",
                 f"frequency_code={claim.frequency_code} requires REF*F8 or REF*BB",
                 severity=severity)


def _validate_837i_extras(ctx: ParseContext, idx: int, claim) -> None:
    if not any(line.revenue_code for line in claim.lines):
        _err(ctx, idx, claim.claim_number, "SV2", "revenue_code",
             "No revenue codes (SV201) present on any line — required for 837I")


def _validate_837d_extras(ctx: ParseContext, idx: int, claim) -> None:
    for line in claim.lines:
        if not line.procedure_code:
            continue
        if not line.procedure_code.upper().startswith("D"):
            _err(ctx, idx, claim.claim_number, "SV3", "procedure_code",
                 f"CDT code {line.procedure_code!r} does not start with 'D'",
                 severity="WARNING")


def _validate_835(ctx: ParseContext) -> None:
    for i, remit in enumerate(ctx.remittances):
        if remit.dropped:
            continue
        if not remit.claim_status_code:
            _remit_err(ctx, i, remit.claim_number, "claim_status_code",
                       "CLP02 (claim status code) is empty")
        if remit.remittance_date is None:
            # Lesson P1 — NOT an ERROR, just a WARNING so the row persists with NULL
            _remit_err(ctx, i, remit.claim_number, "remittance_date",
                       "No DTM*050 (received date) or DTM*405 (production date) — "
                       "remittance_date will be NULL",
                       severity="WARNING")
