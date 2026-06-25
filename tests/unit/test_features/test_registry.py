"""Registry coverage + M1 strict validation."""

from __future__ import annotations

import pandas as pd
import pytest

from rcm.features.registry import (
    FEATURE_COLUMNS_HEALTHCARE,
    FEATURE_REGISTRY,
    FeatureCategory,
    FeatureSchemaError,
    feature_count,
    get_feature_columns,
    specs_by_category,
    universal_columns,
    validate_feature_frame,
)


def test_all_healthcare_cols_have_specs():
    missing = [c for c in FEATURE_COLUMNS_HEALTHCARE if c not in FEATURE_REGISTRY]
    assert not missing, f"Columns lack FeatureSpec entries: {missing}"


def test_healthcare_column_count_matches_categories():
    # CR-104 → 101; CR-122B → 100; CR-126B added 2 recency smoothed features
    # → universal = 102. Healthcare = 102 universal + 4 Cat-M = 106.
    universal_n = len(universal_columns())
    assert universal_n == 102
    assert feature_count("837P", "healthcare") == universal_n + 4


def test_unknown_variant_raises():
    with pytest.raises(KeyError):
        get_feature_columns("837Z", "unknown")


class TestValidateFeatureFrame:
    def test_pass_exact_match(self):
        df = pd.DataFrame({c: [0] for c in FEATURE_COLUMNS_HEALTHCARE})
        validate_feature_frame(df, "837P", "healthcare")  # no raise

    def test_fail_missing(self):
        df = pd.DataFrame({c: [0] for c in FEATURE_COLUMNS_HEALTHCARE[:-1]})
        with pytest.raises(FeatureSchemaError, match="missing"):
            validate_feature_frame(df, "837P", "healthcare")

    def test_fail_extra(self):
        df = pd.DataFrame({c: [0] for c in FEATURE_COLUMNS_HEALTHCARE})
        df["bonus_feature"] = 0
        with pytest.raises(FeatureSchemaError, match="extra"):
            validate_feature_frame(df, "837P", "healthcare")

    def test_fail_order(self):
        # Reverse the columns — same set, wrong order
        df = pd.DataFrame({c: [0] for c in reversed(FEATURE_COLUMNS_HEALTHCARE)})
        with pytest.raises(FeatureSchemaError, match="order"):
            validate_feature_frame(df, "837P", "healthcare")


def test_category_specs_consistent():
    """Every category enum value has at least one spec OR is explicitly empty
    (we don't have variant-only categories with no specs)."""
    for cat in FeatureCategory:
        specs = specs_by_category(cat)
        # Z_availability has at least 5; M_variant has 6 for healthcare
        # No category should be empty
        assert specs, f"Category {cat.value} has no specs"
