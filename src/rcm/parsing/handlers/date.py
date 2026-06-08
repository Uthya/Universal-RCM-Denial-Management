"""DTP (837) and DTM (835) — date/time references.

DTP01 qualifier tells us what kind of date:
    472 service date         434 statement period     435 admission date
    096 discharge date       431 onset of illness     439 accident
    090 report start date    150 service period start
    096 discharge date       454 initial treatment    197 readmission

DTM01 (835) qualifiers:
    405 production date      050 received date        232/233 stmt period
"""

from __future__ import annotations

from collections.abc import Sequence

from rcm.parsing.context import HomeCareEpisodeRec, ParseContext
from rcm.parsing.dispatchers import register_handler
from rcm.parsing.safe import safe_date, safe_date_range, safe_element


def _apply_to_claim(ctx: ParseContext, qualifier: str, start, end) -> None:
    if ctx.current_claim is None:
        # Header-level date — buffer in ctx; CLM will pick it up
        if qualifier == "472":
            ctx.current_service_from_date = start
            ctx.current_service_to_date = end or start
        return

    claim = ctx.current_claim
    if qualifier == "472":
        claim.service_from_date = start
        claim.service_to_date = end or start
    elif qualifier == "434":
        # Statement period (institutional)
        claim.service_from_date = start
        claim.service_to_date = end or start
        # Open a home_care_episode shell — populated later by handlers that
        # see CRC*75 / OASIS / discipline_mix
        if claim.service_variant == "837I" and claim.home_care_episode is None:
            claim.home_care_episode = HomeCareEpisodeRec(
                episode_start_date=start, episode_end_date=end,
            )
    elif qualifier == "435":
        claim.variant_data["admission_date"] = start.isoformat() if start else None
    elif qualifier == "096":
        claim.variant_data["discharge_date"] = start.isoformat() if start else None
    elif qualifier == "431":
        claim.variant_data["onset_of_illness_date"] = start.isoformat() if start else None
    elif qualifier == "439":
        claim.variant_data["accident_date"] = start.isoformat() if start else None
    elif qualifier == "090":
        claim.variant_data["plan_of_care_start_date"] = start.isoformat() if start else None
    elif qualifier == "150":
        claim.variant_data["service_period_start"] = start.isoformat() if start else None


def handle_dtp(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    qualifier = safe_element(elements, 1)
    fmt = safe_element(elements, 2)
    raw_date = safe_element(elements, 3)
    start, end = safe_date_range(raw_date, fmt)
    if start is None and raw_date:
        ctx.add_event("validator_warning", segment_name="DTP",
                      details={"unparseable_date": raw_date, "qualifier": qualifier})
    _apply_to_claim(ctx, qualifier, start, end)


def handle_dtm(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    """835 production / received / statement-period dates.

    DTM*050 (received date) becomes the remittance_date if a current_remittance
    is active. DTM*405 (production date at the BPR level) is captured too —
    used as a fallback when CLP has no DTM*050.
    """
    qualifier = safe_element(elements, 1)
    d = safe_date(safe_element(elements, 2))
    if d is None:
        return
    if qualifier in ("050", "232") and ctx.current_remittance is not None:
        ctx.current_remittance.remittance_date = d
    elif qualifier == "405":
        # Buffer at file level so the next CLP can use it as remittance_date
        # fallback when no DTM*050 follows. Stored on the proper ParseContext
        # field (ctx is slot=True; dynamic setattr would fail silently).
        ctx.add_event("segment_handled", segment_name="DTM",
                      details={"production_date": d.isoformat()})
        ctx._dtm_production_date = d


# DTP applies to 837P/I/D; DTM to 835
for variant in ("837P", "837I", "837D"):
    register_handler(variant, "DTP", handle_dtp)
register_handler("835", "DTM", handle_dtm)
