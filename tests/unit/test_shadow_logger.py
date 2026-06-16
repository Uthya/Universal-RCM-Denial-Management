"""CR-065 — Shadow logger unit tests.

No DB. Mocks the bulk insert + the per-variant predictor. Verifies:

  - is_shadow_enabled() respects RCM_SHADOW_LOGGING
  - empty scored list → no DB call
  - production rows always written for every scored claim
  - shadow rows only written when a variant has a predictor
  - shadow failure on one variant does not break production rows for that variant
"""
from __future__ import annotations

import os
import types
from unittest.mock import AsyncMock, patch

import pytest

from rcm.ml import shadow


# Build a minimal ScoredClaim-like object for tests; we only access the fields
# log_paired_predictions actually reads.
def _scored(claim_id, claim_number, variant, subtype, risk_score=0.5, risk_level="MEDIUM"):
    s = types.SimpleNamespace()
    s.claim_id = claim_id
    s.claim_number = claim_number
    s.service_variant = variant
    s.claim_subtype = subtype
    s.risk_score = risk_score
    s.risk_level = risk_level
    return s


class TestIsShadowEnabled:
    @pytest.mark.parametrize("v,expected", [
        ("true", True), ("TRUE", True), ("1", True),
        ("yes", True), ("on", True),
        ("false", False), ("FALSE", False), ("0", False),
        ("no", False), ("off", False), ("anything", False),
    ])
    def test_env_values(self, v, expected, monkeypatch):
        monkeypatch.setenv("RCM_SHADOW_LOGGING", v)
        assert shadow.is_shadow_enabled() is expected

    def test_default_true_when_unset(self, monkeypatch):
        monkeypatch.delenv("RCM_SHADOW_LOGGING", raising=False)
        assert shadow.is_shadow_enabled() is True


