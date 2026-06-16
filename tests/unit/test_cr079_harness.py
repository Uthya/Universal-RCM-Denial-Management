"""CR-079 — unit guards for the tuning harness.

Pure-Python; no DB. Guards that:
  * suggest_params returns values inside the AIR-approved ranges
  * the search space dimensionality matches the AIR (12 parameters)
  * _select_threshold reproduces the trainer's precision-floor behaviour
  * promote-script gate dict is self-consistent (variants ↔ thresholds)
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import numpy as np
import optuna
import pytest

# Make the scripts/ dir importable.
SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cr079_tune as T  # noqa: E402
import cr079_promote as P  # noqa: E402


class TestSuggestParams:
    @pytest.fixture
    def study(self):
        # In-memory study with a deterministic seed so suggested values are
        # predictable across runs.
        return optuna.create_study(direction="maximize",
                                   sampler=optuna.samplers.TPESampler(seed=42))

    def test_returns_all_twelve_params(self, study):
        trial = study.ask()
        params = T.suggest_params(trial, auto_spw=3.0)
        assert set(params) == {
            "n_estimators", "max_depth", "learning_rate",
            "min_child_weight", "gamma",
            "subsample", "colsample_bytree", "colsample_bylevel",
            "reg_alpha", "reg_lambda",
            "scale_pos_weight", "max_delta_step",
        }
        assert len(params) == 12

    @pytest.mark.parametrize("auto_spw", [1.0, 3.0, 10.0])
    def test_ranges_within_air_bounds(self, study, auto_spw):
        # Sample many trials and assert every value lies inside the
        # AIR-approved range.
        for _ in range(50):
            t = study.ask()
            p = T.suggest_params(t, auto_spw)
            assert 100 <= p["n_estimators"] <= 800
            assert 3 <= p["max_depth"] <= 9
            assert 0.01 <= p["learning_rate"] <= 0.30
            assert 1 <= p["min_child_weight"] <= 20
            assert 0.0 <= p["gamma"] <= 5.0
            assert 0.5 <= p["subsample"] <= 1.0
            assert 0.4 <= p["colsample_bytree"] <= 1.0
            assert 0.4 <= p["colsample_bylevel"] <= 1.0
            assert 1e-8 <= p["reg_alpha"] <= 10.0
            assert 1e-8 <= p["reg_lambda"] <= 10.0
            assert 0 <= p["max_delta_step"] <= 10
            # scale_pos_weight range scales with auto_spw
            assert max(0.05, 0.5 * auto_spw) <= p["scale_pos_weight"] <= max(0.10, 2.0 * auto_spw)
            study.tell(t, 0.5)


class TestSelectThreshold:
    def test_returns_threshold_meeting_precision_floor(self):
        rng = np.random.default_rng(42)
        y = np.concatenate([np.zeros(800), np.ones(200)]).astype(int)
        scores = np.concatenate([rng.beta(2, 5, 800), rng.beta(5, 2, 200)])
        t = T._select_threshold(scores, y, precision_floor=0.85)
        yp = (scores >= t).astype(int)
        tp = int(((yp == 1) & (y == 1)).sum())
        fp = int(((yp == 1) & (y == 0)).sum())
        precision = tp / (tp + fp) if (tp + fp) else 0
        # Floor must be respected when achievable
        assert precision >= 0.85 - 1e-6

    def test_falls_back_when_floor_unreachable(self):
        # Pathological label noise: signal is barely above chance.
        rng = np.random.default_rng(7)
        y = np.concatenate([np.zeros(50), np.ones(50)]).astype(int)
        scores = rng.uniform(0, 1, size=100)
        t = T._select_threshold(scores, y, precision_floor=0.99)
        # _select_threshold should still return a finite threshold in the
        # [0.01, 0.99) candidate range.
        assert 0.01 <= t < 0.99


class TestPromoteGateConfig:
    def test_every_variant_has_gates(self):
        assert set(P.GATES) == {"837P_healthcare", "837D_dental", "837I_home_care"}

    def test_dental_thresholds_are_stricter_than_healthcare(self):
        """CR-072 noted dental's 10.55% noise floor → gate is 2× stricter."""
        hc = P.GATES["837P_healthcare"]
        dt = P.GATES["837D_dental"]
        assert dt["min_d_roc_auc"] > hc["min_d_roc_auc"]
        assert dt["min_d_pr_auc"] > hc["min_d_pr_auc"]
        assert dt["min_d_f1"]     > hc["min_d_f1"]

    def test_brier_tolerance_is_looser_for_smaller_variants(self):
        # Healthcare has 50k rows → tight Brier tolerance.
        # Dental + home-care have <2k rows → looser tolerance.
        assert P.GATES["837P_healthcare"]["max_brier_pct_worse"] <= 5.0
        assert P.GATES["837D_dental"]["max_brier_pct_worse"] >= 10.0
        assert P.GATES["837I_home_care"]["max_brier_pct_worse"] >= 10.0

    def test_precision_floor_is_uniform(self):
        # The trainer's PRECISION_FLOOR is 0.85; gates must enforce that for
        # every variant.
        for k in P.GATES:
            assert P.GATES[k]["min_precision"] == 0.85

    def test_variants_constant_matches_gates(self):
        assert set(P.VARIANTS) == set(P.GATES.keys())
