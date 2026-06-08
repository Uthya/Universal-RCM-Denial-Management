"""Category E — timely filing (5 features)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from rcm.features.categories._helpers import _as_date, _has_str
from rcm.features.categories.availability import RefDataLookup
from rcm.features.constants import DEFAULT_TIMELY_FILING_DAYS, TIMELY_NEAR_THRESHOLD_RATIO


def _payer_timely_days(policies: list[dict]) -> int:
    if not policies:
        return DEFAULT_TIMELY_FILING_DAYS
    for p in policies:
        if p.get("policy_type") != "timely_filing":
            continue
        rule = p.get("structured_rule") or {}
        v = rule.get("days")
        if isinstance(v, int) and v > 0:
            return v
    return DEFAULT_TIMELY_FILING_DAYS


def compute(df: pd.DataFrame, *, ref: RefDataLookup | None = None) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    ref = ref or RefDataLookup()

    svc_from = df["service_from_date"].apply(_as_date)
    sub = df["submission_date"].apply(_as_date)
    payers = df.get("payer_canonical_name", pd.Series([None] * len(df)))

    days = []
    for s, f in zip(sub, svc_from):
        if s and f:
            d = (s - f).days
            days.append(max(d, 0))
        else:
            days.append(0)
    out["service_to_submission_days"] = pd.Series(days, index=df.index).astype("int32")

    payer_days = []
    for p in payers:
        polys = ref.payer_policies_by_payer.get(str(p), []) if _has_str(p) else []
        payer_days.append(_payer_timely_days(polys))
    out["payer_timely_filing_days"] = pd.Series(payer_days, index=df.index).astype("int16")

    ratio = out["service_to_submission_days"] / out["payer_timely_filing_days"].clip(lower=1)
    out["timely_filing_proximity_ratio"] = ratio.astype("float32")
    out["is_past_timely_filing"] = (ratio >= 1.0).astype("int8")
    out["is_near_timely_filing"] = ((ratio >= TIMELY_NEAR_THRESHOLD_RATIO) & (ratio < 1.0)).astype("int8")
    return out
