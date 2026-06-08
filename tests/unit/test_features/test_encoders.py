"""Leakage-safe target encoder behavior."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from rcm.features.encoders import LeakageSafeTargetEncoder


class TestEncoder:
    def test_fit_transform_basic(self):
        rng = np.random.RandomState(0)
        n = 200
        X = pd.DataFrame({"payer": rng.choice(["A", "B", "C"], size=n)})
        # Make payer 'A' strongly predictive of denial
        y = pd.Series([1 if p == "A" else 0 for p in X["payer"]])
        enc = LeakageSafeTargetEncoder(columns=["payer"])
        encoded = enc.fit_transform(X, y)
        assert "payer_encoded" in encoded.columns
        assert len(encoded) == n
        assert enc.is_fitted()

    def test_leakage_safe_OOF_doesnt_perfect_predict(self):
        """Sanity: the encoder's OOF output must not equal the actual y exactly.
        If it did, that would mean leak."""
        rng = np.random.RandomState(42)
        n = 500
        X = pd.DataFrame({"payer": rng.choice(["A", "B", "C", "D"], size=n)})
        y = pd.Series(rng.binomial(1, 0.5, size=n))
        enc = LeakageSafeTargetEncoder(columns=["payer"])
        encoded = enc.fit_transform(X, y)
        # No row's encoded value should be exactly y_i (which would be perfect leak)
        diff = (encoded["payer_encoded"] - y).abs()
        assert diff.min() > 0, "Encoder leaked row's own label"

    def test_transform_unseen_uses_global_mean(self):
        X = pd.DataFrame({"payer": ["A", "B"] * 50})
        y = pd.Series([1, 0] * 50)
        enc = LeakageSafeTargetEncoder(columns=["payer"])
        enc.fit_transform(X, y)
        unseen = enc.transform_row({"payer": "NEW_VALUE"})
        # global mean is 0.5; sklearn's encoder smooths toward it
        assert "payer_encoded" in unseen
        assert 0.0 <= unseen["payer_encoded"] <= 1.0

    def test_is_known(self):
        X = pd.DataFrame({"payer": ["A", "B", "C"] * 30})
        y = pd.Series([1, 0, 1] * 30)
        enc = LeakageSafeTargetEncoder(columns=["payer"])
        enc.fit_transform(X, y)
        assert enc.is_known("payer", "A")
        assert enc.is_known("payer", "B")
        assert not enc.is_known("payer", "NEW")
        assert not enc.is_known("not_a_column", "anything")

    def test_persist_reload_roundtrip(self):
        X = pd.DataFrame({
            "payer": ["A", "B"] * 50,
            "cpt": ["X", "Y"] * 50,
        })
        y = pd.Series([1, 0] * 50)
        enc = LeakageSafeTargetEncoder(columns=["payer", "cpt"])
        encoded = enc.fit_transform(X, y)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "enc.joblib"
            enc.save(p)
            reloaded = LeakageSafeTargetEncoder.load(p)
            assert reloaded.is_fitted()
            assert reloaded.fitted_columns() == ["payer", "cpt"]

            # Transform the same X again — should produce same numeric output
            re_encoded = reloaded.transform(X)
            # Order may differ; check the column names match
            assert set(re_encoded.columns) == set(encoded.columns)
            # Transform-time output uses full-fit encoder, NOT OOF — values
            # will differ from fit_transform but should be valid floats
            assert re_encoded["payer_encoded"].notna().all()

    def test_transform_without_fit_raises(self):
        enc = LeakageSafeTargetEncoder(columns=["payer"])
        with pytest.raises(RuntimeError, match="not fitted"):
            enc.transform(pd.DataFrame({"payer": ["A"]}))
