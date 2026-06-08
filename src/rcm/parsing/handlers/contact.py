"""NM1, N1, PER — names and contacts.

NM1 carries different things depending on NM101 entity code:
    85 billing provider     82 rendering provider     77 service location
    DN referring            DK ordering               DQ supervising
    87 pay-to provider      IL subscriber             QC patient
    PR payer                PE payee                  40 receiver  41 submitter

We don't materialize a Provider/Payer/Patient row here — handlers stash the
identifying info in ParseContext.current_* fields and let CLM consume it.
"""

from __future__ import annotations

from collections.abc import Sequence

from rcm.parsing.context import (
    ParseContext,
    PatientRec,
    PayerRec,
    ProviderRec,
    SubscriberRec,
)
from rcm.parsing.dispatchers import register_handler
from rcm.parsing.safe import safe_element


_PROVIDER_ENTITY_TO_TYPE: dict[str, str] = {
    "85": "billing",
    "87": "pay_to",
    "82": "rendering",
    "77": "facility",
    "DN": "referring",
    "DK": "ordering",
    "DQ": "supervising",
}


def handle_nm1(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    entity = safe_element(elements, 1).upper()
    entity_type_qualifier = safe_element(elements, 2)        # 1=person, 2=org
    last_or_org = safe_element(elements, 3)
    first_name = safe_element(elements, 4)
    id_qualifier = safe_element(elements, 8).upper()
    id_code = safe_element(elements, 9)

    # Remember the current NM1 entity so trailing N3/N4/PRV bind to it.
    ctx.current_nm1_entity = entity
    # Discard any address fragments left over from a prior loop with no N4.
    ctx._addr_street1 = None
    ctx._addr_street2 = None

    if entity in _PROVIDER_ENTITY_TO_TYPE:
        ptype = _PROVIDER_ENTITY_TO_TYPE[entity]
        npi = id_code if id_qualifier == "XX" else None
        if npi:
            rec = ProviderRec(
                npi=npi,
                provider_type=ptype,
                organization_name=last_or_org if entity_type_qualifier == "2" else None,
                last_name=last_or_org if entity_type_qualifier == "1" else None,
                first_name=first_name or None,
            )
            # Apply any PRV taxonomy seen earlier in the loop (PRV often
            # precedes the NM1 it describes in 837P loop 2000A).
            buffered_taxonomy = ctx._pending_prv.pop(ptype, None)
            if buffered_taxonomy:
                rec.taxonomy_code = buffered_taxonomy
                if ptype == "billing":
                    ctx.current_billing_provider_taxonomy = buffered_taxonomy
            ctx.providers.append(rec)
            if ptype == "billing":
                ctx.current_billing_provider_npi = npi
                if ctx.current_claim is not None and not ctx.current_claim.billing_provider_npi:
                    ctx.current_claim.billing_provider_npi = npi
            elif ptype == "rendering":
                ctx.current_rendering_provider_npi = npi
                # 837s often emit rendering NPI at the line level (loop 2400),
                # AFTER the CLM. Propagate to the open claim too.
                if ctx.current_claim is not None and not ctx.current_claim.rendering_provider_npi:
                    ctx.current_claim.rendering_provider_npi = npi
            elif ptype == "referring":
                ctx.current_referring_provider_npi = npi
                if ctx.current_claim is not None and not ctx.current_claim.referring_provider_npi:
                    ctx.current_claim.referring_provider_npi = npi
        return

    if entity == "IL":
        # Subscriber
        member_id = id_code if id_qualifier == "MI" else None
        if member_id:
            ctx.current_subscriber_member_id = member_id
            ctx.subscribers.append(SubscriberRec(
                member_id=member_id,
                relationship_code=ctx.current_subscriber_relationship,
                group_number=ctx.current_subscriber_group_number,
                coordination_of_benefits=ctx.current_subscriber_cob_position,
                payer_canonical_name=ctx.current_payer_name,
            ))
            # When relationship code = 18 (self), the subscriber IS the patient.
            # No NM1*QC will follow; fill in patient_member_id now so CLM sees it.
            if ctx.current_subscriber_relationship == "18":
                ctx.current_patient_member_id = member_id
                ctx.current_patient_last_name = ctx.current_patient_last_name or last_or_org or None
                ctx.current_patient_first_name = ctx.current_patient_first_name or (first_name or None)
                ctx.patients.append(PatientRec(
                    member_id=member_id,
                    first_name=first_name or None,
                    last_name=last_or_org or None,
                    date_of_birth=ctx.current_patient_dob,
                    gender=ctx.current_patient_gender,
                ))
        return

    if entity == "QC":
        # Patient
        member_id = id_code if id_qualifier == "MI" else (
            ctx.current_patient_member_id or ctx.current_subscriber_member_id
        )
        ctx.current_patient_last_name = last_or_org or None
        ctx.current_patient_first_name = first_name or None
        if member_id:
            ctx.current_patient_member_id = member_id
            ctx.patients.append(PatientRec(
                member_id=member_id,
                first_name=first_name or None,
                last_name=last_or_org or None,
                date_of_birth=ctx.current_patient_dob,
                gender=ctx.current_patient_gender,
            ))
        return

    if entity == "PR":
        # Payer (NM1 form). The PI/XV qualifier carries the payer ID.
        ctx.current_payer_name = last_or_org or None
        if last_or_org:
            ctx.payers.append(PayerRec(
                canonical_name=last_or_org,
                payer_taxonomy=None,
            ))
        return

    # 40 receiver / 41 submitter / others — informational, no model yet
    # (still counted as handled — caller doesn't need a record).


def handle_n1(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    """N1 in 835 carries payer (N1*PR) and payee (N1*PE) names."""
    entity = safe_element(elements, 1).upper()
    name = safe_element(elements, 2)
    if not name:
        return
    if entity == "PR":
        ctx.current_payer_name = name
        ctx.payers.append(PayerRec(canonical_name=name))


def handle_per(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    """PER — contact information. Captured as ParseEvent for telemetry only;
    no master-data row created in this phase."""
    contact_func = safe_element(elements, 1)
    ctx.add_event("segment_handled", segment_name="PER",
                  details={"contact_function": contact_func})


def handle_dmg(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    """DMG — demographic info (date of birth, gender). Sets current_patient_*
    so the next NM1*QC can attach it to the patient record."""
    from rcm.parsing.safe import safe_date
    date_qualifier = safe_element(elements, 1)
    if date_qualifier == "D8":
        ctx.current_patient_dob = safe_date(safe_element(elements, 2))
    ctx.current_patient_gender = safe_element(elements, 3) or None


# Entity-code → short label for the address bucket on claim.variant_data.
_NM1_ENTITY_LABEL: dict[str, str] = {
    "85": "billing_provider",
    "87": "pay_to_provider",
    "82": "rendering_provider",
    "77": "service_facility",
    "DN": "referring_provider",
    "DK": "ordering_provider",
    "DQ": "supervising_provider",
    "IL": "subscriber",
    "QC": "patient",
    "PR": "payer",
    "PE": "payee",
    "40": "receiver",
    "41": "submitter",
}


def handle_n3(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    """N3 — address line(s). Buffered until the next N4 consumes them.

    EDI spec: N3*street_line_1*street_line_2. Line 2 is optional.
    We don't persist anything here — the N4 handler stitches city/state/zip
    onto these street lines and writes the assembled address."""
    ctx._addr_street1 = safe_element(elements, 1) or None
    ctx._addr_street2 = safe_element(elements, 2) or None


def handle_n4(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    """N4 — city/state/zip/country. Combines with buffered N3 lines and
    attaches the address to the NM1 entity that opened the loop:

      * Provider entities (85/82/77/DN/...): writes `state` onto the most
        recent ProviderRec so providers.state gets populated.
      * Any entity with a current claim: writes a structured address dict
        onto `claim.variant_data.addresses[<label>]` so the full address
        survives without a schema migration.
      * Always emits a `segment_handled` parse_event so the address shows up
        in the EDI Inspector's parse trace.
    """
    city = safe_element(elements, 1) or None
    state = (safe_element(elements, 2) or "").upper() or None
    zip_code = safe_element(elements, 3) or None
    country = (safe_element(elements, 4) or "").upper() or None

    addr = {
        "street1": ctx._addr_street1,
        "street2": ctx._addr_street2,
        "city": city,
        "state": state,
        "zip": zip_code,
        "country": country,
    }
    entity = ctx.current_nm1_entity
    label = _NM1_ENTITY_LABEL.get(entity or "", f"nm1_{entity}" if entity else "unknown")

    # Stamp ProviderRec.state for provider-type entities so providers.state
    # gets a value at persistence time (column already exists).
    if entity in _PROVIDER_ENTITY_TO_TYPE and ctx.providers:
        # Match on the same provider_type rather than blindly using the last
        # ProviderRec: an NM1*85 with no NPI does not create a record, so the
        # "last" provider might belong to a previous loop.
        target_type = _PROVIDER_ENTITY_TO_TYPE[entity]
        for prov in reversed(ctx.providers):
            if prov.provider_type == target_type and prov.state is None:
                prov.state = state
                break

    # Park the full address on the active claim's variant_data so it survives
    # round-trip to the DB without needing a new column. If we're before the
    # first CLM (e.g. billing-provider loop 2000A), buffer on ctx and let CLM
    # transfer the buffer into the new claim.
    if ctx.current_claim is not None:
        addresses = ctx.current_claim.variant_data.setdefault("addresses", {})
        addresses[label] = addr
    else:
        ctx._pending_addresses[label] = addr

    ctx.add_event(
        "segment_handled",
        segment_name="N4",
        details={"entity": entity, "label": label, "state": state, "zip": zip_code},
    )

    # Clear the N3 buffer so a later N4 without a preceding N3 doesn't pick up
    # stale lines.
    ctx._addr_street1 = None
    ctx._addr_street2 = None


def handle_prv(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    """PRV — provider specialty / taxonomy.

    Spec: PRV*provider_code*reference_id_qualifier*reference_id
        PRV01 = BI (billing) | PE (performing) | RF (referring) | AT (attending) | ...
        PRV02 = ZZ or PXC for taxonomy code list
        PRV03 = taxonomy code itself

    We stamp the taxonomy on the most recent ProviderRec of the matching type
    (so providers.taxonomy_code gets populated without a schema change)."""
    prv01 = (safe_element(elements, 1) or "").upper()
    prv02 = (safe_element(elements, 2) or "").upper()
    prv03 = safe_element(elements, 3) or None

    # Only the taxonomy-code variant is meaningful here.
    is_taxonomy = prv02 in {"ZZ", "PXC"}
    if not (prv03 and is_taxonomy):
        ctx.add_event("segment_handled", segment_name="PRV",
                      details={"qualifier": prv01, "code_list": prv02})
        return

    _prv_to_provider_type: dict[str, str] = {
        "BI": "billing",
        "PE": "rendering",  # performing == rendering
        "RF": "referring",
        "AT": "billing",    # attending often == billing for inst.
        "OP": "ordering",
        "SU": "supervising",
    }
    target_type = _prv_to_provider_type.get(prv01)

    # Prefer matching by current_nm1_entity (most precise), then by PRV01 type,
    # then fall back to the last provider in the file.
    target = None
    if ctx.current_nm1_entity in _PROVIDER_ENTITY_TO_TYPE and ctx.providers:
        want = _PROVIDER_ENTITY_TO_TYPE[ctx.current_nm1_entity]
        for prov in reversed(ctx.providers):
            if prov.provider_type == want:
                target = prov
                break
    if target is None and target_type and ctx.providers:
        for prov in reversed(ctx.providers):
            if prov.provider_type == target_type:
                target = prov
                break

    if target is not None and not target.taxonomy_code:
        target.taxonomy_code = prv03
        # Also propagate billing taxonomy onto the current claim so FE can
        # use it without a join (the FE layer reads
        # ctx.current_billing_provider_taxonomy).
        if target.provider_type == "billing":
            ctx.current_billing_provider_taxonomy = prv03
    elif target is None and target_type:
        # PRV before the NM1 it describes — buffer; NM1 will apply.
        ctx._pending_prv[target_type] = prv03

    ctx.add_event("segment_handled", segment_name="PRV",
                  details={"qualifier": prv01, "taxonomy": prv03,
                           "target_type": target.provider_type if target else None})


def handle_pat(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    """PAT — patient information (loop 2010CA).

    Spec: PAT*individual_relationship*patient_location*employment_status*
              student_status*date_format*date_of_death*unit_qualifier*weight

    Most useful fields:
        PAT01 = patient-to-subscriber relationship code (01 spouse / 19 child / ...)
        PAT07 = unit qualifier (01 = lbs, 02 = kg)
        PAT08 = patient weight
    We stash the parsed fields on the current claim's variant_data so FE can
    consume them later; weight is a frequent feature for transport/specialty.
    """
    relationship = safe_element(elements, 1) or None
    unit = safe_element(elements, 7) or None
    weight = safe_element(elements, 8) or None

    # If the surrounding subscriber didn't carry a relationship code, fall
    # back to PAT01 so subscribers.relationship_code gets populated.
    if relationship and ctx.subscribers and not ctx.subscribers[-1].relationship_code:
        ctx.subscribers[-1].relationship_code = relationship

    if ctx.current_claim is not None:
        bucket = ctx.current_claim.variant_data.setdefault("patient", {})
        if relationship: bucket["relationship_to_subscriber"] = relationship
        if unit:         bucket["weight_unit"] = unit
        if weight:
            try:
                bucket["weight"] = float(weight)
            except (TypeError, ValueError):
                bucket["weight_raw"] = weight

    ctx.add_event("segment_handled", segment_name="PAT",
                  details={"relationship": relationship,
                           "unit": unit, "weight": weight})


# --- register against all variants ----------------------------------------
for variant in ("837P", "837I", "837D", "835"):
    register_handler(variant, "NM1", handle_nm1)
    register_handler(variant, "PER", handle_per)
    register_handler(variant, "DMG", handle_dmg)
    register_handler(variant, "N3",  handle_n3)
    register_handler(variant, "N4",  handle_n4)
register_handler("835", "N1", handle_n1)
register_handler("837P", "N1", handle_n1)
register_handler("837I", "N1", handle_n1)
register_handler("837D", "N1", handle_n1)
# PRV / PAT are 837 loops only — not part of 835.
for variant in ("837P", "837I", "837D"):
    register_handler(variant, "PRV", handle_prv)
    register_handler(variant, "PAT", handle_pat)
