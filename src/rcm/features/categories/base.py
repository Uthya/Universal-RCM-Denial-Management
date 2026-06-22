"""Category J — base claim features (11 features, no external deps)."""

from __future__ import annotations

import pandas as pd

from rcm.features.categories._helpers import _as_date, _safe_float, _safe_int


def compute(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)

    charge = df["total_charge_amount"].apply(_safe_float).astype("float32")
    billed = df.get("lines_billed_sum", pd.Series(0.0, index=df.index)).apply(_safe_float).astype("float32")
    lines = df.get("claim_lines_count", pd.Series(0, index=df.index)).apply(_safe_int).astype("int32")
    dxs = df.get("diagnoses_count", pd.Series(0, index=df.index)).apply(_safe_int).astype("int32")
    units = df.get("lines_units_sum", pd.Series(0.0, index=df.index)).apply(_safe_float).astype("float32")

    out["total_charge_amount"] = charge
    out["total_billed_amount"] = billed
    out["charge_to_billed_ratio"] = (charge / billed.clip(lower=0.01)).astype("float32")
    out["line_count"] = lines
    out["diagnosis_count"] = dxs
    out["total_units"] = units
    out["units_per_line"] = (units / lines.clip(lower=1)).astype("float32")

    svc_from = df["service_from_date"].apply(_as_date)
    svc_to = df["service_to_date"].apply(_as_date)
    out["service_month"] = svc_from.apply(lambda d: d.month if d else 0).astype("int8")
    out["service_day_of_week"] = svc_from.apply(lambda d: d.weekday() if d else 0).astype("int8")
    out["weekend_service"] = (out["service_day_of_week"] >= 5).astype("int8")
    out["service_duration_days"] = [
        (b - a).days if a and b else 0 for a, b in zip(svc_from, svc_to)
    ]
    out["service_duration_days"] = out["service_duration_days"].astype("int32")
    return out
