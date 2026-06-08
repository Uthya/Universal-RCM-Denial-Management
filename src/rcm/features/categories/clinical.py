"""Category C — medical necessity / clinical alignment (8 features)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from rcm.features.categories._helpers import _has_str, _safe_float
from rcm.features.categories.availability import RefDataLookup
from rcm.features.constants import EM_HIGH_COMPLEXITY


def _is_unspecified(dx: Any) -> int:
    if not _has_str(dx):
        return 0
    s = str(dx).upper()
    return int(s.endswith(".9") or s.startswith("Z"))


def _acute_chronic(dx_list: list[dict]) -> int:
    """0=unknown, 1=acute, 2=chronic, 3=mixed. Simple ICD-10 prefix heuristic:
       'I' (circulatory) often chronic; 'J0'-'J2' acute respiratory; etc.
       This is a placeholder until a proper severity table is loaded.
    """
    if not dx_list:
        return 0
    acute = chronic = 0
    for d in dx_list:
        code = str(d.get("code", "")).upper()
        if not code:
            continue
        if code.startswith(("J0", "J1", "J2", "R", "S", "T")):
            acute += 1
        elif code.startswith(("I", "E1", "M", "N", "G")):
            chronic += 1
    if acute and chronic:
        return 3
    if acute:
        return 1
    if chronic:
        return 2
    return 0


def compute(
    df: pd.DataFrame,
    *,
    ref: RefDataLookup | None = None,
    cpt_dx_denial_rate: dict[tuple[str, str], float] | None = None,
    dx_chapter_encoded: pd.Series | None = None,
    cpt_category_encoded: pd.Series | None = None,
) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    ref = ref or RefDataLookup()
    cpt_dx_denial_rate = cpt_dx_denial_rate or {}

    cpts = df.get("primary_cpt", pd.Series([None] * len(df)))
    dxs = df.get("primary_dx", pd.Series([None] * len(df)))
    dxs_list = df.get("diagnoses", pd.Series([[]] * len(df), index=df.index))

    # cpt_dx_alignment_score: high score = LOW denial rate for this CPT×DX pair.
    # Inverted so larger = better alignment. Default 0.5 (no info).
    align: list[float] = []
    for cpt, dx in zip(cpts, dxs):
        if not (_has_str(cpt) and _has_str(dx)):
            align.append(0.5)
            continue
        rate = cpt_dx_denial_rate.get((str(cpt), str(dx)))
        align.append(1.0 - rate if rate is not None else 0.5)
    out["cpt_dx_alignment_score"] = pd.Series(align, index=df.index).astype("float32")

    out["has_unspecified_diagnosis"] = pd.Series([_is_unspecified(d) for d in dxs], index=df.index).astype("int8")

    if dx_chapter_encoded is not None:
        out["primary_dx_chapter_encoded"] = dx_chapter_encoded.reindex(df.index, fill_value=0.0).astype("float32")
    else:
        out["primary_dx_chapter_encoded"] = pd.Series(0.0, index=df.index, dtype="float32")

    # dx_severity_score from ref
    severities = []
    for dx in dxs:
        if _has_str(dx):
            severities.append(_safe_float(ref.dx_severity.get(str(dx), 0.0)))
        else:
            severities.append(0.0)
    out["dx_severity_score"] = pd.Series(severities, index=df.index).astype("float32")

    out["acute_vs_chronic_indicator"] = pd.Series(
        [_acute_chronic(dl if isinstance(dl, list) else []) for dl in dxs_list],
        index=df.index,
    ).astype("int8")

    if cpt_category_encoded is not None:
        out["cpt_category_encoded"] = cpt_category_encoded.reindex(df.index, fill_value=0.0).astype("float32")
    else:
        out["cpt_category_encoded"] = pd.Series(0.0, index=df.index, dtype="float32")

    def _high_complexity(cpt: Any) -> int:
        if not _has_str(cpt):
            return 0
        try:
            return int(int(str(cpt)) in EM_HIGH_COMPLEXITY)
        except ValueError:
            return 0

    out["is_high_complexity_em"] = pd.Series([_high_complexity(c) for c in cpts], index=df.index).astype("int8")

    # principal_dx_supports_procedure: lookup in cms_lcd_coverage; default 1
    def _lcd_supports(cpt: Any, dx: Any) -> int:
        if not (_has_str(cpt) and _has_str(dx)):
            return 1
        lcd = ref.lcd_coverage.get(str(cpt))
        if not lcd:
            return 1
        covered = lcd.get("covered_dx_codes") or []
        excluded = lcd.get("excluded_dx_codes") or []
        if str(dx) in excluded:
            return 0
        if covered and str(dx) not in covered:
            return 0
        return 1

    out["principal_dx_supports_procedure"] = pd.Series(
        [_lcd_supports(c, d) for c, d in zip(cpts, dxs)],
        index=df.index,
    ).astype("int8")
    return out
