"""Training-corpus loader.

Pulls labelled rows from ``mv_claim_labels`` and joins everything FE needs:

    claims            (CLM-level fields, dates, NPIs)
    claim_lines       (procedure_code, modifiers, POS, revenue, hipps, tooth_*, NDC)
    diagnoses         (codes + types)
    patients          (DOB, gender)
    providers         (billing taxonomy, state)
    subscribers       (COB position, group_number)
    payers            (taxonomy, canonical_name)

Returns a pandas DataFrame keyed by ``claim_id`` with one row per claim, plus
a ``denied`` label column from ``mv_claim_labels``. Multi-row child data
(lines, diagnoses) is rolled into list/scalar columns so each FE category
can compute its features without re-JOINing.

This is STAGE 1 from spec §4.9. It is the single SQL entry point for the
pandas-side FE pipeline.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ---- claim-level columns we pull as scalars ---------------------------------
_CLAIM_COLUMNS = """
    c.id                              AS claim_id,
    c.claim_number,
    c.service_variant,
    c.claim_subtype,
    c.payer_id,
    c.patient_id,
    c.subscriber_id,
    c.billing_provider_id,
    c.rendering_provider_id,
    c.referring_provider_id,
    c.total_charge_amount,
    c.facility_type_code,
    c.frequency_code,
    c.service_from_date,
    c.service_to_date,
    c.submission_date,
    c.authorization_number,
    c.referral_number,
    c.previous_payer_claim_control_no,
    c.variant_data,
    py.canonical_name                AS payer_canonical_name,
    py.payer_taxonomy                AS payer_taxonomy,
    pt.date_of_birth                 AS patient_dob,
    pt.gender                        AS patient_gender,
    bpr.taxonomy_code                AS billing_provider_taxonomy,
    bpr.state                        AS billing_provider_state,
    bpr.npi                          AS billing_provider_npi,
    rpr.npi                          AS rendering_provider_npi,
    sub.coordination_of_benefits     AS subscriber_cob,
    sub.relationship_code            AS subscriber_relationship,
    sub.group_number                 AS subscriber_group
