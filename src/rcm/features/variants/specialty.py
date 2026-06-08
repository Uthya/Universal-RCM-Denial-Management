"""Category M — specialty variant (10 features) — 837P/specialty.

The 'specialty' subtype routes oncology, DME, behavioral health, and
lab/radiology claims through a SHARED variant block. Each sub-specialty
contributes its own signals; non-applicable signals default to 0 for
claims of other sub-specialties.

Sub-specialty contributors (per spec §4.2):
    oncology     — ndc_drug_present, j_code_count, is_high_cost_drug
    DME          — cr3_certification_present, is_rental, is_purchase
    behavioral   — is_h_code, is_initial_assessment, is_group_therapy
    lab/rad      — clia_number_present
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from rcm.features.categories._helpers import _has_str, _safe_float
from rcm.features.variants._base import (
    _ensure_str_list,
    _has_modifier,
)


# Behavioral health CPT sets
_BH_INITIAL_ASSESSMENT_CPTS = frozenset({"90791", "90792"})
_BH_GROUP_THERAPY_CPTS = frozenset({"90849", "90853"})

_HIGH_COST_DRUG_CHARGE_THRESHOLD = 5_000.0


def _has_ndc(ndcs: Any) -> int:
    lst = _ensure_str_list(ndcs)
    return int(bool(lst))


def _count_j_codes(cpts: Any) -> int:
    lst = _ensure_str_list(cpts)
    return sum(1 for c in lst if str(c).upper().startswith("J"))


def _has_ref_qualifier(var_data: Any, qualifier: str) -> int:
    if not isinstance(var_data, dict):
        return 0
    refs = var_data.get("references") or {}
    return int(bool(refs.get(qualifier)))


def _has_cert_type(cert_types: Any, target: str) -> int:
    if not isinstance(cert_types, list):
        return 0
    return int(any(_has_str(c) and str(c).lower() == target for c in cert_types))


def compute(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)

    ndcs_col = df.get("ndc_drug_codes", pd.Series([[]] * len(df), index=df.index))
    cpts_list_col = df.get("procedure_codes", pd.Series([[]] * len(df), index=df.index))
    primary_cpt_col = df.get("primary_cpt", pd.Series([None] * len(df)))
    charge_col = df.get("total_charge_amount", pd.Series([0.0] * len(df), index=df.index))
    modifiers_col = df.get("modifiers", pd.Series([[]] * len(df), index=df.index))
    cert_types_col = df.get("cert_types", pd.Series([[]] * len(df), index=df.index))
    var_data_col = df.get("variant_data", pd.Series([{}] * len(df), index=df.index))

    # ---- Oncology signals ----
    # 1. ndc_drug_present
    out["ndc_drug_present"] = pd.Series(
        [_has_ndc(n) for n in ndcs_col], index=df.index,
    ).astype("int8")

    # 2. j_code_count
    j_counts = [_count_j_codes(cpts) for cpts in cpts_list_col]
    out["j_code_count"] = pd.Series(j_counts, index=df.index).astype("int8")

    # 3. is_high_cost_drug — charge > 5k AND at least one J code
    out["is_high_cost_drug"] = pd.Series(
        [int(_safe_float(c) > _HIGH_COST_DRUG_CHARGE_THRESHOLD and jc > 0)
         for c, jc in zip(charge_col, j_counts)],
        index=df.index,
    ).astype("int8")

    # ---- DME signals ----
    # 4. cr3_certification_present (DME cert type)
    out["cr3_certification_present"] = pd.Series(
        [_has_cert_type(cts, "dme") for cts in cert_types_col],
        index=df.index,
    ).astype("int8")

    # 5. is_rental (modifier RR)
    modifiers_lists = [_ensure_str_list(m) for m in modifiers_col]
    out["is_rental"] = pd.Series(
        [int(_has_modifier(ms, "RR")) for ms in modifiers_lists],
        index=df.index,
    ).astype("int8")

    # 6. is_purchase (modifier NU)
    out["is_purchase"] = pd.Series(
        [int(_has_modifier(ms, "NU")) for ms in modifiers_lists],
        index=df.index,
    ).astype("int8")

    # ---- Behavioral health signals ----
    # 7. is_h_code (primary CPT starts with 'H')
    out["is_h_code"] = pd.Series(
        [int(_has_str(c) and str(c).upper().startswith("H")) for c in primary_cpt_col],
        index=df.index,
    ).astype("int8")

    # 8. is_initial_assessment (BH eval CPTs)
    out["is_initial_assessment"] = pd.Series(
        [int(_has_str(c) and str(c) in _BH_INITIAL_ASSESSMENT_CPTS) for c in primary_cpt_col],
        index=df.index,
    ).astype("int8")

    # 9. is_group_therapy (BH group CPTs)
    out["is_group_therapy"] = pd.Series(
        [int(_has_str(c) and str(c) in _BH_GROUP_THERAPY_CPTS) for c in primary_cpt_col],
        index=df.index,
    ).astype("int8")

    # ---- Lab/Rad signals ----
    # 10. clia_number_present (REF*X4)
    out["clia_number_present"] = pd.Series(
        [_has_ref_qualifier(vd, "X4") for vd in var_data_col],
        index=df.index,
    ).astype("int8")

    return out
