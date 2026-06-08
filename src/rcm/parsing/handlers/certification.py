"""CR1 (ambulance) / CR3 (DME oxygen) / CRC (conditions indicators).

CR1 is the ambulance certification — populates a TransportCertRec on the
current claim. CRC carries condition codes (e.g. CRC*07 = ambulance cert;
CRC*75 = home health certifications including homebound).
"""

from __future__ import annotations

from collections.abc import Sequence

from rcm.parsing.context import (
    ClaimCertRec,
    HomeCareEpisodeRec,
    ParseContext,
    TransportCertRec,
)
from rcm.parsing.dispatchers import register_handler
from rcm.parsing.safe import safe_decimal, safe_element, safe_int


def handle_cr1(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    if ctx.current_claim is None:
        return
    weight_lbs = safe_int(safe_element(elements, 2))
    transport_code = safe_element(elements, 3).upper() or None
    reason_code = safe_element(elements, 4).upper() or None
    miles = safe_decimal(safe_element(elements, 6))
    round_trip_desc = safe_element(elements, 9)
    emergent = transport_code in ("E", "EM")

    cert = TransportCertRec(
        transport_miles=miles,
        patient_weight_lbs=weight_lbs,
        transport_reason_code=reason_code,
        round_trip=bool(round_trip_desc),
        emergent=emergent,
    )
    ctx.current_claim.transport_cert = cert
    ctx.current_claim.certifications.append(ClaimCertRec(
        certification_type="ambulance",
        raw_segment=raw,
        structured_data={
            "miles": str(miles) if miles is not None else None,
            "weight_lbs": weight_lbs,
            "reason_code": reason_code,
            "transport_code": transport_code,
        },
    ))


def handle_cr3(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    """CR3 — DME certification (oxygen / power wheelchair / etc.)."""
    if ctx.current_claim is None:
        return
    cert_type = safe_element(elements, 1)  # I=initial, R=renewal, S=revised
    duration_months = safe_int(safe_element(elements, 3))
    ctx.current_claim.certifications.append(ClaimCertRec(
        certification_type="dme",
        raw_segment=raw,
        structured_data={
            "certification_type_code": cert_type,
            "duration_months": duration_months,
        },
    ))


def handle_crc(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    """CRC — conditions indicators. Most important codes:
        07  ambulance certification
        75  home health certification (CRC03 includes homebound flag)
        11  hospice certification
    """
    if ctx.current_claim is None:
        return
    category = safe_element(elements, 1).upper()
    applies = (safe_element(elements, 2).upper() == "Y")
    codes = [safe_element(elements, i) for i in range(3, 8) if safe_element(elements, i)]

    if category == "07":
        # Ambulance — augment existing transport cert if present
        ctx.current_claim.certifications.append(ClaimCertRec(
            certification_type="ambulance",
            raw_segment=raw,
            structured_data={"applies": applies, "codes": codes},
        ))
    elif category == "75":
        ctx.current_claim.certifications.append(ClaimCertRec(
            certification_type="homebound",
            raw_segment=raw,
            structured_data={"applies": applies, "codes": codes},
        ))
        if ctx.current_claim.service_variant == "837I":
            if ctx.current_claim.home_care_episode is None and ctx.current_claim.service_from_date:
                ctx.current_claim.home_care_episode = HomeCareEpisodeRec(
                    episode_start_date=ctx.current_claim.service_from_date,
                )
            if ctx.current_claim.home_care_episode is not None:
                ctx.current_claim.home_care_episode.homebound_certified = applies
    elif category == "11":
        ctx.current_claim.certifications.append(ClaimCertRec(
            certification_type="hospice_election",
            raw_segment=raw,
            structured_data={"applies": applies, "codes": codes},
        ))


for variant in ("837P", "837I", "837D"):
    register_handler(variant, "CR1", handle_cr1)
    register_handler(variant, "CR3", handle_cr3)
    register_handler(variant, "CRC", handle_crc)
