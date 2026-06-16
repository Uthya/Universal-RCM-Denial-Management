"""CR-067 cutover tests — FB is the primary prediction engine.

Each test starts the backend's predictions module against the live local DB
and exercises the FB-primary code path via the in-process helper functions
(no HTTP overhead). The HTTP-level smoke test is performed separately in the
Evaluation C step of the CR-067 deliverable.

Tests:
  1. `_fb_predictor_for` resolves the 3 trainable variants and returns None
     for institutional_other.
  2. `_any_fb_artifact_exists` returns True given the artifacts on disk.
  3. `_predict_file_via_fb` returns FB-scored ScoredClaim objects for the
     LR1K_D smoke-test file's claims.
  4. Option-C fallback: synthetic institutional_other claim routes via
     `_fb_predictor_for` → None.
  5. Kill switch: setting RCM_FB_PRIMARY=false in the env reverts
     `_fb_primary_enabled()` to False.

Skipped unless RCM_INTEGRATION_DSN is set.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    "RCM_INTEGRATION_DSN" not in os.environ,
    reason="RCM_INTEGRATION_DSN not set",
)


class TestCR067Cutover:
    def test_fb_variant_keys_table(self):
        from rcm.routers.public.predictions import _FB_VARIANT_KEYS
        # 3 trainable variants are mapped; institutional_other and unknown are NOT
        assert ("837P", "healthcare") in _FB_VARIANT_KEYS
        assert ("837D", "dental") in _FB_VARIANT_KEYS
        assert ("837I", "home_care") in _FB_VARIANT_KEYS
        assert ("837I", "institutional_other") not in _FB_VARIANT_KEYS
        assert (None, None) not in _FB_VARIANT_KEYS

    def test_fb_predictor_resolves_for_trainable_variants(self):
        from rcm.routers.public.predictions import _fb_predictor_for
        # Each trainable variant must resolve to a non-None HealthcarePredictor
        for v, st in [("837P", "healthcare"), ("837D", "dental"), ("837I", "home_care")]:
            p = _fb_predictor_for(v, st)
            assert p is not None, f"FB predictor missing for {v}/{st}"

    def test_fb_predictor_none_for_unsupported_variants(self):
        from rcm.routers.public.predictions import _fb_predictor_for
        # Option C cases return None — caller must fall back
        assert _fb_predictor_for("837I", "institutional_other") is None
        assert _fb_predictor_for("unknown_variant", "whatever") is None
        assert _fb_predictor_for(None, None) is None
        assert _fb_predictor_for("837P", None) is None  # unknown subtype

    def test_any_fb_artifact_exists(self):
        from rcm.routers.public.predictions import _any_fb_artifact_exists
        assert _any_fb_artifact_exists() is True, (
            "Expected at least one FB artifact present from prior R3/CR-072 training run"
        )

    def test_kill_switch(self, monkeypatch):
        from rcm.routers.public.predictions import _fb_primary_enabled
        monkeypatch.setenv("RCM_FB_PRIMARY", "false")
        assert _fb_primary_enabled() is False
        monkeypatch.setenv("RCM_FB_PRIMARY", "true")
        assert _fb_primary_enabled() is True
        # Default (unset) is True
        monkeypatch.delenv("RCM_FB_PRIMARY", raising=False)
        assert _fb_primary_enabled() is True

    @pytest.mark.asyncio
    async def test_predict_file_via_fb_returns_scored_claims(self):
        """Run the FB dispatch over LR1K_D_pair_05 (edi_file_id=4067) — the
        smoke-test file from CR-068A. The 10 claims are all 837D/dental, so
        all should be routed through the FB 837D predictor (no fallback).

        Calls the live backend at http://127.0.0.1:8000 (assumed running with
        the right DATABASE_URL in its env). The test asserts FB scoring is
        wired end-to-end; if the backend isn't up, the test is skipped.
        """
        import httpx
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                # backend health
                health = await client.get("http://127.0.0.1:8000/openapi.json")
                if health.status_code != 200:
                    pytest.skip("backend not running")
                # predict-file
                r = await client.post(
                    "http://127.0.0.1:8000/api/predictions/predict-file/4067"
                )
        except httpx.ConnectError:
            pytest.skip("backend not running")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["edi_file_id"] == 4067
        # 10 claims expected for this smoke file
        assert body["predicted_claims"] == 10, body
        # Confirm all rows are 837D/dental (high_risk only is shown; risk_summary
        # exposes the buckets across all claims)
        for hr in body["high_risk_claims"]:
            assert hr["service_variant"] == "837D"
            assert hr["claim_subtype"] == "dental"
            assert hr["risk_level"] == "HIGH"
            assert 0.0 <= float(hr["risk_score"]) <= 1.0
            # FB top_denial_reasons must contain actual FB feature names
            # (e.g., 'total_charge_amount', 'payer_cpt_denial_rate',
            # 'same_day_visits_for_patient'). simple_pipeline used CARC code
            # names like 'CARC_29'.
            reasons = hr.get("top_denial_reasons") or []
            assert reasons, "Expected non-empty top_denial_reasons"
            feature_names = {r.get("feature", "") for r in reasons}
            simple_pipeline_features = {n for n in feature_names if n.startswith("CARC_")}
            assert not simple_pipeline_features, (
                f"Found simple_pipeline-style CARC features in FB response: "
                f"{simple_pipeline_features}"
            )
