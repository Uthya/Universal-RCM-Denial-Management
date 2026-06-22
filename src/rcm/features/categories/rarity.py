"""Category L — rarity / unseen / missing flags.

Reads training vocabularies from an optional `RarityState` snapshot
(populated at training time, persisted with the model artifact). At
predict time the same state is loaded and used for unseen detection.

Mutually exclusive rule: when ``unseen_X==1``, ``is_rare_X==0``
(no-volume isn't 'rare' — it's a vocabulary miss).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from rcm.features.categories._helpers import _has_str
from rcm.features.constants import (
    RARE_CPT_THRESHOLD,
    RARE_DX_THRESHOLD,
    RARE_PAYER_THRESHOLD,
    RARE_PROVIDER_THRESHOLD,
)


@dataclass
class RarityState:
    """Per-column training vocabulary + volume map.

    `volume_by_value[col][value]` = how many training rows had that value.
    Persisted alongside the model artifact.
    """
    volume_by_value: dict[str, dict[str, int]] = field(default_factory=dict)
    rare_thresholds: dict[str, int] = field(default_factory=lambda: {
        "payer_canonical_name": RARE_PAYER_THRESHOLD,
        "primary_cpt": RARE_CPT_THRESHOLD,
        "primary_dx": RARE_DX_THRESHOLD,
        "billing_provider_npi": RARE_PROVIDER_THRESHOLD,
        "rendering_provider_npi": RARE_PROVIDER_THRESHOLD,
    })

    @classmethod
    def fit(cls, df: pd.DataFrame) -> "RarityState":
        cols = [
            "payer_canonical_name",
            "primary_cpt",
            "primary_dx",
            "billing_provider_npi",
            "rendering_provider_npi",
        ]
        state = cls()
        for col in cols:
            if col not in df.columns:
                state.volume_by_value[col] = {}
                continue
            vc = df[col].dropna().astype("string").value_counts().to_dict()
            state.volume_by_value[col] = {str(k): int(v) for k, v in vc.items()}
        return state

    def volume(self, col: str, value: Any) -> int:
        v = str(value) if value is not None else ""
        if not v:
            return 0
        return self.volume_by_value.get(col, {}).get(v, 0)

    def known(self, col: str, value: Any) -> bool:
        v = str(value) if value is not None else ""
        if not v:
            return False
        return v in self.volume_by_value.get(col, {})


def compute(df: pd.DataFrame, *, state: RarityState | None = None) -> pd.DataFrame:
    """Compute rarity/unseen/missing columns.

    If `state` is None (cold start, no training vocab yet), every unseen_*
    flag is 0 and every is_rare_* is 0 too. Safe default for the very
    first training run where there's no prior vocabulary.
    """
    out = pd.DataFrame(index=df.index)

    def vol(col_data, vocab_col):
        if state is None:
            return [0] * len(col_data)
        return [state.volume(vocab_col, v) for v in col_data]

    def known(col_data, vocab_col):
        if state is None:
            return [True] * len(col_data)  # treat as known when no vocab → no false unseen flags
        return [state.known(vocab_col, v) for v in col_data]

    payer_volume = vol(df.get("payer_canonical_name", pd.Series([None] * len(df))), "payer_canonical_name")
    dx_volume = vol(df.get("primary_dx", pd.Series([None] * len(df))), "primary_dx")

    payer_known = known(df.get("payer_canonical_name", pd.Series([None] * len(df))), "payer_canonical_name")
    cpt_known = known(df.get("primary_cpt", pd.Series([None] * len(df))), "primary_cpt")
    dx_known = known(df.get("primary_dx", pd.Series([None] * len(df))), "primary_dx")
    rend_known = known(df.get("rendering_provider_npi", pd.Series([None] * len(df))), "rendering_provider_npi")

    # Mutual exclusion: only rare when seen-but-rare; unseen wins otherwise
    out["is_rare_payer"] = [
        int(k and 0 < v < RARE_PAYER_THRESHOLD) for k, v in zip(payer_known, payer_volume)
    ]
    out["is_rare_dx"] = [
        int(k and 0 < v < RARE_DX_THRESHOLD) for k, v in zip(dx_known, dx_volume)
    ]

    out["unseen_payer"] = [int(not k) for k in payer_known]
    out["unseen_cpt"] = [int(not k) for k in cpt_known]
    out["unseen_dx"] = [int(not k) for k in dx_known]
    out["unseen_rendering_provider"] = [int(not k) for k in rend_known]
    out["unseen_any"] = [
        int(any([up, uc, ud, ur]))
        for up, uc, ud, ur in zip(
            out["unseen_payer"], out["unseen_cpt"], out["unseen_dx"],
            out["unseen_rendering_provider"],
        )
    ]

    # Missing flags
    out["missing_payer"] = (~df.get("payer_canonical_name", pd.Series(dtype="object")).apply(_has_str)).astype("int8")
    out["missing_diagnosis"] = (df.get("diagnoses_count", pd.Series(0, index=df.index)).fillna(0).astype(int) == 0).astype("int8")
    out["missing_procedure"] = (~df.get("primary_cpt", pd.Series(dtype="object")).apply(_has_str)).astype("int8")
    out["missing_pos"] = (~df.get("primary_pos", pd.Series(dtype="object")).apply(_has_str)).astype("int8")
    out["missing_count"] = (
        out["missing_payer"] + out["missing_diagnosis"]
        + out["missing_procedure"] + out["missing_pos"]
    ).astype("int8")

    # Downcast to compact dtypes
    for c in out.columns:
        if out[c].dtype == "int64":
            out[c] = out[c].astype("int8" if c != "missing_count" else "int8")
    return out
