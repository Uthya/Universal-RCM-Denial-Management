"""Category M — transport variant (8 features) — 837P/transport.

Ambulance / NEMT denials cluster around: missing CR1, missing CRC*07
medical-necessity codes, missing origin/destination, mileage outside the
local norm, level-of-service modifier mismatch.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from rcm.features.categories._helpers import _has_str, _safe_float, _safe_int


# Level-of-service modifier integer mapping (ALS=1, BLS=2, SCT=3)
_LOS_INT = {
    "ALS": 1, "ALS1": 1, "ALS2": 1,
    "BLS": 2, "BLSE": 2,
    "SCT": 3,
}

# Transport reason codes A-E
_REASON_INT = {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5}


def _transport_cert_field(tc: Any, key: str, default: Any = None) -> Any:
    if not isinstance(tc, dict):
        return default
    v = tc.get(key)
    return v if v is not None else default


def compute(
    df: pd.DataFrame,
    *,
    transport_reason_code_encoded: pd.Series | None = None,
    los_modifier_encoded: pd.Series | None = None,
) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)

    tc_col = df.get("transport_cert", pd.Series([None] * len(df), index=df.index))

    # 1. ambulance_cert_present (was transport_cert populated?)
    out["ambulance_cert_present"] = pd.Series(
        [int(isinstance(tc, dict)) for tc in tc_col], index=df.index,
    ).astype("int8")

    # 2. transport_miles (default 0)
    out["transport_miles"] = pd.Series(
        [_safe_float(_transport_cert_field(tc, "transport_miles", 0.0)) for tc in tc_col],
        index=df.index,
    ).astype("float32")

    # 3. patient_weight_lbs (default 0)
    out["patient_weight_lbs"] = pd.Series(
        [_safe_int(_transport_cert_field(tc, "patient_weight_lbs", 0)) for tc in tc_col],
        index=df.index,
    ).astype("int16")

    # 4. transport_reason_code_encoded
    if transport_reason_code_encoded is not None:
        out["transport_reason_code_encoded"] = transport_reason_code_encoded.reindex(
            df.index, fill_value=0.0,
        ).astype("float32")
    else:
        out["transport_reason_code_encoded"] = pd.Series(
            [float(_REASON_INT.get(
                str(_transport_cert_field(tc, "transport_reason_code", "")).upper(), 0,
            )) for tc in tc_col],
            index=df.index,
        ).astype("float32")

    # 5. round_trip_indicator (defaults False → 0)
    out["round_trip_indicator"] = pd.Series(
        [int(bool(_transport_cert_field(tc, "round_trip", False))) for tc in tc_col],
        index=df.index,
    ).astype("int8")

    # 6. emergent_indicator
    out["emergent_indicator"] = pd.Series(
        [int(bool(_transport_cert_field(tc, "emergent", False))) for tc in tc_col],
        index=df.index,
    ).astype("int8")

    # 7. origin_dest_specified (both addresses present in transport_certifications row)
    def _has_origin_dest(tc: Any) -> int:
        if not isinstance(tc, dict):
            return 0
        return int(bool(tc.get("origin_address")) and bool(tc.get("destination_address")))
    out["origin_dest_specified"] = pd.Series(
        [_has_origin_dest(tc) for tc in tc_col], index=df.index,
    ).astype("int8")

    # 8. los_modifier_encoded
    if los_modifier_encoded is not None:
        out["los_modifier_encoded"] = los_modifier_encoded.reindex(df.index, fill_value=0.0).astype("float32")
    else:
        out["los_modifier_encoded"] = pd.Series(
            [float(_LOS_INT.get(
                str(_transport_cert_field(tc, "level_of_service", "")).upper(), 0,
            )) for tc in tc_col],
            index=df.index,
        ).astype("float32")

    return out
