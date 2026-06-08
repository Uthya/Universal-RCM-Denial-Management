"""Tiny per-row helpers shared by category builders.

Keep these pure (no DB/session). Each helper takes a pandas Series row
(or a column) and returns a scalar / Series of the right dtype.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd


def _as_date(v: Any) -> date | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, date):
        return v
    try:
        return pd.to_datetime(v).date()
    except Exception:
        return None


def _safe_int(v: Any, default: int = 0) -> int:
    if v is None or pd.isna(v):
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    if v is None or pd.isna(v):
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _ensure_list(v: Any) -> list:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, tuple):
        return list(v)
    try:
        if pd.isna(v):
            return []
    except Exception:
        pass
    return [v]


def _has_str(s: Any) -> bool:
    if s is None:
        return False
    if isinstance(s, float) and pd.isna(s):
        return False
    return bool(str(s).strip())
