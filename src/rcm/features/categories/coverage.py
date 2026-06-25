"""Category A — coverage / eligibility (10 features; CR-104 retired
is_secondary_claim, cross_payer_count_for_patient; CR-126B added
payer_overall_denial_rate_recent_2k_smoothed and
payer_overall_denial_rate_90d_smoothed)."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from rcm.features.categories._helpers import _as_date, _has_str
from rcm.features.categories.availability import RefDataLookup


# CR-126B Bayesian smoothing constants. Tuned against the empirical lifetime
# global denial prior measured in CR-124. Changing these does NOT require a
# migration — the MVs store raw counts, and smoothing happens here.
_SMOOTHING_ALPHA: float = 25.0
_SMOOTHING_PRIOR: float = 0.2772


def _smooth_recency(
    snap_dict: dict, key: tuple, alpha: float = _SMOOTHING_ALPHA, prior: float = _SMOOTHING_PRIOR,
) -> float:
    """Bayesian-smoothed rate: (denied + α·prior) / (volume + α).

    snap_dict stores (denied_count, volume). Missing key → return prior
    (defensive default for unseen payers; matches CR-125 design)."""
    entry = snap_dict.get(key)
    if entry is None:
        return float(prior)
    denied, volume = entry
    return float((denied + alpha * prior) / (volume + alpha))


_COB_MAP = {None: 0, "": 0, "P": 1, "S": 2, "T": 3}


def _age_at(dob: Any, svc_from: Any) -> int:
    d = _as_date(dob)
    s = _as_date(svc_from)
    if not d or not s:
        return 0
    age = s.year - d.year - ((s.month, s.day) < (d.month, d.day))
    return max(age, 0)


def _age_band(age: int) -> int:
    if age <= 0:
        return 0
    if age < 18:
        return 0
    if age < 40:
        return 1
    if age < 65:
        return 2
    return 3


def compute(
    df: pd.DataFrame,
    *,
    ref: RefDataLookup | None = None,
    payer_overall_denial: dict[int, float] | None = None,
    payer_taxonomy_encoded: pd.Series | None = None,
    payer_recent_2k: dict[tuple[int, str, str], tuple[int, int]] | None = None,
    payer_90d: dict[tuple[int, str, str], tuple[int, int]] | None = None,
) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    ref = ref or RefDataLookup()
    payer_overall_denial = payer_overall_denial or {}
    payer_recent_2k = payer_recent_2k or {}
    payer_90d = payer_90d or {}

    ages = [
        _age_at(dob, svc)
        for dob, svc in zip(df.get("patient_dob"), df.get("service_from_date"))
    ]
    out["patient_age_at_service"] = pd.Series(ages, index=df.index).astype("int32")
    out["patient_age_band"] = pd.Series([_age_band(a) for a in ages], index=df.index).astype("int8")

    # Age / gender vs procedure: default 1 when no ref data
    cpts = df.get("primary_cpt", pd.Series([None] * len(df)))
    genders = df.get("patient_gender", pd.Series([None] * len(df)))

    def age_ok(cpt: Any, age: int) -> int:
        if not _has_str(cpt):
            return 1
        meta = ref.procedure_metadata.get(str(cpt))
        if not meta:
            return 1
        lo = meta.get("age_min", 0)
        hi = meta.get("age_max", 150)
        return int(lo <= age <= hi)

    def gender_ok(cpt: Any, gender: Any) -> int:
        if not _has_str(cpt):
            return 1
        meta = ref.procedure_metadata.get(str(cpt))
        if not meta:
            return 1
        restriction = meta.get("gender_restriction")
        if restriction is None or not _has_str(gender):
            return 1
        return int(str(gender).upper() == str(restriction).upper())

    out["patient_age_vs_procedure_valid"] = pd.Series(
        [age_ok(c, a) for c, a in zip(cpts, ages)], index=df.index,
    ).astype("int8")
    out["patient_gender_vs_procedure_valid"] = pd.Series(
        [gender_ok(c, g) for c, g in zip(cpts, genders)], index=df.index,
    ).astype("int8")

    # COB
    cob_raw = df.get("subscriber_cob", pd.Series([None] * len(df)))
    out["cob_position_encoded"] = pd.Series(
        [_COB_MAP.get(v if _has_str(v) else None, 0) for v in cob_raw], index=df.index,
    ).astype("int8")
    # has_secondary_payer derived from cob_position_encoded>1 (no separate
    # subscriber roll-up in current data)
    out["has_secondary_payer"] = (out["cob_position_encoded"] > 1).astype("int8")

    # MV-backed: payer_overall_denial_rate
    payer_ids = df.get("payer_id", pd.Series([None] * len(df)))
    out["payer_overall_denial_rate"] = pd.Series(
        [float(payer_overall_denial.get(pid, 0.0)) if pid is not None else 0.0 for pid in payer_ids],
        index=df.index,
    ).astype("float32")

    # Encoded payer taxonomy from the joint-encoder Cat K bridge
    if payer_taxonomy_encoded is not None:
        out["payer_taxonomy_encoded"] = payer_taxonomy_encoded.astype("float32").reindex(df.index, fill_value=0.0)
    else:
        out["payer_taxonomy_encoded"] = pd.Series(0.0, index=df.index, dtype="float32")

    # CR-126B: Bayesian-smoothed recency denial rates. Predict time reads
    # from mv_payer_denial_rates_{recent_2k,90d} via joint_snap; smoothing
    # applied here. Train time overrides with leakage-safe per-row values
    # via FeatureBuilder._assemble's safe_recency_rates path.
    variants = df.get("service_variant", pd.Series([None] * len(df), index=df.index))
    subtypes = df.get("claim_subtype",   pd.Series([None] * len(df), index=df.index))
    keys = [
        (pid, str(v), str(s)) if (pid is not None and _has_str(v) and _has_str(s)) else None
        for pid, v, s in zip(df.get("payer_id", pd.Series([None] * len(df))), variants, subtypes)
    ]
    out["payer_overall_denial_rate_recent_2k_smoothed"] = pd.Series(
        [_smooth_recency(payer_recent_2k, k) if k is not None else _SMOOTHING_PRIOR for k in keys],
        index=df.index,
    ).astype("float32")
    out["payer_overall_denial_rate_90d_smoothed"] = pd.Series(
        [_smooth_recency(payer_90d, k) if k is not None else _SMOOTHING_PRIOR for k in keys],
        index=df.index,
    ).astype("float32")

    return out
