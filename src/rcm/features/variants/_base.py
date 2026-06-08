"""Helpers shared across variant Category-M blocks.

Pure functions only — no DB, no side effects. Each variant module imports
what it needs from here.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def _cpt_int(cpt: Any) -> int | None:
    """Convert a CPT string to int when possible (numeric CPTs only).
    Returns None for HCPCS alpha codes (A0429, J0135, etc.)."""
    if cpt is None:
        return None
    s = str(cpt).strip()
    if not s or not s.isdigit():
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _has_modifier(modifiers: Any, target: str) -> bool:
    """True if any modifier in the row's list matches `target` (case-insensitive)."""
    if not isinstance(modifiers, list):
        return False
    t = target.upper()
    return any(str(m).upper() == t for m in modifiers if m)


def _has_any_modifier(modifiers: Any, targets: set[str]) -> bool:
    if not isinstance(modifiers, list):
        return False
    upper_targets = {t.upper() for t in targets}
    return any(str(m).upper() in upper_targets for m in modifiers if m)


def _has_cpt_prefix(cpt: Any, prefix: str) -> bool:
    if cpt is None:
        return False
    s = str(cpt).strip().upper()
    return s.startswith(prefix.upper())


def _ensure_str_list(v: Any) -> list[str]:
    """Coerce to a list[str], filtering Nones/blanks."""
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v if x is not None and str(x).strip()]
    if isinstance(v, tuple):
        return [str(x) for x in v if x is not None and str(x).strip()]
    return []


def _series_apply(df: pd.DataFrame, col: str, fn) -> pd.Series:
    """Apply `fn` element-wise to df[col], returning a Series aligned to df.index.
    Safe when the column doesn't exist (returns Series of defaults)."""
    if col not in df.columns:
        return pd.Series([fn(None) for _ in range(len(df))], index=df.index)
    return df[col].apply(fn)
