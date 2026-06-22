"""Category M — healthcare-variant features (4 columns; CR-104 retired surgery_global_period_active + cob_indicator).

See FEATURE_COLUMNS_HEALTHCARE tail in registry.py for the exact list.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from rcm.features.categories._helpers import _has_str
from rcm.features.categories.history import PatientHistorySnapshot
from rcm.features.constants import (
    EM_CODE_RANGES,
    TELEHEALTH_MODIFIERS,
    TELEHEALTH_POS_CODES,
)


def _em_level(cpt: Any) -> int:
    """E&M level 1–5 derived from the suffix of CPT codes in the 99201-99499 band.

    99211 → 1, 99212 → 2, … 99215 → 5; similarly for new vs established.
    Returns 0 for non-E&M CPTs.
    """
    if not _has_str(cpt):
        return 0
    try:
        n = int(str(cpt))
    except ValueError:
        return 0
    if not (99201 <= n <= 99499):
        return 0
    # Last digit-1 indexed level for the office/established/inpatient ranges
    last = n % 10
    if last == 0:
        return 0
    return max(min(last, 5), 1)


def _is_telehealth(pos: Any, modifiers: list[str]) -> int:
    if _has_str(pos) and str(pos) in TELEHEALTH_POS_CODES:
        return 1
    for m in modifiers or []:
        if _has_str(m) and str(m).upper() in TELEHEALTH_MODIFIERS:
            return 1
    return 0


def _is_preventive(cpt: Any) -> int:
    if not _has_str(cpt):
        return 0
    try:
        n = int(str(cpt))
    except ValueError:
        return 0
    new_lo, new_hi = EM_CODE_RANGES["preventive_new"]
    est_lo, est_hi = EM_CODE_RANGES["preventive_established"]
    return int(new_lo <= n <= new_hi or est_lo <= n <= est_hi)


def _is_consultation(cpt: Any) -> int:
    if not _has_str(cpt):
        return 0
    try:
        n = int(str(cpt))
    except ValueError:
        return 0
    out_lo, out_hi = EM_CODE_RANGES["consultation"]
    in_lo, in_hi = EM_CODE_RANGES["consultation_inpatient"]
    return int(out_lo <= n <= out_hi or in_lo <= n <= in_hi)


def compute(
    df: pd.DataFrame,
    *,
    history_snapshot: PatientHistorySnapshot | None = None,
) -> pd.DataFrame:
    """Compute the 4 healthcare-variant columns."""
    out = pd.DataFrame(index=df.index)
    cpts = df.get("primary_cpt", pd.Series([None] * len(df)))
    poss = df.get("primary_pos", pd.Series([None] * len(df)))
    mods = df.get("modifiers", pd.Series([[]] * len(df), index=df.index))

    out["e_and_m_level"] = pd.Series([_em_level(c) for c in cpts], index=df.index).astype("int8")
    out["is_telehealth"] = pd.Series(
        [_is_telehealth(p, m if isinstance(m, list) else []) for p, m in zip(poss, mods)],
        index=df.index,
    ).astype("int8")
    out["is_preventive_visit"] = pd.Series([_is_preventive(c) for c in cpts], index=df.index).astype("int8")
    out["is_consultation"] = pd.Series([_is_consultation(c) for c in cpts], index=df.index).astype("int8")
    return out
