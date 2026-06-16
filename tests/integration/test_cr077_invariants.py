"""CR-077 — invariant tests that would have caught the CR-075-shape
bugs found in the post-CR-067 audit.

Categories:
  - Evaluation Integrity (split arithmetic + threshold provenance)
  - Prediction Attribution Integrity (pipeline_name = actual scorer;
    model_version = actual deployed model; threshold = actual threshold)
  - Feature Integrity (booster has feature_names; count + order match)
  - Artifact Integrity (all 5 files load + match schema)
  - Response Integrity (404 on missing resource)

Every test here is designed to FAIL CI if the relevant bug class
recurs. Skipped unless RCM_INTEGRATION_DSN is set + a live backend at
http://127.0.0.1:8000.
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest


pytestmark = pytest.mark.skipif(
    "RCM_INTEGRATION_DSN" not in os.environ,
    reason="RCM_INTEGRATION_DSN not set",
)


def _live_backend(url: str = "http://127.0.0.1:8000/openapi.json") -> bool:
    try:
        with httpx.Client(timeout=2) as c:
            return c.get(url).status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Evaluation Integrity
# ---------------------------------------------------------------------------

class TestCR077EvaluationIntegrity:
    """Asserts the CR-075 contract: train/validation/held-out split.

    Failure mode this catches: someone removes the held-out slice and reverts
    to OOF-only evaluation. The reported metrics would silently revert to the
    threshold-selection-biased numbers.
    """

    def test_train_response_has_three_slice_fields(self):
        if not _live_backend():
            pytest.skip("backend not running")
        with httpx.Client(timeout=300) as c:
            r = c.post("http://127.0.0.1:8000/api/predictions/train", json={})
        assert r.status_code == 200, r.text
        body = r.json()
        split = body["split"]
        # All three counts must be present and positive
        assert split["train_samples"] > 0, "train_samples must be > 0"
        assert split["validation_samples"] > 0, (
            "validation_samples must be > 0 — CR-075 mandates a real validation slice"
        )
        assert split["held_out_samples"] > 0, (
            "held_out_samples must be > 0 — CR-075 mandates a held-out test set"
        )
        # test_samples is the v1-frontend alias for held_out_samples
        assert split["test_samples"] == split["held_out_samples"]

        # CR-076 #7: model_version must look distinguishable, not "v1.0.0"
        mv = body["model_version"]
        assert mv and mv.startswith("v1.fb."), (
            f"model_version must be a CR-076-style timestamp tag, got {mv!r}"
        )
        assert "20" in mv, f"model_version should embed a year, got {mv!r}"

        # Evaluation block must exist with all three slices
        ev = body.get("evaluation") or {}
        assert ev.get("oof_metrics") is not None, "OOF diagnostic missing"
        assert ev.get("validation_metrics") is not None, "validation_metrics missing"
        assert ev.get("held_out_metrics") is not None, "held_out_metrics missing"

        # CR-075 invariant: headline `metrics` equals held-out (NOT OOF)
        # F1 in particular is the threshold-sensitive metric we caught CR-075 on
        held_f1 = ev["held_out_metrics"]["f1"]
        headline_f1 = body["metrics"]["f1"]
        assert abs(headline_f1 - held_f1) < 1e-9, (
            f"headline F1 {headline_f1} must come from held-out, not OOF "
            f"({ev['oof_metrics']['f1']})"
        )


# ---------------------------------------------------------------------------
# Prediction Attribution Integrity
# ---------------------------------------------------------------------------

class TestCR077AttributionIntegrity:
    """Asserts CR-076 #1, #2 — prediction_log labels reflect the actual
    scoring engine + version + threshold.

    Failure mode this catches: someone reverts the Option-C attribution
    fix and Option-C-fallback claims start logging as 'featurebuilder' in
    production rows (the original audit finding).
    """

    @pytest.mark.asyncio
    async def test_option_c_claim_logs_as_simple_pipeline_in_production(self):
        if not _live_backend():
            pytest.skip("backend not running")
        import asyncpg
        # Find any institutional_other claim (Option-C fallback target)
        dsn = "postgresql://rcm:rcm_dev_password@localhost:5433/rcm_denials_dev"
        c = await asyncpg.connect(dsn=dsn, timeout=8)
        try:
            row = await c.fetchrow("""
                SELECT id FROM claims
                WHERE claim_subtype='institutional_other'
                  AND deleted_at IS NULL
                LIMIT 1
            """)
        finally:
            await c.close()
        if row is None:
            pytest.skip("no institutional_other claim in DB")
        claim_id = int(row["id"])

        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"http://127.0.0.1:8000/api/predictions/predict-claim/{claim_id}"
            )
        assert r.status_code == 200, r.text

        # Query the prediction_log row that was just written
        c = await asyncpg.connect(dsn=dsn, timeout=8)
        try:
            rows = await c.fetch("""
                SELECT pipeline_name, prediction_type, model_version
                  FROM prediction_log
                 WHERE claim_id = $1
                   AND prediction_time > now() - interval '1 minute'
                 ORDER BY prediction_time DESC
            """, claim_id)
        finally:
            await c.close()

        # Should have EXACTLY one row, labeled simple_pipeline/production
        # (no shadow row because no FB predictor exists for institutional_other)
        prod_rows = [r for r in rows if r["prediction_type"] == "production"]
        assert prod_rows, "no production row written"
        prod = prod_rows[0]
        assert prod["pipeline_name"] == "simple_pipeline", (
            f"Option-C fallback claim must log as 'simple_pipeline', "
            f"got pipeline_name={prod['pipeline_name']!r}. "
            f"This is the exact CR-076 #1 bug."
        )
        assert prod["model_version"].startswith("v1.simple."), (
            f"model_version must be a simple_pipeline version, got {prod['model_version']!r}"
        )

    @pytest.mark.asyncio
    async def test_fb_claim_logs_correct_fb_version(self):
        if not _live_backend():
            pytest.skip("backend not running")
        import asyncpg
        dsn = "postgresql://rcm:rcm_dev_password@localhost:5433/rcm_denials_dev"
        c = await asyncpg.connect(dsn=dsn, timeout=8)
        try:
            row = await c.fetchrow("""
                SELECT id FROM claims
                WHERE claim_subtype='dental'
                  AND service_variant='837D'
                  AND deleted_at IS NULL
                LIMIT 1
            """)
        finally:
            await c.close()
        if row is None:
            pytest.skip("no dental claim in DB")
        claim_id = int(row["id"])

        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"http://127.0.0.1:8000/api/predictions/predict-claim/{claim_id}"
            )
        assert r.status_code == 200, r.text

        c = await asyncpg.connect(dsn=dsn, timeout=8)
        try:
            rows = await c.fetch("""
                SELECT pipeline_name, prediction_type, model_version, decision_threshold
                  FROM prediction_log
                 WHERE claim_id = $1
                   AND prediction_time > now() - interval '1 minute'
            """, claim_id)
        finally:
            await c.close()

        prod = [r for r in rows if r["prediction_type"] == "production"]
        assert prod, "no production row"
        p = prod[0]
        assert p["pipeline_name"] == "featurebuilder"
        # Model version is unique per training run AND embeds the variant
        assert "837D_dental" in str(p["model_version"]), (
            f"FB production row's model_version should embed the variant, "
            f"got {p['model_version']!r} — CR-076 #2 / #7 bug recurrence"
        )
        # Decision threshold must NOT be the 0.5 placeholder anymore
        assert float(p["decision_threshold"]) != 0.5, (
            "decision_threshold is still the 0.5 placeholder — "
            "CR-076 #8 bug recurrence"
        )


# ---------------------------------------------------------------------------
# Feature Integrity (lesson M1)
# ---------------------------------------------------------------------------

class TestCR077FeatureIntegrity:
    """Asserts CR-076 #3 — XGBoost booster knows its feature names so a
    silent positional-order drift can't mis-attribute SHAP."""

    def test_booster_has_feature_names_per_variant(self):
        from rcm.ml.predictor import HealthcarePredictor
        root = Path("artifacts/featurebuilder")
        for v, st in [("837P","healthcare"),("837D","dental"),("837I","home_care")]:
            d = root / f"{v}_{st}"
            if not d.is_dir():
                pytest.skip(f"artifact {v}/{st} missing")
            pred = HealthcarePredictor.load(d)
            booster_names = pred.bundle.booster.feature_names or []
            bundle_cols = pred.bundle.feature_columns
            assert booster_names, (
                f"{v}/{st}: booster.feature_names is empty — "
                f"XGBoost would fall back to positional matching (lesson M1)"
            )
            assert len(booster_names) == len(bundle_cols), (
                f"{v}/{st}: booster has {len(booster_names)} feature names "
                f"but bundle has {len(bundle_cols)} columns"
            )
            assert list(booster_names) == list(bundle_cols), (
                f"{v}/{st}: feature ORDER drift — booster vs bundle"
            )