"""


async def load_training_corpus(
    session: AsyncSession,
    *,
    service_variant: str | None = None,
    claim_subtype: str | None = None,
    since: date | None = None,
    until: date | None = None,
    limit: int | None = None,
) -> pd.DataFrame:
    """Return the labelled training corpus as a pandas DataFrame.

    Filters:
        service_variant / claim_subtype — restrict to a single per-variant pool
        since / until                   — date window on service_from_date
        limit                           — cap row count (dev/test convenience)

    The output DataFrame has one row per claim plus columns:
        denied (int 0/1), payer_canonical_name (str), primary_cpt (str|None),
        primary_dx (str|None), modifiers (list[str]), revenue_codes (list[str]),
        hipps_codes (list[str]), tooth_numbers (list[str]), procedure_codes
        (list[str]), place_of_service (str|None), claim_lines_count (int),
        diagnoses_count (int), has_paperwork (bool), has_certification (bool),
        cert_types (list[str]), amounts (dict[str, float]), home_care_*
        (when present), transport_* (when present).
    """
    where_clauses = ["mv.denied IS NOT NULL"]
    params: dict[str, Any] = {}

    if service_variant is not None:
        where_clauses.append("mv.service_variant = :service_variant")
        params["service_variant"] = service_variant
    if claim_subtype is not None:
        where_clauses.append("mv.claim_subtype = :claim_subtype")
        params["claim_subtype"] = claim_subtype
    if since is not None:
        where_clauses.append("mv.service_from_date >= :since")
        params["since"] = since
    if until is not None:
        where_clauses.append("mv.service_from_date <= :until")
        params["until"] = until

    where_sql = " AND ".join(where_clauses)
    limit_sql = f"LIMIT {int(limit)}" if limit else ""

    base_sql = text(f"""
        SELECT
            {_CLAIM_COLUMNS},
            mv.denied
        FROM mv_claim_labels mv
        JOIN claims c        ON c.id = mv.claim_id
        LEFT JOIN payers py  ON py.id = c.payer_id
        LEFT JOIN patients pt ON pt.id = c.patient_id
        LEFT JOIN providers bpr ON bpr.id = c.billing_provider_id
        LEFT JOIN providers rpr ON rpr.id = c.rendering_provider_id
        LEFT JOIN subscribers sub ON sub.id = c.subscriber_id
        WHERE {where_sql}
        ORDER BY mv.claim_id
        {limit_sql}
    """)

    claim_rows = (await session.execute(base_sql, params)).mappings().all()
    if not claim_rows:
        logger.warning("load_training_corpus: empty result for filters %s", params)
        return _empty_corpus()

    claim_ids = [r["claim_id"] for r in claim_rows]

    # ---- multi-row children, loaded in bulk and rolled into per-claim lists ---
    lines_by_claim, dx_by_claim, attachments_by_claim, certs_by_claim, amounts_by_claim, \
        episodes_by_claim, transports_by_claim = await _load_children(session, claim_ids)

    base_df = pd.DataFrame(claim_rows)
    base_df["denied"] = base_df["denied"].astype("int8")

    rolled: list[dict[str, Any]] = []
    for cid in claim_ids:
        lines = lines_by_claim.get(cid, [])
        dxs = dx_by_claim.get(cid, [])

        # Primary CPT = first line by line_number
        primary_cpt = next((l["procedure_code"] for l in lines if l.get("procedure_code")), None)
        primary_dx = dxs[0]["diagnosis_code"] if dxs else None
        primary_dx_type = dxs[0]["diagnosis_type"] if dxs else None
        primary_pos = next((l["place_of_service"] for l in lines if l.get("place_of_service")), None)

        modifiers: list[str] = []
        for l in lines:
            for k in ("modifier1", "modifier2", "modifier3", "modifier4"):
                v = l.get(k)
                if v:
                    modifiers.append(v)

        rolled.append({
            "claim_id": cid,
            "primary_cpt": primary_cpt,
            "primary_dx": primary_dx,
            "primary_dx_type": primary_dx_type,
            "primary_pos": primary_pos,
            "procedure_codes": [l["procedure_code"] for l in lines if l.get("procedure_code")],
            "modifiers": modifiers,
            "revenue_codes": [l["revenue_code"] for l in lines if l.get("revenue_code")],
            "hipps_codes": [l["hipps_code"] for l in lines if l.get("hipps_code")],
            "tooth_numbers": [l["tooth_number"] for l in lines if l.get("tooth_number")],
            "ndc_drug_codes": [l["ndc_drug_code"] for l in lines if l.get("ndc_drug_code")],
            "lines_billed_sum": sum(float(l["billed_amount"] or 0) for l in lines),
            "lines_units_sum": sum(float(l["units"] or 0) for l in lines),
            "claim_lines_count": len(lines),
            "diagnoses_count": len(dxs),
            "diagnoses": [{"code": d["diagnosis_code"], "type": d["diagnosis_type"]} for d in dxs],
            "has_paperwork": bool(attachments_by_claim.get(cid)),
            "attachment_types": [a["report_type_code"] for a in attachments_by_claim.get(cid, [])],
            "has_certification": bool(certs_by_claim.get(cid)),
            "cert_types": [c["certification_type"] for c in certs_by_claim.get(cid, [])],
            "amounts": {a["amount_qualifier"]: float(a["amount"]) for a in amounts_by_claim.get(cid, [])},
            "home_care_episode": episodes_by_claim.get(cid),    # dict | None
            "transport_cert": transports_by_claim.get(cid),     # dict | None
        })

    rolled_df = pd.DataFrame(rolled)
    df = base_df.merge(rolled_df, on="claim_id", how="left")
    df = df.set_index("claim_id", drop=False)
    return df


async def _load_children(
    session: AsyncSession, claim_ids: list[int],
) -> tuple[
    dict[int, list[dict]],
    dict[int, list[dict]],
    dict[int, list[dict]],
    dict[int, list[dict]],
    dict[int, list[dict]],
    dict[int, dict],
    dict[int, dict],
]:
    """Bulk-load child tables for the given claim ids and bucket by claim_id."""
    # 1k chunks to keep param-count comfortable
    chunk_size = 1000

    lines = defaultdict(list)
    dxs = defaultdict(list)
    attachments = defaultdict(list)
    certs = defaultdict(list)
    amounts = defaultdict(list)
    episodes: dict[int, dict] = {}
    transports: dict[int, dict] = {}

    for start in range(0, len(claim_ids), chunk_size):
        chunk = claim_ids[start:start + chunk_size]

        line_rows = (await session.execute(text("""
            SELECT claim_id, line_number, procedure_code, procedure_code_qualifier,
                   modifier1, modifier2, modifier3, modifier4,
                   billed_amount, units, units_basis, place_of_service,
                   revenue_code, hipps_code, tooth_number, tooth_surfaces, ndc_drug_code
            FROM claim_lines WHERE claim_id = ANY(:ids) ORDER BY claim_id, line_number
        """), {"ids": chunk})).mappings().all()
        for r in line_rows:
            lines[r["claim_id"]].append(dict(r))

        dx_rows = (await session.execute(text("""
            SELECT claim_id, sequence_number, diagnosis_code, diagnosis_type, present_on_admission
            FROM diagnoses WHERE claim_id = ANY(:ids) ORDER BY claim_id, sequence_number
        """), {"ids": chunk})).mappings().all()
        for r in dx_rows:
            dxs[r["claim_id"]].append(dict(r))

        att_rows = (await session.execute(text("""
            SELECT claim_id, report_type_code, transmission_code, attachment_control_no
            FROM claim_attachments WHERE claim_id = ANY(:ids)
        """), {"ids": chunk})).mappings().all()
        for r in att_rows:
            attachments[r["claim_id"]].append(dict(r))

        cert_rows = (await session.execute(text("""
            SELECT claim_id, certification_type, structured_data
            FROM claim_certifications WHERE claim_id = ANY(:ids)
        """), {"ids": chunk})).mappings().all()
        for r in cert_rows:
            certs[r["claim_id"]].append(dict(r))

        amt_rows = (await session.execute(text("""
            SELECT claim_id, amount_qualifier, amount
            FROM claim_amounts WHERE claim_id = ANY(:ids)
        """), {"ids": chunk})).mappings().all()
        for r in amt_rows:
            amounts[r["claim_id"]].append(dict(r))

        ep_rows = (await session.execute(text("""
            SELECT claim_id, episode_start_date, episode_end_date, hipps_code,
                   oasis_assessment_date, visit_count, discipline_mix, is_lupa,
                   homebound_certified, plan_of_care_signed_date
            FROM home_care_episodes WHERE claim_id = ANY(:ids)
        """), {"ids": chunk})).mappings().all()
        for r in ep_rows:
            episodes[r["claim_id"]] = dict(r)

        tc_rows = (await session.execute(text("""
            SELECT claim_id, transport_miles, patient_weight_lbs, transport_reason_code,
                   round_trip, emergent, level_of_service
            FROM transport_certifications WHERE claim_id = ANY(:ids)
        """), {"ids": chunk})).mappings().all()
        for r in tc_rows:
            transports[r["claim_id"]] = dict(r)

    return (lines, dxs, attachments, certs, amounts, episodes, transports)


def _empty_corpus() -> pd.DataFrame:
    """Return an empty DataFrame with the union of expected columns. The
    builder still produces an empty feature matrix without complaining,
    which makes the cold-start unit tests well-defined."""
    cols = [
        "claim_id", "claim_number", "service_variant", "claim_subtype", "payer_id",
        "patient_id", "subscriber_id", "billing_provider_id", "rendering_provider_id",
        "referring_provider_id", "total_charge_amount", "facility_type_code",
        "frequency_code", "service_from_date", "service_to_date", "submission_date",
        "authorization_number", "referral_number", "previous_payer_claim_control_no",
        "variant_data", "payer_canonical_name", "payer_taxonomy", "patient_dob",
        "patient_gender", "billing_provider_taxonomy", "billing_provider_state",
        "billing_provider_npi", "rendering_provider_npi", "subscriber_cob",
        "subscriber_relationship", "subscriber_group", "denied",
        "primary_cpt", "primary_dx", "primary_dx_type", "primary_pos",
        "procedure_codes", "modifiers", "revenue_codes", "hipps_codes",
        "tooth_numbers", "ndc_drug_codes", "lines_billed_sum", "lines_units_sum",
        "claim_lines_count", "diagnoses_count", "diagnoses",
        "has_paperwork", "attachment_types", "has_certification", "cert_types",
        "amounts", "home_care_episode", "transport_cert",
    ]
    return pd.DataFrame({c: pd.Series(dtype="object") for c in cols})
