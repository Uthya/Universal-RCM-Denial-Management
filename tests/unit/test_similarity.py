"""CR-136 — unit tests for the historical similarity forecast engine.

Covers:
  - SimilarityEngine.load on a synthetic history DataFrame
  - lookup() returns a NeighbourSet with correct denial count + matching factors
  - aggregate_similarity_forecast math (Poisson-Binomial expected count)
  - empty / cold-start handling (fall back to model fallback)
  - schema serialisation of the new Forecast / HighRiskClaimItem fields
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from rcm.ml.similarity import (
    DEFAULT_K,
    MIN_VARIANT_HISTORY,
    NeighbourSet,
    SimilarityEngine,
    aggregate_similarity_forecast,
)


def _make_history(n_per_variant: int = 300) -> pd.DataFrame:
    """Synthetic history with two variants and three payers.  837P claims at
    score >= 0.9 are mostly denied; 837I claims at score < 0.3 are mostly
    approved.  Adequate for the engine to discriminate."""
    rng = np.random.default_rng(42)
    rows = []
    for variant in ("837P", "837I"):
        for _ in range(n_per_variant):
            payer = int(rng.choice([1001, 1002, 1003]))
            score = float(rng.uniform(0, 1))
            is_repl = int(rng.random() < 0.3)
            # Synthetic denial rule
            if variant == "837P":
                denied = int(score >= 0.85)
            else:
                denied = int(score >= 0.6 and not is_repl)
            rows.append({
                "claim_id": len(rows) + 1,
                "service_variant": variant,
                "claim_subtype": "healthcare" if variant == "837P" else "home_care",
                "payer_id": payer,
                "is_replacement": is_repl,
                "predicted_risk": score,
                "denied": denied,
            })
    return pd.DataFrame(rows)


def _engine_from_df(df: pd.DataFrame) -> SimilarityEngine:
    payer_te = df.groupby("payer_id")["denied"].mean().to_dict()
    variant_te = df.groupby("service_variant")["denied"].mean().to_dict()
    eng = SimilarityEngine(
        history=df.copy(),
        payer_te=payer_te, variant_te=variant_te,
        train_mean=float(df["denied"].mean()),
    )
    eng._build_indices()
    return eng


class TestSimilarityEngine:
    def test_engine_loads_history(self):
        df = _make_history()
        eng = _engine_from_df(df)
        assert len(eng.history) == 600
        # Trees per variant + a global tree
        assert "837P" in eng._trees
        assert "837I" in eng._trees
        assert "__GLOBAL__" in eng._trees

    def test_lookup_returns_neighbour_set(self):
        df = _make_history()
        eng = _engine_from_df(df)
        # A high-score 837P claim should be retrieved with mostly denied neighbours
        ns = eng.lookup(
            risk_score=0.97, service_variant="837P",
            payer_id=1001, is_replacement=False,
        )
        assert isinstance(ns, NeighbourSet)
        assert ns.fallback_used == "similarity"
        assert ns.n_neighbours == DEFAULT_K
        # Expect majority denied on synthetic data
        assert ns.p_hat >= 0.7
        assert ns.n_denied >= 7

    def test_low_score_837i_mostly_paid(self):
        df = _make_history()
        eng = _engine_from_df(df)
        ns = eng.lookup(
            risk_score=0.1, service_variant="837I",
            payer_id=1001, is_replacement=False,
        )
        # Low-score 837I → mostly paid in our synthetic rule
        assert ns.p_hat <= 0.3

    def test_matching_factors_populated(self):
        df = _make_history()
        eng = _engine_from_df(df)
        ns = eng.lookup(
            risk_score=0.95, service_variant="837P",
            payer_id=1001, is_replacement=False,
        )
        # At minimum same variant and similar score should be flagged
        assert "Same service variant" in ns.matching_factors
        assert "Similar calibrated score" in ns.matching_factors

    def test_empty_history_returns_model_fallback(self):
        eng = SimilarityEngine(history=pd.DataFrame())
        ns = eng.lookup(
            risk_score=0.5, service_variant="837P",
            payer_id=1001, is_replacement=False,
        )
        assert ns.fallback_used == "model"
        assert ns.p_hat == 0.5
        assert ns.n_neighbours == 0

    def test_unknown_variant_uses_global_tree(self):
        df = _make_history()
        eng = _engine_from_df(df)
        ns = eng.lookup(
            risk_score=0.5, service_variant="837Q",  # not in history
            payer_id=1001, is_replacement=False,
        )
        # Should still serve via global tree
        assert ns.fallback_used == "similarity"
        assert ns.n_neighbours == DEFAULT_K

    def test_average_similarity_in_unit_interval(self):
        df = _make_history()
        eng = _engine_from_df(df)
        ns = eng.lookup(
            risk_score=0.5, service_variant="837P",
            payer_id=1001, is_replacement=False,
        )
        assert 0.0 < ns.average_similarity <= 1.0

    def test_min_variant_history_enforced(self):
        # Build a history where 837P has fewer than MIN_VARIANT_HISTORY rows
        df = _make_history(n_per_variant=50)  # below the 100-row min
        eng = _engine_from_df(df)
        # 837P tree should not exist; only the global tree
        assert "837P" not in eng._trees
        # Global may or may not exist depending on total size
        assert ("__GLOBAL__" in eng._trees) == (len(df) >= MIN_VARIANT_HISTORY)


class TestAggregate:
    def test_empty_cohort(self):
        f = aggregate_similarity_forecast([])
        assert f.n_high == 0
        assert f.expected_denials == 0.0
        assert f.historical_evidence_count == 0
        assert f.average_similarity == 0.0

    def test_homogeneous_cohort(self):
        ns_list = [
            NeighbourSet(p_hat=0.9, n_neighbours=10, n_denied=9, n_paid=1,
                          average_similarity=0.95, matching_factors=(),
                          fallback_used="similarity")
            for _ in range(20)
        ]
        f = aggregate_similarity_forecast(ns_list)
        assert f.n_high == 20
        assert f.expected_denials == 18.0
        assert f.expected_approvals == 2.0
        assert f.historical_evidence_count == 200
        assert f.average_similarity == 0.95
        assert f.fallback_distribution["similarity"] == 1.0

    def test_mixed_fallback_distribution(self):
        ns_list = [
            NeighbourSet(p_hat=0.9, n_neighbours=10, n_denied=9, n_paid=1,
                          average_similarity=0.9, matching_factors=(),
                          fallback_used="similarity"),
            NeighbourSet(p_hat=0.5, n_neighbours=0, n_denied=0, n_paid=0,
                          average_similarity=0.0, matching_factors=(),
                          fallback_used="model"),
        ]
        f = aggregate_similarity_forecast(ns_list)
        assert f.fallback_distribution["similarity"] == 0.5
        assert f.fallback_distribution["model"] == 0.5
        # Total evidence is 10 (only the similarity one had neighbours)
        assert f.historical_evidence_count == 10


class TestSchemaSerialisation:
    def test_forecast_engine_default_bucket(self):
        from rcm.schemas.public import Forecast
        f = Forecast()
        assert f.engine == "bucket"
        assert f.historical_evidence_count == 0
        assert f.average_similarity is None

    def test_high_risk_claim_item_new_fields_defaults(self):
        from rcm.schemas.public import HighRiskClaimItem
        c = HighRiskClaimItem(
            claim_id=1, claim_number="X", risk_score=0.9, risk_level="HIGH",
        )
        assert c.neighbours_found == 0
        assert c.neighbours_denied == 0
        assert c.average_similarity is None
        assert c.matching_factors == []

    def test_forecast_similarity_payload_round_trip(self):
        from rcm.schemas.public import Forecast
        f = Forecast(
            n_high=20, expected_denials=18.0, expected_approvals=2.0,
            forecast_confidence_pct=0.90, interval_low=16.0, interval_high=20.0,
            historical_sample_size=200, data_window_days=180,
            fallback_distribution={"similarity": 1.0},
            engine="similarity",
            historical_evidence_count=200, average_similarity=0.95,
        )
        assert f.engine == "similarity"
        assert f.historical_evidence_count == 200
        d = f.model_dump()
        assert d["engine"] == "similarity"
        assert d["average_similarity"] == 0.95
