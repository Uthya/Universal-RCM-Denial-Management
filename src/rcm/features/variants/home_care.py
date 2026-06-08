"""Category M — home care variant (11 features) — 837I/home_care.

Home health denials cluster around: missing homebound certification,
late OASIS assessment, missing F2F encounter, LUPA (visit count < 5),
and recertification-episode handling. HIPPS code captures the case-mix
group that drives payment.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from rcm.features.categories._helpers import _as_date, _has_str
from rcm.features.categories.history import PatientHistorySnapshot
from rcm.features.constants import HOME_CARE_SKILLED_REVENUE
from rcm.features.variants._base import _ensure_str_list


def _episode_field(ep: Any, key: str, default: Any = None) -> Any:
    if not isinstance(ep, dict):
        return default
    v = ep.get(key)
    return v if v is not None else default


def _has_cert_type(cert_types: Any, target: str) -> int:
    if not isinstance(cert_types, list):
        return 0
    return int(any(_has_str(c) and str(c).lower() == target for c in cert_types))


def _has_ref_qualifier(var_data: Any, qualifier: str) -> int:
    """Check claim.variant_data['references'][qualifier]"""
    if not isinstance(var_data, dict):
        return 0
    refs = var_data.get("references") or {}
    return int(bool(refs.get(qualifier)))


def compute(
    df: pd.DataFrame,
    *,
    hipps_code_encoded: pd.Series | None = None,
    history_snapshot: PatientHistorySnapshot | None = None,
) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)

    episode_col = df.get("home_care_episode", pd.Series([None] * len(df), index=df.index))
    revenue_codes_col = df.get("revenue_codes", pd.Series([[]] * len(df), index=df.index))
    cert_types_col = df.get("cert_types", pd.Series([[]] * len(df), index=df.index))
    var_data_col = df.get("variant_data", pd.Series([{}] * len(df), index=df.index))
    svc_from_col = df.get("service_from_date", pd.Series([None] * len(df)))
    hipps_col = df.get("hipps_codes", pd.Series([[]] * len(df), index=df.index))
    cids = df.get("claim_id", pd.Series([None] * len(df)))

    # 1. hipps_code_encoded
    if hipps_code_encoded is not None:
        out["hipps_code_encoded"] = hipps_code_encoded.reindex(df.index, fill_value=0.0).astype("float32")
    else:
        # Placeholder: hash first HIPPS code to a stable float in [0,1)
        out["hipps_code_encoded"] = pd.Series(
            [float(hash(str((h or [None])[0])) % 1000) / 1000.0 if isinstance(h, list) and h else 0.0
             for h in hipps_col],
            index=df.index,
        ).astype("float32")

    # 2. episode_length_days
    out["episode_length_days"] = pd.Series(
        [(_as_date(_episode_field(ep, "episode_end_date")) - _as_date(_episode_field(ep, "episode_start_date"))).days
         if _episode_field(ep, "episode_start_date") and _episode_field(ep, "episode_end_date")
         else 0
         for ep in episode_col],
        index=df.index,
    ).astype("int16")

    # 3. revenue_code_count_skilled
    out["revenue_code_count_skilled"] = pd.Series(
        [sum(1 for rc in _ensure_str_list(rcs) if rc in HOME_CARE_SKILLED_REVENUE) for rcs in revenue_codes_col],
        index=df.index,
    ).astype("int16")

    # 4. visit_count_in_episode
    out["visit_count_in_episode"] = pd.Series(
        [int(_episode_field(ep, "visit_count", 0) or 0) for ep in episode_col],
        index=df.index,
    ).astype("int16")

    # 5. homebound_certification_present (CRC*75 captured)
    out["homebound_certification_present"] = pd.Series(
        [_has_cert_type(cts, "homebound") for cts in cert_types_col],
        index=df.index,
    ).astype("int8")

    # 6. oasis_within_5_days
    def _oasis_within(ep: Any) -> int:
        if not isinstance(ep, dict):
            return 0
        oasis = _as_date(ep.get("oasis_assessment_date"))
        start = _as_date(ep.get("episode_start_date"))
        if not oasis or not start:
            return 0
        # OASIS done within 5 days before or after episode start
        delta = abs((oasis - start).days)
        return int(delta <= 5)
    out["oasis_within_5_days"] = pd.Series(
        [_oasis_within(ep) for ep in episode_col],
        index=df.index,
    ).astype("int8")

    # 7. face_to_face_encounter_present (REF*9F captured)
    out["face_to_face_encounter_present"] = pd.Series(
        [_has_ref_qualifier(vd, "9F") for vd in var_data_col],
        index=df.index,
    ).astype("int8")

    # 8. physician_certification_present (REF*EW or signed POC)
    def _phys_cert(vd: Any, ep: Any) -> int:
        if _has_ref_qualifier(vd, "EW"):
            return 1
        if isinstance(ep, dict) and ep.get("plan_of_care_signed_date"):
            return 1
        return 0
    out["physician_certification_present"] = pd.Series(
        [_phys_cert(vd, ep) for vd, ep in zip(var_data_col, episode_col)],
        index=df.index,
    ).astype("int8")

    # 9. is_lupa (visit_count < 5)
    out["is_lupa"] = (out["visit_count_in_episode"].fillna(0) > 0) & (out["visit_count_in_episode"] < 5)
    out["is_lupa"] = out["is_lupa"].astype("int8")

    # 10. discipline_count_total (sum of PT/OT/ST/SN counts from discipline_mix)
    def _discipline_total(ep: Any) -> int:
        if not isinstance(ep, dict):
            return 0
        mix = ep.get("discipline_mix") or {}
        if not isinstance(mix, dict):
            return 0
        return sum(int(v) for v in mix.values() if isinstance(v, (int, float)))
    out["discipline_count_total"] = pd.Series(
        [_discipline_total(ep) for ep in episode_col],
        index=df.index,
    ).astype("int16")

    # 11. is_recertification_episode — needs MV lookup. Default 0 when no snapshot.
    if history_snapshot is not None:
        # Proxy: patient had >= 1 prior claim with same provider per is_new_patient_to_provider
        out["is_recertification_episode"] = pd.Series(
            [int(not history_snapshot.by_claim.get(cid, {}).get("prior_with_provider_count", 0) == 0)
             for cid in cids],
            index=df.index,
        ).astype("int8")
    else:
        out["is_recertification_episode"] = pd.Series(0, index=df.index, dtype="int8")

    return out
