"""Category M — therapy variant (9 features) — 837P/therapy.

Therapy denials cluster around: discipline modifier presence (GP/GO/GN),
KX threshold attestation, plan-of-care attachment + recency, Medicare
therapy cap proximity, evaluation vs treatment CPT distinction.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from rcm.features.categories._helpers import _as_date, _has_str, _safe_float
from rcm.features.categories.history import PatientHistorySnapshot
from rcm.features.constants import (
    MEDICARE_THERAPY_CAP_2024,
    THERAPY_DISCIPLINE_MODIFIERS,
    THERAPY_MAINTENANCE_MODIFIER,
    THERAPY_THRESHOLD_MODIFIER,
)
from rcm.features.variants._base import (
    _ensure_str_list,
    _has_any_modifier,
    _has_modifier,
)


# Evaluation CPT codes (PT/OT eval & re-eval)
_EVAL_CPTS: frozenset[int] = frozenset({97161, 97162, 97163, 97164,  # PT
                                         97165, 97166, 97167, 97168,  # OT
                                         92521, 92522, 92523, 92524})  # ST


def _discipline_for(modifiers: list[str]) -> str | None:
    """Return GP/GO/GN/KH if present; first match wins."""
    for m in modifiers:
        up = str(m).upper()
        if up in THERAPY_DISCIPLINE_MODIFIERS or up == THERAPY_MAINTENANCE_MODIFIER:
            return up
    return None


def _cap_proximity(annual_charges_for_patient: float) -> float:
    """YTD therapy charges / Medicare therapy cap. Clipped to [0, 5.0]
    so absurd values from bad upstream data don't blow up the model."""
    if not annual_charges_for_patient or annual_charges_for_patient <= 0:
        return 0.0
    return min(float(annual_charges_for_patient) / MEDICARE_THERAPY_CAP_2024, 5.0)


def _plan_of_care_recent(svc_from: Any, poc_date: Any) -> int:
    s = _as_date(svc_from)
    p = _as_date(poc_date)
    if not s or not p:
        return 0
    return int((s - p).days <= 30 and (s - p).days >= 0)


def compute(
    df: pd.DataFrame,
    *,
    discipline_modifier_encoded: pd.Series | None = None,
    history_snapshot: PatientHistorySnapshot | None = None,
) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)

    modifiers_col = df.get("modifiers", pd.Series([[]] * len(df), index=df.index))
    cpts_col = df.get("primary_cpt", pd.Series([None] * len(df)))
    attachments_col = df.get("attachment_types", pd.Series([[]] * len(df), index=df.index))
    var_data_col = df.get("variant_data", pd.Series([{}] * len(df), index=df.index))
    svc_from_col = df.get("service_from_date", pd.Series([None] * len(df)))
    annual_charges = df.get("annual_charges_for_patient", pd.Series([0.0] * len(df), index=df.index))
    if "annual_charges_for_patient" not in df.columns:
        # Look up from snapshot if not yet in df (history runs separately)
        if history_snapshot is not None:
            annual_charges = pd.Series(
                [float(history_snapshot.by_claim.get(cid, {}).get("annual_charges_for_patient", 0.0))
                 for cid in df.get("claim_id", pd.Series([None] * len(df)))],
                index=df.index,
            )

    # Pre-compute lists once
    modifiers_lists = [_ensure_str_list(m) for m in modifiers_col]

    # 1. discipline_modifier_encoded — placeholder target encoding;
    #    real training-time encoder is fit by Cat K when the column is
    #    promoted. For this phase we emit a hash-like int (0 if none).
    _DISCIPLINE_INT = {"GP": 1, "GO": 2, "GN": 3, "KH": 4}
    if discipline_modifier_encoded is not None:
        out["discipline_modifier_encoded"] = discipline_modifier_encoded.reindex(df.index, fill_value=0.0).astype("float32")
    else:
        out["discipline_modifier_encoded"] = pd.Series(
            [float(_DISCIPLINE_INT.get(_discipline_for(ms) or "", 0)) for ms in modifiers_lists],
            index=df.index,
        ).astype("float32")

    # 2. kx_modifier_present
    out["kx_modifier_present"] = pd.Series(
        [int(_has_modifier(ms, THERAPY_THRESHOLD_MODIFIER)) for ms in modifiers_lists],
        index=df.index,
    ).astype("int8")

    # 3. cap_proximity (YTD therapy charges / Medicare cap)
    out["cap_proximity"] = pd.Series(
        [_cap_proximity(_safe_float(c)) for c in annual_charges],
        index=df.index,
    ).astype("float32")

    # 4. plan_of_care_present (PWK with type CT or PN)
    out["plan_of_care_present"] = pd.Series(
        [int(any(_has_str(t) and str(t).upper() in ("CT", "PN")
                 for t in (a if isinstance(a, list) else [])))
         for a in attachments_col],
        index=df.index,
    ).astype("int8")

    # 5. plan_of_care_recent (DTP*090 stored in variant_data)
    poc_dates = []
    for vd in var_data_col:
        if isinstance(vd, dict):
            poc_dates.append(vd.get("plan_of_care_start_date"))
        else:
            poc_dates.append(None)
    out["plan_of_care_recent"] = pd.Series(
        [_plan_of_care_recent(s, p) for s, p in zip(svc_from_col, poc_dates)],
        index=df.index,
    ).astype("int8")

    # 6. kh_modifier (maintenance therapy flag)
    out["kh_modifier"] = pd.Series(
        [int(_has_modifier(ms, THERAPY_MAINTENANCE_MODIFIER)) for ms in modifiers_lists],
        index=df.index,
    ).astype("int8")

    # 7. evaluation_vs_treatment (1 if eval CPT, else 0)
    def _is_eval(cpt: Any) -> int:
        if not _has_str(cpt):
            return 0
        try:
            return int(int(str(cpt)) in _EVAL_CPTS)
        except ValueError:
            return 0
    out["evaluation_vs_treatment"] = pd.Series(
        [_is_eval(c) for c in cpts_col], index=df.index,
    ).astype("int8")

    # 8. is_maintenance_therapy (alias of kh_modifier for variant-aware modeling)
    out["is_maintenance_therapy"] = out["kh_modifier"].astype("int8")

    # 9. therapy_sessions_ytd — from patient history snapshot
    #    Uses claims_in_last_365d as a proxy (no separate therapy-only window today;
    #    when therapy-specific MV lands, switch to that).
    if history_snapshot is not None:
        cids = df.get("claim_id", pd.Series([None] * len(df)))
        out["therapy_sessions_ytd"] = pd.Series(
            [int(history_snapshot.by_claim.get(cid, {}).get("claims_in_last_365d", 0))
             for cid in cids],
            index=df.index,
        ).astype("int16")
    else:
        out["therapy_sessions_ytd"] = pd.Series(0, index=df.index, dtype="int16")

    return out
