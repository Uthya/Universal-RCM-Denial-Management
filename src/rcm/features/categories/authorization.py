"""Category B — authorization / referral (7 features).

All ref-data-dependent features default to safe values when payer_policies
is empty: the model interprets them as "no known requirement, so no risk".
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from rcm.features.categories._helpers import _has_str
from rcm.features.categories.availability import RefDataLookup


def _payer_policy_applies_to_cpt(policies: list[dict], policy_type: str,
                                  cpt: Any, variant: Any) -> bool:
    """Find any matching policy of `policy_type` for this CPT × variant."""
    if not policies or not _has_str(cpt):
        return False
    cpt = str(cpt)
    for p in policies:
        if p.get("policy_type") != policy_type:
            continue
        codes = p.get("applies_to_codes")
        if codes and cpt not in codes:
            continue
        sv = p.get("service_variant")
        if sv and _has_str(variant) and sv != variant:
            continue
        return True
    return False


def _payer_policy_referral_required(policies: list[dict], claim_subtype: Any) -> bool:
    if not policies:
        return False
    for p in policies:
        if p.get("policy_type") != "referral_required":
            continue
        sub = p.get("claim_subtype")
        if sub and _has_str(claim_subtype) and sub != claim_subtype:
            continue
        return True
    return False


def _payer_policy_auth_regex(policies: list[dict]) -> str | None:
    if not policies:
        return None
    for p in policies:
        if p.get("policy_type") != "prior_auth":
            continue
        rule = p.get("structured_rule") or {}
        rx = rule.get("auth_format_regex")
        if rx:
            return rx
    return None


def compute(df: pd.DataFrame, *, ref: RefDataLookup | None = None) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    ref = ref or RefDataLookup()

    auths = df.get("authorization_number", pd.Series([None] * len(df)))
    refs = df.get("referral_number", pd.Series([None] * len(df)))
    cpts = df.get("primary_cpt", pd.Series([None] * len(df)))
    payers = df.get("payer_canonical_name", pd.Series([None] * len(df)))
    subtypes = df.get("claim_subtype", pd.Series([None] * len(df)))
    variants = df.get("service_variant", pd.Series([None] * len(df)))

    has_auth = [int(_has_str(a)) for a in auths]
    has_ref = [int(_has_str(r)) for r in refs]
    out["has_prior_authorization"] = pd.Series(has_auth, index=df.index).astype("int8")
    out["has_referral"] = pd.Series(has_ref, index=df.index).astype("int8")

    auth_required = []
    referral_required = []
    auth_format_valid = []
    for cpt, payer, subtype, variant, auth_no in zip(cpts, payers, subtypes, variants, auths):
        polys = ref.payer_policies_by_payer.get(str(payer), []) if _has_str(payer) else []

        required = _payer_policy_applies_to_cpt(polys, "prior_auth", cpt, variant)
        auth_required.append(int(required))

        ref_required = _payer_policy_referral_required(polys, subtype)
        referral_required.append(int(ref_required))

        rx = _payer_policy_auth_regex(polys)
        if rx is None:
            auth_format_valid.append(1)
        elif not _has_str(auth_no):
            auth_format_valid.append(1)  # no auth at all is a separate signal
        else:
            try:
                auth_format_valid.append(int(bool(re.match(rx, str(auth_no)))))
            except re.error:
                auth_format_valid.append(1)

    out["auth_required_for_cpt_payer"] = pd.Series(auth_required, index=df.index).astype("int8")
    out["auth_missing_when_required"] = (
        (out["auth_required_for_cpt_payer"] == 1) & (out["has_prior_authorization"] == 0)
    ).astype("int8")
    out["referral_required_for_specialty"] = pd.Series(referral_required, index=df.index).astype("int8")
    out["referral_missing_when_required"] = (
        (out["referral_required_for_specialty"] == 1) & (out["has_referral"] == 0)
    ).astype("int8")
    out["auth_number_format_valid"] = pd.Series(auth_format_valid, index=df.index).astype("int8")
    return out
