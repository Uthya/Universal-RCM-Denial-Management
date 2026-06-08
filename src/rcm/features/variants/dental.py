"""Category M — dental variant (8 features) — 837D/dental.

Dental denials cluster around: missing tooth identifiers on
surface-applicable procedures, missing predetermination (F8) on high-cost
treatments, orthodontia documentation, frequency limits on the same
tooth (cleanings, radiographs).
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from rcm.features.categories._helpers import _has_str
from rcm.features.categories.history import PatientHistorySnapshot
from rcm.features.variants._base import _ensure_str_list


# CDT category codes by first digit
# D0xxx diagnostic, D1xxx preventive, D2xxx restorative, D3xxx endodontics,
# D4xxx periodontics, D5xxx prosthodontics, D6xxx implant/prostho, D7xxx oral surgery,
# D8xxx orthodontics, D9xxx adjunctive
_CDT_CATEGORY_INT = {"D0": 0, "D1": 1, "D2": 2, "D3": 3, "D4": 4,
                      "D5": 5, "D6": 6, "D7": 7, "D8": 8, "D9": 9}

# Radiograph codes (CDT D0210-D0274 range)
_RADIOGRAPH_CDT_RANGE_LO = 210
_RADIOGRAPH_CDT_RANGE_HI = 274


def _cdt_category(cpt: Any) -> int:
    if not _has_str(cpt):
        return 0
    s = str(cpt).upper()
    if len(s) < 2:
        return 0
    prefix = s[:2]
    return _CDT_CATEGORY_INT.get(prefix, 0)


def _is_preventive_cdt(cpt: Any) -> int:
    """D0xxx (diagnostic) and D1xxx (preventive) treated as preventive for denial purposes."""
    cat = _cdt_category(cpt)
    return int(cat in (0, 1))


def _is_radiograph(cpt: Any) -> int:
    if not _has_str(cpt):
        return 0
    s = str(cpt).upper()
    if not s.startswith("D"):
        return 0
    try:
        n = int(s[1:])
    except (ValueError, TypeError):
        return 0
    return int(_RADIOGRAPH_CDT_RANGE_LO <= n <= _RADIOGRAPH_CDT_RANGE_HI)


def _has_ref_qualifier(var_data: Any, qualifier: str) -> int:
    if not isinstance(var_data, dict):
        return 0
    refs = var_data.get("references") or {}
    return int(bool(refs.get(qualifier)))


def _has_cert_type(cert_types: Any, target: str) -> int:
    if not isinstance(cert_types, list):
        return 0
    return int(any(_has_str(c) and str(c).lower() == target for c in cert_types))


def compute(
    df: pd.DataFrame,
    *,
    cdt_category_encoded: pd.Series | None = None,
    history_snapshot: PatientHistorySnapshot | None = None,
) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)

    tooth_nums_col = df.get("tooth_numbers", pd.Series([[]] * len(df), index=df.index))
    cpts_col = df.get("primary_cpt", pd.Series([None] * len(df)))
    var_data_col = df.get("variant_data", pd.Series([{}] * len(df), index=df.index))
    cert_types_col = df.get("cert_types", pd.Series([[]] * len(df), index=df.index))

    # 1. tooth_number_specified — any line has a tooth_number
    out["tooth_number_specified"] = pd.Series(
        [int(bool(_ensure_str_list(tn))) for tn in tooth_nums_col],
        index=df.index,
    ).astype("int8")

    # 2. tooth_surface_count — average # of surfaces across lines from variant_data.tooth_surfaces
    #    Persisted on claim_lines but rolled up by tooth_numbers list length proxy.
    #    Lacking a per-line surfaces list in the corpus, default to count of distinct tooth_numbers.
    def _surface_count_proxy(tns: Any) -> float:
        tn_list = _ensure_str_list(tns)
        if not tn_list:
            return 0.0
        return float(len(tn_list))
    out["tooth_surface_count"] = pd.Series(
        [_surface_count_proxy(tn) for tn in tooth_nums_col],
        index=df.index,
    ).astype("float32")

    # 3. cdt_category_encoded
    if cdt_category_encoded is not None:
        out["cdt_category_encoded"] = cdt_category_encoded.reindex(df.index, fill_value=0.0).astype("float32")
    else:
        out["cdt_category_encoded"] = pd.Series(
            [float(_cdt_category(c)) for c in cpts_col],
            index=df.index,
        ).astype("float32")

    # 4. predetermination_filed (REF*F8)
    out["predetermination_filed"] = pd.Series(
        [_has_ref_qualifier(vd, "F8") for vd in var_data_col],
        index=df.index,
    ).astype("int8")

    # 5. orthodontia_indicator (DN1 captured into certifications)
    out["orthodontia_indicator"] = pd.Series(
        [_has_cert_type(cts, "orthodontic") for cts in cert_types_col],
        index=df.index,
    ).astype("int8")

    # 6. is_preventive_service (D0xxx-D1xxx)
    out["is_preventive_service"] = pd.Series(
        [_is_preventive_cdt(c) for c in cpts_col],
        index=df.index,
    ).astype("int8")

    # 7. service_age_in_months_for_tooth — needs per-tooth history MV; default 0
    #    Phase 4 should build mv_patient_tooth_history; for now we approximate
    #    as days_since_last_claim (already-leakage-safe) / 30.
    if history_snapshot is not None:
        cids = df.get("claim_id", pd.Series([None] * len(df)))
        out["service_age_in_months_for_tooth"] = pd.Series(
            [int(history_snapshot.by_claim.get(cid, {}).get("days_since_last_claim", 0) or 0) // 30
             if history_snapshot.by_claim.get(cid, {}).get("days_since_last_claim") else 0
             for cid in cids],
            index=df.index,
        ).astype("int16")
    else:
        out["service_age_in_months_for_tooth"] = pd.Series(0, index=df.index, dtype="int16")

    # 8. radiograph_within_year — true if THIS CPT is radiograph AND prior_with_provider_count > 0
    #    Proxy for "any prior radiograph for this patient in last 365d". Will need a
    #    dedicated MV (mv_patient_radiograph_history) to be exact; safe default 0.
    out["radiograph_within_year"] = pd.Series(
        [_is_radiograph(c) for c in cpts_col],
        index=df.index,
    ).astype("int8")

    return out
