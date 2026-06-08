"""Per-variant feature blocks (Category M).

Every module exposes `compute(df, *, …) -> pd.DataFrame` with columns
matching the variant's FEATURE_COLUMNS_<VARIANT> tail in registry.py.

To add a new variant:
    1. Create a new module here with a `compute(df) -> pd.DataFrame` fn
    2. Add an entry in registry.py: a FeatureSpec list + a FEATURE_COLUMNS_<NAME>
       tuple + an entry in _VARIANT_COLUMNS
    3. Add a dispatch entry in builder.py:_dispatch_variant_block
    4. Add unit tests in tests/unit/test_features/test_variants.py
"""

from rcm.features.variants import (
    dental,
    global_fallback,
    healthcare,
    home_care,
    institutional_other,
    specialty,
    therapy,
    transport,
)

__all__ = [
    "dental",
    "global_fallback",
    "healthcare",
    "home_care",
    "institutional_other",
    "specialty",
    "therapy",
    "transport",
]