# ---------------------------------------------------------------------------
# Artifact Integrity
# ---------------------------------------------------------------------------

class TestCR077ArtifactIntegrity:
    def test_every_trainable_variant_artifact_loads(self):
        from rcm.ml.predictor import HealthcarePredictor
        root = Path("artifacts/featurebuilder")
        required_files = {"model.json", "encoder.joblib", "calibrator.joblib",
                          "rarity_state.joblib", "feature_schema.json"}
        for v, st in [("837P","healthcare"),("837D","dental"),("837I","home_care")]:
            d = root / f"{v}_{st}"
            assert d.is_dir(), f"artifact dir missing: {d}"
            files_on_disk = {p.name for p in d.iterdir() if p.is_file()}
            missing = required_files - files_on_disk
            assert not missing, f"{v}/{st} missing artifact files: {missing}"
            # Load + smoke-check
            pred = HealthcarePredictor.load(d)
            assert pred.bundle.booster.num_features() > 0
            assert pred.bundle.decision_threshold > 0
            assert pred.bundle.model_version != "v1.0.0", (
                f"{v}/{st}: model_version is the hardcoded 'v1.0.0' — "
                f"CR-076 #2 bug recurrence (every retraining must produce "
                f"a unique version string)"
            )


# ---------------------------------------------------------------------------
# Response Integrity
# ---------------------------------------------------------------------------

class TestCR077ResponseIntegrity:
    def test_predict_file_missing_id_returns_404(self):
        if not _live_backend():
            pytest.skip("backend not running")
        with httpx.Client(timeout=10) as c:
            r = c.post("http://127.0.0.1:8000/api/predictions/predict-file/999999")
        assert r.status_code == 404, (
            f"predict-file/<missing-id> must return 404, got {r.status_code}. "
            f"CR-076 #5 bug recurrence."
        )
        assert "not found" in r.text.lower()
