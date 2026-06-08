"""Category K — target-encoded categoricals (5 features).

These wrap LeakageSafeTargetEncoder. The builder calls `fit_transform` at
training time and `transform` at predict time, never bypassing the encoder.

Columns covered:
    payer_name_encoded          ← payer_canonical_name
    primary_cpt_encoded         ← primary_cpt
    primary_dx_encoded          ← primary_dx
    place_of_service_encoded    ← primary_pos
    facility_type_code_encoded  ← facility_type_code
"""

from __future__ import annotations

import pandas as pd

from rcm.features.constants import MISSING_CATEGORICAL_SENTINEL
from rcm.features.encoders import LeakageSafeTargetEncoder


_SOURCE_COLUMNS = (
    "payer_canonical_name",
    "primary_cpt",
    "primary_dx",
    "primary_pos",
    "facility_type_code",
)
_OUTPUT_NAMES = (
    "payer_name_encoded",
    "primary_cpt_encoded",
    "primary_dx_encoded",
    "place_of_service_encoded",
    "facility_type_code_encoded",
)


def make_encoder() -> LeakageSafeTargetEncoder:
    """Return an unfitted encoder configured for the Category K columns."""
    return LeakageSafeTargetEncoder(columns=list(_SOURCE_COLUMNS))


def fit_transform(df: pd.DataFrame, y) -> tuple[LeakageSafeTargetEncoder, pd.DataFrame]:
    """Fit a fresh encoder on the categorical columns and return both the
    encoder (for persistence) AND the encoded frame (with renamed columns
    matching FEATURE_REGISTRY).
    """
    enc = make_encoder()
    X = _prep(df)
    encoded = enc.fit_transform(X, y)
    return enc, _rename(encoded)


def transform(df: pd.DataFrame, encoder: LeakageSafeTargetEncoder) -> pd.DataFrame:
    """Predict-time transform via the fitted encoder."""
    X = _prep(df)
    encoded = encoder.transform(X)
    return _rename(encoded)


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    for src in _SOURCE_COLUMNS:
        if src in df.columns:
            out[src] = df[src].astype("string").fillna(MISSING_CATEGORICAL_SENTINEL)
        else:
            out[src] = pd.Series(MISSING_CATEGORICAL_SENTINEL, index=df.index, dtype="string")
    return out


def _rename(encoded: pd.DataFrame) -> pd.DataFrame:
    """Rename `<source>_encoded` → registry-mandated column names."""
    rename = dict(zip(
        [f"{c}_encoded" for c in _SOURCE_COLUMNS],
        _OUTPUT_NAMES,
    ))
    out = encoded.rename(columns=rename)
    # Stable column order
    return out[list(_OUTPUT_NAMES)]
