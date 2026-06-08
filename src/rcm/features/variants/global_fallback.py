"""Category M — global fallback (0 features).

Used by the FeatureBuilder when a claim arrives with a `(service_variant,
claim_subtype)` tuple that isn't registered. The matrix shape is just the
108 universal columns — no variant-specific Category M block.

This is the safety net that satisfies the "any-variant" goal: the system
will produce a feature row for ANY claim, even if its variant has no
specialized model yet. The corresponding global model (`_global` artifact
bundle) consumes this matrix.
"""

from __future__ import annotations

import pandas as pd


def compute(df: pd.DataFrame) -> pd.DataFrame:
    # No variant-specific features; return an empty-columns DataFrame
    # indexed identically to the input. The builder will simply skip the
    # variant-concat step when this returns empty.
    return pd.DataFrame(index=df.index)
