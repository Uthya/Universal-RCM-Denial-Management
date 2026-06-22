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
    include_freq7: bool = False,
) -> pd.DataFrame:
    """Return the labelled training corpus as a pandas DataFrame.

    Filters:
        service_variant / claim_subtype — restrict to a single per-variant pool
        since / until                   — date window on service_from_date
        limit                           — cap row count (dev/test convenience)
        include_freq7                   — CR-120A: add freq=7 replacements to
                                          the corpus with their OWN remit
                                          labels. Default False preserves the
                                          pre-CR-120 freq=1-only training.

    The output DataFrame has one row per claim plus columns:
        denied (int 0/1), payer_canonical_name (str), primary_cpt (str|None),
        primary_dx (str|None), modifiers (list[str]), revenue_codes (list[str]),
        hipps_codes (list[str]), tooth_numbers (list[str]), procedure_codes
        (list[str]), place_of_service (str|None), claim_lines_count (int),
        diagnoses_count (int), has_paperwork (bool), has_certification (bool),
        cert_types (list[str]), amounts (dict[str, float]), home_care_*
        (when present), transport_* (when present).
    """
    params: dict[str, Any] = {}

    sv_filter = "" if service_variant is None else "AND c.service_variant = :service_variant"
    cs_filter = "" if claim_subtype is None else "AND c.claim_subtype = :claim_subtype"
    since_filter = "" if since is None else "AND c.service_from_date >= :since"
    until_filter = "" if until is None else "AND c.service_from_date <= :until"
    if service_variant is not None:
        params["service_variant"] = service_variant
    if claim_subtype is not None:
        params["claim_subtype"] = claim_subtype
    if since is not None:
        params["since"] = since
    if until is not None:
        params["until"] = until

    limit_sql = f"LIMIT {int(limit)}" if limit else ""

    # CR-071: Strategy A1 ("any denial wins") — denial propagated from a
    # freq=7 replacement back to its freq=1 original via (claim_number,
    # payer_id). Read-only; computed at query time from live claims +
    # remittance_claims. mv_claim_labels itself is unchanged. payer isolation
    # preserved via `IS NOT DISTINCT FROM` (NULL/NULL is a match, NULL/non-NULL
    # is not). Affects ~1,928 mv rows on the current corpus; per the
    # CR-071 label-semantics validation report.
    #
    # CR-120A: when include_freq7=True we bypass mv_claim_labels (which has a
    # baked-in freq=1/NULL filter) and label-compute inline from
    # remittance_claims so freq=7 rows can enter training with their OWN
    # labels. freq=1 rows still receive CR-071 descendant propagation; freq=7
    # rows ignore it (their label IS their own outcome).
    freq_codes = "('1','7')" if include_freq7 else "('1')"
    base_sql = text(f"""
        WITH labeled AS (
            SELECT c.id AS claim_id, c.frequency_code,
                   bool_or(rc.claim_status_code = '4')                                  AS any_denied,
                   bool_or(rc.claim_status_code IN ('1','2','3','19','20'))             AS any_approved
            FROM claims c
            LEFT JOIN remittance_claims rc ON rc.claim_id = c.id
            WHERE c.deleted_at IS NULL
              AND (c.frequency_code IS NULL OR c.frequency_code IN {freq_codes})
              AND c.service_from_date IS NOT NULL
              {sv_filter} {cs_filter} {since_filter} {until_filter}
            GROUP BY c.id, c.frequency_code
            HAVING (
                bool_or(rc.claim_status_code = '4') OR
                bool_or(rc.claim_status_code IN ('1','2','3','19','20'))
            )
        ),
        descendant_denial AS (
            SELECT DISTINCT r.claim_number, r.payer_id
            FROM claims r
            JOIN remittance_claims rc
                 ON rc.claim_id = r.id AND rc.claim_status_code = '4'
            WHERE r.frequency_code = '7'
              AND r.deleted_at IS NULL
        )
        SELECT
            {_CLAIM_COLUMNS},
            CASE
                WHEN labeled.any_denied THEN 1
                WHEN labeled.frequency_code <> '7' AND dd.claim_number IS NOT NULL THEN 1
                ELSE 0
            END AS denied
        FROM labeled
        JOIN claims c        ON c.id = labeled.claim_id
        LEFT JOIN descendant_denial dd
             ON dd.claim_number = c.claim_number
            AND dd.payer_id IS NOT DISTINCT FROM c.payer_id
        LEFT JOIN payers py  ON py.id = c.payer_id
        LEFT JOIN patients pt ON pt.id = c.patient_id
        LEFT JOIN providers bpr ON bpr.id = c.billing_provider_id
        LEFT JOIN providers rpr ON rpr.id = c.rendering_provider_id
        LEFT JOIN subscribers sub ON sub.id = c.subscriber_id
        ORDER BY labeled.claim_id
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


async def load_original_snapshots(
    session: AsyncSession, claim_ids: list[int],
) -> pd.DataFrame:
    """CR-117 Stage 1 — per-replacement original snapshot.

    For each claim_id in ``claim_ids`` (typically a freq=7 replacement),
    resolve its corresponding freq=1 original via
    ``(claim_number, payer_id IS NOT DISTINCT FROM)`` and return a snapshot
    of the fields the Stage-1 lifecycle features need.

    Leakage guard: the original's remittance row is restricted to
    ``remittance_date < replacement.service_from_date`` (strict-<). For
    claims whose original has not been adjudicated yet (or whose remit
    landed on the same day) we return the original's structural fields
    but leave ``original_claim_status_code`` / ``original_remittance_date``
    / ``original_top_carc`` / ``original_top_carc_bucket`` NULL — the
    feature compute treats this as "no prior denial signal".

    Self-match prevention: the join excludes ``o.id = ch.child_id`` so a
    freq=1 claim passed in by accident does not match itself.

    Tie-break on duplicate originals (CHANGELOG note: 157 of 95,256
    ``(claim_number, payer_id)`` groups have ≥2 originals): earliest
    ``service_from_date`` then earliest ``id``.

    Returns a DataFrame indexed by ``child_id`` with columns:
        original_id                       Int64 | NaN
        original_claim_status_code        str   | NaN
        original_remittance_date          date  | NaT
        original_authorization_number     str   | NaN
        original_referral_number          str   | NaN
        original_top_carc                 str   | NaN
        original_top_carc_bucket          str   | NaN
        original_total_charge_amount      float | NaN
        original_modifiers                list[str] (Stage 2)
        original_procedure_codes          list[str] (Stage 2)
        original_diagnosis_codes          list[str] (Stage 2)
        original_claim_lines_count        int (Stage 2)
    Rows for claim_ids without any resolvable original are present with
    all-NaN values (so callers can ``reindex(claim_ids)`` safely).
    """
    if not claim_ids:
        return pd.DataFrame()

    rows = (await session.execute(text("""
        WITH children AS (
            SELECT c.id AS child_id, c.claim_number, c.payer_id,
                   c.service_from_date AS child_svc_date
            FROM claims c
            WHERE c.id = ANY(:ids) AND c.deleted_at IS NULL
        ),
        candidates AS (
            SELECT ch.child_id,
                   o.id                    AS original_id,
                   o.authorization_number  AS original_authorization_number,
                   o.referral_number       AS original_referral_number,
                   o.total_charge_amount   AS original_total_charge_amount,
                   o.service_from_date     AS original_service_from_date,
                   ch.child_svc_date
            FROM children ch
            JOIN claims o ON o.claim_number = ch.claim_number
                         AND o.payer_id IS NOT DISTINCT FROM ch.payer_id
                         AND o.frequency_code = '1'
                         AND o.deleted_at IS NULL
                         AND o.id <> ch.child_id
        ),
        chosen AS (
            SELECT DISTINCT ON (child_id) *
            FROM candidates
            ORDER BY child_id, original_service_from_date NULLS LAST, original_id
        ),
        best_remit AS (
            SELECT DISTINCT ON (ch.child_id)
                   ch.child_id, ch.original_id,
                   ch.original_authorization_number, ch.original_referral_number,
                   ch.original_total_charge_amount,
                   rc.id                  AS remit_id,
                   rc.claim_status_code   AS original_claim_status_code,
                   rc.remittance_date     AS original_remittance_date
            FROM chosen ch
            LEFT JOIN remittance_claims rc
                   ON rc.claim_id = ch.original_id
                  AND rc.remittance_date IS NOT NULL
                  AND rc.remittance_date < ch.child_svc_date
            ORDER BY ch.child_id, rc.remittance_date DESC NULLS LAST, rc.id DESC
        )
        SELECT br.child_id,
               br.original_id,
               br.original_authorization_number,
               br.original_referral_number,
               br.original_total_charge_amount,
               br.original_claim_status_code,
               br.original_remittance_date,
               (
                   SELECT adj.adjustment_reason_code
                   FROM adjustments adj
                   WHERE adj.remittance_claim_id = br.remit_id
                   ORDER BY abs(adj.adjustment_amount) DESC NULLS LAST, adj.id
                   LIMIT 1
               ) AS original_top_carc
        FROM best_remit br
        ORDER BY br.child_id
    """), {"ids": claim_ids})).mappings().all()

    if not rows:
        out = pd.DataFrame(index=pd.Index(claim_ids, name="child_id"))
        for c in (
            "original_id", "original_authorization_number",
            "original_referral_number", "original_total_charge_amount",
            "original_claim_status_code", "original_remittance_date",
            "original_top_carc", "original_top_carc_bucket",
            "original_modifiers", "original_procedure_codes",
            "original_diagnosis_codes", "original_claim_lines_count",
        ):
            out[c] = pd.Series(dtype="object", index=out.index)
        return out

    df = pd.DataFrame(rows).set_index("child_id")

    # CR-118 Stage 2 — bulk-load the original's claim_lines + diagnoses so we
    # can compute the correction-delta features. One SQL per child table
    # (mirrors `_load_children`'s chunked pattern). Keyed by the resolved
    # `original_id`, which is at most one per child row.
    original_ids = [int(oid) for oid in df["original_id"].dropna().unique().tolist()]
    orig_procs: dict[int, set[str]] = {}
    orig_mods: dict[int, set[str]] = {}
    orig_dxs: dict[int, set[str]] = {}
    orig_line_count: dict[int, int] = {}
    if original_ids:
        # Lines (procedure_code + 4 modifier slots + count). Same column
        # shape as `_load_children`'s line query so the rollup logic is
        # familiar to anyone reading both.
        chunk_size = 1000
        for start in range(0, len(original_ids), chunk_size):
            chunk = original_ids[start:start + chunk_size]
            line_rows = (await session.execute(text("""
                SELECT claim_id, procedure_code,
                       modifier1, modifier2, modifier3, modifier4
                FROM claim_lines
                WHERE claim_id = ANY(:ids)
            """), {"ids": chunk})).mappings().all()
            for r in line_rows:
                cid = int(r["claim_id"])
                orig_line_count[cid] = orig_line_count.get(cid, 0) + 1
                pc = r.get("procedure_code")
                if pc:
                    orig_procs.setdefault(cid, set()).add(str(pc))
                for k in ("modifier1", "modifier2", "modifier3", "modifier4"):
                    m = r.get(k)
                    if m and str(m).strip():
                        orig_mods.setdefault(cid, set()).add(str(m).strip())
            dx_rows = (await session.execute(text("""
                SELECT claim_id, diagnosis_code
                FROM diagnoses
                WHERE claim_id = ANY(:ids) AND diagnosis_code IS NOT NULL
            """), {"ids": chunk})).mappings().all()
            for r in dx_rows:
                cid = int(r["claim_id"])
                orig_dxs.setdefault(cid, set()).add(str(r["diagnosis_code"]))

    def _lookup(oid_cell, bag: dict[int, set[str]]) -> list[str] | None:
        if pd.isna(oid_cell):
            return None
        return sorted(bag.get(int(oid_cell), set()))

    def _lookup_count(oid_cell) -> int | None:
        if pd.isna(oid_cell):
            return None
        return orig_line_count.get(int(oid_cell), 0)

    df["original_modifiers"]         = df["original_id"].map(lambda v: _lookup(v, orig_mods))
    df["original_procedure_codes"]   = df["original_id"].map(lambda v: _lookup(v, orig_procs))
    df["original_diagnosis_codes"]   = df["original_id"].map(lambda v: _lookup(v, orig_dxs))
    df["original_claim_lines_count"] = df["original_id"].map(_lookup_count)

    # Resolve CARC → canonical bucket using the CR-092 single-source-of-truth
    # mapping. Lazy import keeps this module DB-import-light.
    from rcm.ml.denial_buckets import carc_bucket  # noqa: PLC0415
    unique_carcs = [c for c in df["original_top_carc"].dropna().unique().tolist() if c]
    bucket_lookup: dict[str, str] = {}
    for c in unique_carcs:
        bucket_lookup[c] = await carc_bucket(c)
    df["original_top_carc_bucket"] = df["original_top_carc"].map(bucket_lookup)

    # Ensure every requested claim_id has a row (NaN for missing originals)
    df = df.reindex(pd.Index(claim_ids, name="child_id"))
    return df


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
