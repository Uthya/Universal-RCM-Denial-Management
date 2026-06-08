"""Tier 3 — payer-specific validation, data-driven from `payer_policies`.

Loads policies once per parse keyed by `(payer_canonical_name, policy_type)`
and applies them to each claim. Three policy_types are evaluated in this
phase; more can be added without parser changes:

    prior_auth          — auth_required_for_cpt_payer
    referral_required   — referral_required_for_specialty
    timely_filing       — claim aged past payer's window

Each policy can scope to a CPT list (`applies_to_codes`) and/or a
service_variant + claim_subtype. The `structured_rule` JSONB is reserved
for future engine-level expansion; this tier only inspects `policy_type`
and `applies_to_codes` for now.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rcm.models.reference import Payer, PayerPolicy
from rcm.parsing.context import ParseContext, ValidationError


_DEFAULT_TIMELY_FILING_DAYS = 365


async def validate_tier3(ctx: ParseContext, session: AsyncSession) -> None:
    if not ctx.claims:
        return

    # Distinct payer names seen in this file
    payer_names = {c.payer_canonical_name for c in ctx.claims if c.payer_canonical_name}
    if not payer_names:
        return

    # Pre-load: payer rows + their policies
    payer_rows = (await session.execute(
        select(Payer).where(Payer.canonical_name.in_(payer_names))
    )).scalars().all()
    payers_by_name = {p.canonical_name: p for p in payer_rows}

    if not payer_rows:
        return

    policy_rows = (await session.execute(
        select(PayerPolicy).where(PayerPolicy.payer_id.in_([p.id for p in payer_rows]))
    )).scalars().all()
    # Bucket by (payer_id, policy_type)
    by_payer_type: dict[tuple[int, str], list[PayerPolicy]] = {}
    for p in policy_rows:
        by_payer_type.setdefault((p.payer_id, p.policy_type.value if hasattr(p.policy_type, "value") else str(p.policy_type)), []).append(p)

    for i, claim in enumerate(ctx.claims):
        if claim.dropped:
            continue
        payer = payers_by_name.get(claim.payer_canonical_name or "")
        if payer is None:
            continue

        primary_cpt = next((line.procedure_code for line in claim.lines if line.procedure_code), None)

        # ---- prior_auth ----
        for pol in by_payer_type.get((payer.id, "prior_auth"), ()):
            if pol.applies_to_codes and primary_cpt not in pol.applies_to_codes:
                continue
            if pol.service_variant and pol.service_variant != claim.service_variant:
                continue
            if not claim.authorization_number:
                ctx.parse_errors.append(ValidationError(
                    segment="REF", field="authorization_number",
                    message=f"Payer {claim.payer_canonical_name} requires prior auth for "
                            f"CPT {primary_cpt}; no REF*G1 present",
                    severity="ERROR", position=0, object_index=i,
                    claim_identifier=claim.claim_number, validator="tier3_payer",
                ))

        # ---- referral_required ----
        for pol in by_payer_type.get((payer.id, "referral_required"), ()):
            if pol.claim_subtype and pol.claim_subtype != claim.claim_subtype:
                continue
            if not claim.referral_number:
                ctx.parse_errors.append(ValidationError(
                    segment="REF", field="referral_number",
                    message=f"Payer {claim.payer_canonical_name} requires referral for "
                            f"{claim.claim_subtype}; no REF*9F present",
                    severity="ERROR", position=0, object_index=i,
                    claim_identifier=claim.claim_number, validator="tier3_payer",
                ))

        # ---- timely_filing ----
        timely_policies = by_payer_type.get((payer.id, "timely_filing"), ())
        window_days = _DEFAULT_TIMELY_FILING_DAYS
        for pol in timely_policies:
            sr = pol.structured_rule or {}
            if isinstance(sr.get("days"), int):
                window_days = sr["days"]
                break
        if claim.service_from_date and claim.submission_date:
            age = (claim.submission_date - claim.service_from_date).days
            if age > window_days:
                ctx.parse_errors.append(ValidationError(
                    segment="DTP", field="service_from_date",
                    message=f"Claim is {age} days old; payer timely filing "
                            f"window is {window_days} days",
                    severity="WARNING", position=0, object_index=i,
                    claim_identifier=claim.claim_number, validator="tier3_payer",
                ))
