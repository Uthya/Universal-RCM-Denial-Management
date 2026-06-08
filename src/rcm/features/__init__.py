"""Feature engineering layer — denial-driven, per-variant.

Public surface:
    FeatureBuilder         — orchestrator that produces a feature DataFrame
    FeatureSpec            — single feature's metadata (name/category/source/leakage/avail)
    FEATURE_COLUMNS_*      — canonical ordered list per variant
    LeakageSafeTargetEncoder — wraps sklearn TargetEncoder with cross_val_predict
    load_training_corpus   — pulls labelled claims from mv_claim_labels + JOINs
    validate_feature_frame — M1 strict name+order check at predict time

Design rules baked in:
    * Same code path for training and prediction. The builder is the single
      source — no separate prediction-only feature paths.
    * Feature columns are stable across training, prediction, monitoring,
      and drift. Reference-data state does NOT change dimensionality.
    * Missing reference data → safe defaults + per-claim availability flags
      summarized as `reference_data_completeness` ∈ [0,1].
    * Encoders use cross_val_predict at fit time; transform uses the
      full-fit encoder. State persisted via joblib for predict-time loading.
"""

from rcm.features.constants import (
    FEATURE_ENGINEERING_VERSION,
    LOW_PROB_CUTOFF,
    PRECISION_FLOOR,
    RARE_CPT_THRESHOLD,
    RARE_DX_THRESHOLD,
    RARE_PAYER_THRESHOLD,
)
from rcm.features.dataset import load_training_corpus
from rcm.features.encoders import LeakageSafeTargetEncoder
from rcm.features.registry import (
    FEATURE_COLUMNS_DENTAL,
    FEATURE_COLUMNS_GLOBAL,
    FEATURE_COLUMNS_HEALTHCARE,
    FEATURE_COLUMNS_HOME_CARE,
    FEATURE_COLUMNS_INSTITUTIONAL_OTHER,
    FEATURE_COLUMNS_SPECIALTY,
    FEATURE_COLUMNS_THERAPY,
    FEATURE_COLUMNS_TRANSPORT,
    FEATURE_REGISTRY,
    FeatureSpec,
    feature_count_by_variant,
    get_feature_columns,
    is_registered_variant,
    registered_variants,
    universal_columns,
    validate_feature_frame,
)

__all__ = [
    "FEATURE_COLUMNS_DENTAL",
    "FEATURE_COLUMNS_GLOBAL",
    "FEATURE_COLUMNS_HEALTHCARE",
    "FEATURE_COLUMNS_HOME_CARE",
    "FEATURE_COLUMNS_INSTITUTIONAL_OTHER",
    "FEATURE_COLUMNS_SPECIALTY",
    "FEATURE_COLUMNS_THERAPY",
    "FEATURE_COLUMNS_TRANSPORT",
    "FEATURE_ENGINEERING_VERSION",
    "FEATURE_REGISTRY",
    "FeatureSpec",
    "LOW_PROB_CUTOFF",
    "LeakageSafeTargetEncoder",
    "PRECISION_FLOOR",
    "RARE_CPT_THRESHOLD",
    "RARE_DX_THRESHOLD",
    "RARE_PAYER_THRESHOLD",
    "feature_count_by_variant",
    "get_feature_columns",
    "is_registered_variant",
    "load_training_corpus",
    "registered_variants",
    "universal_columns",
    "validate_feature_frame",
]