class TestLogPairedPredictions:
    @pytest.mark.asyncio
    async def test_disabled_short_circuits(self, monkeypatch):
        monkeypatch.setenv("RCM_SHADOW_LOGGING", "false")
        scored = [_scored(1, "C1", "837P", "healthcare")]
        with patch.object(shadow, "_bulk_insert_rows", new=AsyncMock(return_value=0)) as ins:
            out = await shadow.log_paired_predictions(session=None, edi_file_id=99, scored_simple=scored)
        assert out["enabled"] is False
        assert out["production_rows"] == 0
        assert out["shadow_rows"] == 0
        ins.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_scored_no_insert(self, monkeypatch):
        monkeypatch.setenv("RCM_SHADOW_LOGGING", "true")
        with patch.object(shadow, "_bulk_insert_rows", new=AsyncMock(return_value=0)) as ins:
            out = await shadow.log_paired_predictions(session=None, edi_file_id=99, scored_simple=[])
        assert out["enabled"] is True
        assert out["production_rows"] == 0
        ins.assert_not_called()

    @pytest.mark.asyncio
    async def test_production_rows_written_when_no_shadow_predictor(self, monkeypatch):
        """Variant has NO artifact (institutional_other case) — production row
        STILL written, shadow row NOT written, no exception."""
        monkeypatch.setenv("RCM_SHADOW_LOGGING", "true")

        scored = [
            _scored(1, "C1", "837I", "institutional_other"),
            _scored(2, "C2", "837I", "institutional_other"),
        ]

        # ShadowLogger.get_predictor returns None for institutional_other
        sl = shadow.ShadowLogger()
        with patch.object(shadow, "get_shadow_logger", return_value=sl), \
             patch.object(shadow, "_bulk_insert_rows", new=AsyncMock(return_value=2)) as ins:
            out = await shadow.log_paired_predictions(session=None, edi_file_id=42, scored_simple=scored)

        assert out["enabled"] is True
        assert out["production_rows"] == 2
        assert out["shadow_rows"] == 0
        assert out["shadow_failures"] == 0
        ins.assert_awaited_once()
        # Inspect: insert should contain exactly 2 rows (2 production, 0 shadow)
        rows_arg = ins.await_args.args[0]
        assert len(rows_arg) == 2
        # All rows must be pipeline_name='simple_pipeline', prediction_type='production'
        # Row layout: ..., pipeline_name (idx 14), prediction_type (idx 15), prediction_group_id (16), created_at (17)
        for r in rows_arg:
            assert r[14] == "simple_pipeline"
            assert r[15] == "production"

    @pytest.mark.asyncio
    async def test_shadow_failure_isolated_per_variant(self, monkeypatch):
        """If shadow predictor raises on a variant, production rows still
        written for ALL claims; shadow rows omitted for that variant only."""
        monkeypatch.setenv("RCM_SHADOW_LOGGING", "true")

        scored = [
            _scored(1, "C1", "837P", "healthcare"),
            _scored(2, "C2", "837P", "healthcare"),
        ]

        # Make get_predictor return a failing mock predictor
        failing_predictor = types.SimpleNamespace()
        failing_predictor.predict = AsyncMock(side_effect=RuntimeError("boom"))
        sl = shadow.ShadowLogger()
        sl._predictors[("837P", "healthcare")] = failing_predictor

        with patch.object(shadow, "get_shadow_logger", return_value=sl), \
             patch.object(shadow, "_bulk_insert_rows", new=AsyncMock(return_value=2)) as ins, \
             patch.object(shadow, "_load_predict_corpus", new=AsyncMock(return_value=__import__("pandas").DataFrame({"claim_id": [1, 2]}))):
            out = await shadow.log_paired_predictions(session=None, edi_file_id=42, scored_simple=scored)

        assert out["enabled"] is True
        assert out["production_rows"] == 2
        assert out["shadow_rows"] == 0  # shadow failed
        assert out["shadow_failures"] == 2
        ins.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_paired_rows_share_prediction_group_id(self, monkeypatch):
        """Production + shadow rows for the same claim must share prediction_group_id."""
        monkeypatch.setenv("RCM_SHADOW_LOGGING", "true")
        import pandas as pd
        import uuid as _uuid

        scored = [_scored(7, "C7", "837P", "healthcare")]

        # Stub predictor returns one prediction result for claim_id=7
        mock_result = types.SimpleNamespace()
        mock_result.claim_id = 7
        mock_result.prediction_id = _uuid.uuid4()
        mock_result.risk_score = 0.42
        mock_result.predicted_label = 0
        mock_result.risk_level = "MEDIUM"
        mock_result.service_variant = "837P"
        mock_result.claim_subtype = "healthcare"
        mock_result.model_version = "v1.0.0"
        mock_result.feature_engineering_version = "fb_v1"
        mock_result.calibrator_version = "isotonic_v1"
        mock_result.decision_threshold = 0.51
        mock_result.fell_back_to_global = False

        ok_predictor = types.SimpleNamespace()
        ok_predictor.predict = AsyncMock(return_value=[mock_result])
        sl = shadow.ShadowLogger()
        sl._predictors[("837P", "healthcare")] = ok_predictor

        with patch.object(shadow, "get_shadow_logger", return_value=sl), \
             patch.object(shadow, "_bulk_insert_rows", new=AsyncMock(return_value=2)) as ins, \
             patch.object(shadow, "_load_predict_corpus", new=AsyncMock(return_value=pd.DataFrame({"claim_id": [7]}))):
            out = await shadow.log_paired_predictions(session=None, edi_file_id=42, scored_simple=scored)

        assert out["production_rows"] == 1
        assert out["shadow_rows"] == 1
        rows = ins.await_args.args[0]
        assert len(rows) == 2
        # group_id is at index 16
        gid_prod = rows[0][16]
        gid_shadow = rows[1][16]
        assert gid_prod == gid_shadow, "paired rows must share prediction_group_id"
        # pipeline_name at index 14 must be distinct
        assert {rows[0][14], rows[1][14]} == {"simple_pipeline", "featurebuilder"}
        # prediction_type at index 15
        assert {rows[0][15], rows[1][15]} == {"production", "shadow"}
