"""Category M — institutional_other variant (5 features) — 837I/(inpatient/hospice/other).

Per spec §2.2, 837I claims that aren't home_care (revenue 0551-0589) are
all bucketed into 'institutional_other' for the variant model layer.
This block covers inpatient room/board (0100-0219), hospice (0820-0859),
and any other institutional service that doesn't fit the home-care block.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from rcm.features.categories._helpers import _as_date, _has_str
from rcm.features.constants import (
    HOSPICE_REVENUE_RANGE,
    INPATIENT_REVENUE_RANGE,
)
from rcm.features.variants._base import _ensure_str_list


def _rev_in_range(code: str, lo: int, hi: int) -> bool:
    if not _has_str(code):
        return False
    try:
        n = int(str(code))
    except ValueError:
        return False
    return lo <= n <= hi


def compute(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)

    revenue_codes_col = df.get("revenue_codes", pd.Series([[]] * len(df), index=df.index))
    var_data_col = df.get("variant_data", pd.Series([{}] * len(df), index=df.index))
    svc_from_col = df.get("service_from_date", pd.Series([None] * len(df)))
    svc_to_col = df.get("service_to_date", pd.Series([None] * len(df)))

    # 1. inpatient_revenue_code_present (0100-0219)
    out["inpatient_revenue_code_present"] = pd.Series(
        [int(any(_rev_in_range(rc, INPATIENT_REVENUE_RANGE[0], INPATIENT_REVENUE_RANGE[1])
                 for rc in _ensure_str_list(rcs)))
         for rcs in revenue_codes_col],
        index=df.index,
    ).astype("int8")

    # 2. hospice_revenue_code_present (0820-0859)
    out["hospice_revenue_code_present"] = pd.Series(
        [int(any(_rev_in_range(rc, HOSPICE_REVENUE_RANGE[0], HOSPICE_REVENUE_RANGE[1])
                 for rc in _ensure_str_list(rcs)))
         for rcs in revenue_codes_col],
        index=df.index,
    ).astype("int8")

    # 3. drg_assigned — DRG comes back via MIA segment on 835. For training-time
    #    detection, look in variant_data (where MIA segment data could be stored
    #    by the parser later). Default 0 today since MIA storage is deferred.
    def _has_drg(vd: Any) -> int:
        if not isinstance(vd, dict):
            return 0
        return int(bool(vd.get("drg_code") or vd.get("drg_amount")))
    out["drg_assigned"] = pd.Series(
        [_has_drg(vd) for vd in var_data_col],
        index=df.index,
    ).astype("int8")

    # 4. admission_date_present (DTP*435)
    def _has_admission(vd: Any) -> int:
        if not isinstance(vd, dict):
            return 0
        return int(bool(vd.get("admission_date")))
    out["admission_date_present"] = pd.Series(
        [_has_admission(vd) for vd in var_data_col],
        index=df.index,
    ).astype("int8")

    # 5. statement_period_days (DTP*434 — encoded as service_from→service_to)
    out["statement_period_days"] = pd.Series(
        [(_as_date(b) - _as_date(a)).days if _as_date(a) and _as_date(b) else 0
         for a, b in zip(svc_from_col, svc_to_col)],
        index=df.index,
    ).astype("int16")

    return out
