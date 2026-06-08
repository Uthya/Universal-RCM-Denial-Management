"""Tier 4 — business rules.

These are constraints that aren't structural (Tier 1), aren't IG-mandated
(Tier 2), and aren't payer-specific (Tier 3). They're the universal sanity
checks any RCM system should enforce.

Most are WARNING-severity — they flag suspicious data without dropping the
row, since some genuinely odd-but-legitimate claims do exist (e.g. very high
charge amounts on oncology J-code claims).
"""

from __future__ import annotations

from datetime import date

from rcm.parsing.context import ParseContext, ValidationError


def _warn(ctx: ParseContext, idx: int, claim_number: str | None,
          segment: str, field: str, message: str) -> None:
    ctx.parse_errors.append(ValidationError(
        segment=segment, field=field, message=message, severity="WARNING",
        position=0, object_index=idx, claim_identifier=claim_number,
        validator="tier4_business",
    ))


def _err(ctx: ParseContext, idx: int, claim_number: str | None,
         segment: str, field: str, message: str) -> None:
    ctx.parse_errors.append(ValidationError(
        segment=segment, field=field, message=message, severity="ERROR",
        position=0, object_index=idx, claim_identifier=claim_number,
        validator="tier4_business",
    ))


def validate_tier4(ctx: ParseContext) -> None:
    today = date.today()

    for i, claim in enumerate(ctx.claims):
        if claim.dropped:
            continue

        # Service date ordering
        if claim.service_from_date and claim.service_to_date:
            if claim.service_from_date > claim.service_to_date:
                _err(ctx, i, claim.claim_number, "DTP", "service_dates",
                     f"service_from_date ({claim.service_from_date}) > "
                     f"service_to_date ({claim.service_to_date})")

        # Service date in the future
        if claim.service_from_date and claim.service_from_date > today:
            _warn(ctx, i, claim.claim_number, "DTP", "service_from_date",
                  f"service_from_date {claim.service_from_date} is in the future")

        # Sum of line charges close to total (1 cent tolerance)
        if claim.total_charge_amount is not None and claim.lines:
            line_sum = sum(
                float(line.billed_amount) for line in claim.lines
                if line.billed_amount is not None
            )
            if abs(line_sum - float(claim.total_charge_amount)) > 0.01:
                _warn(ctx, i, claim.claim_number, "CLM", "total_charge_amount",
                      f"sum(line.billed)={line_sum:.2f} differs from "
                      f"CLM02={float(claim.total_charge_amount):.2f}")

    # Duplicate claim_number within a single file
    seen: dict[str, int] = {}
    for i, claim in enumerate(ctx.claims):
        if claim.dropped:
            continue
        key = (claim.claim_number or "").strip()
        if not key:
            continue
        if key in seen:
            _err(ctx, i, claim.claim_number, "CLM", "claim_number",
                 f"Duplicate claim_number {key!r} in same file (first at index {seen[key]})")
        else:
            seen[key] = i

    # 835: duplicate CLP01 within file
    seen_remit: dict[str, int] = {}
    for i, remit in enumerate(ctx.remittances):
        if remit.dropped:
            continue
        key = (remit.claim_number or "").strip()
        if not key:
            continue
        if key in seen_remit:
            ctx.parse_errors.append(ValidationError(
                segment="CLP", field="claim_number",
                message=f"Duplicate CLP01 {key!r} in same 835 (first at index {seen_remit[key]})",
                severity="ERROR", position=0, object_index=i,
                claim_identifier=remit.claim_number, validator="tier4_business",
            ))
        else:
            seen_remit[key] = i
