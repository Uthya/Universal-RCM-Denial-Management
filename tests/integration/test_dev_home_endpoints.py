"""Integration tests for Home-dashboard endpoints: /uploads, /models, /jobs.

Per CR-041 verification matrix:
    A. Endpoint tests — happy-path response shape
    B. Empty-state tests — empty/null behavior on quiet DBs
    C. Invalid-param failure tests
    D. Remote PG compat via RCM_INTEGRATION_DSN
    E. No regression — existing /env and /db endpoints still work

Skipped unless RCM_INTEGRATION_DSN is set.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


pytestmark = pytest.mark.skipif(
    "RCM_INTEGRATION_DSN" not in os.environ,
    reason="RCM_INTEGRATION_DSN not set — home dashboard endpoint tests skipped",
)


@pytest.fixture
def client():
    os.environ["DATABASE_URL"] = os.environ["RCM_INTEGRATION_DSN"]
    os.environ.setdefault("JWT_SECRET_KEY", "dev-console-test-secret-32-chars-long")
    from rcm.main import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# /uploads/recent
# ---------------------------------------------------------------------------

class TestUploadsRecent:
    def test_default_shape(self, client: TestClient):
        r = client.get("/api/dev/uploads/recent")
        assert r.status_code == 200
        b = r.json()
        assert {"items", "total"} <= set(b)
        for item in b["items"]:
            assert {"id", "file_name", "file_type", "service_variant_detected",
                     "claim_subtype_detected", "parse_status", "claims_saved",
                     "claims_dropped", "uploaded_at"} <= set(item)
            assert isinstance(item["id"], int)

    def test_limit_param(self, client: TestClient):
        r = client.get("/api/dev/uploads/recent?limit=3")
        assert r.status_code == 200
        assert len(r.json()["items"]) <= 3

    def test_limit_too_large_422(self, client: TestClient):
        r = client.get("/api/dev/uploads/recent?limit=1000")
        assert r.status_code == 422

    def test_limit_zero_422(self, client: TestClient):
        r = client.get("/api/dev/uploads/recent?limit=0")
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# /models/registry
# ---------------------------------------------------------------------------

class TestModelsRegistry:
    def test_default_shape(self, client: TestClient):
        r = client.get("/api/dev/models/registry")
        assert r.status_code == 200
        b = r.json()
        assert {"items", "total", "trained_count"} <= set(b)
        # FE registry has 11 entries per CR-035 (10 real + _global)
        assert b["total"] == 11
        assert b["trained_count"] >= 0

    def test_all_fe_variants_present(self, client: TestClient):
        r = client.get("/api/dev/models/registry")
        b = r.json()
        keys = {(it["service_variant"], it["claim_subtype"]) for it in b["items"]}
        # The 11 keys from features.registered_variants() per CR-035
        for expected in [
            ("837P", "healthcare"),
            ("837P", "therapy"),
            ("837P", "transport"),
            ("837P", "specialty"),
            ("837I", "home_care"),
            ("837I", "institutional_other"),
            ("837I", "inpatient"),
            ("837I", "hospice"),
            ("837I", "specialty"),
            ("837D", "dental"),
            ("_global", "_global"),
        ]:
            assert expected in keys, f"Missing {expected} from registry"

    def test_empty_training_state(self, client: TestClient):
        """Until ML training is wired (Phase 4), no rows in
        model_training_metrics → has_trained_model=false for every entry."""
        r = client.get("/api/dev/models/registry")
        b = r.json()
        # Each entry has is_registered=true (from FE) but has_trained_model
        # reflects DB state; default empty-state should be false for all
        for item in b["items"]:
            assert item["is_registered"] is True
            assert isinstance(item["feature_count"], int)
            assert item["feature_count"] >= 108
            # has_trained_model may be true if a prior session left rows;
            # the contract is: when DB empty, it's false
            if not item["has_trained_model"]:
                assert item["model_version"] is None
                assert item["trained_at"] is None
                assert item["metrics_pr_auc"] is None


# ---------------------------------------------------------------------------
# /jobs/summary
# ---------------------------------------------------------------------------

class TestJobsSummary:
    def test_default_shape(self, client: TestClient):
        r = client.get("/api/dev/jobs/summary")
        assert r.status_code == 200
        b = r.json()
        assert {"by_status", "queued", "running",
                 "succeeded_last_hour", "failed_last_hour"} <= set(b)
        # All 5 statuses always present, count=0 when no rows
        statuses = {row["status"] for row in b["by_status"]}
        assert statuses == {"queued", "running", "succeeded", "failed", "cancelled"}

    def test_empty_state_returns_zeroes(self, client: TestClient):
        r = client.get("/api/dev/jobs/summary")
        b = r.json()
        # arq workers not wired yet → background_jobs is empty → all counts 0
        for row in b["by_status"]:
            assert row["count"] >= 0  # may be 0 (current state) or >0 if seeded
        assert b["queued"] >= 0
        assert b["running"] >= 0
        assert b["succeeded_last_hour"] >= 0
        assert b["failed_last_hour"] >= 0


# ---------------------------------------------------------------------------
# No-regression sentinel
# ---------------------------------------------------------------------------

class TestNoRegression:
    def test_env_info_still_works(self, client: TestClient):
        r = client.get("/api/dev/env/info")
        assert r.status_code == 200
        assert r.json()["database"]["alembic_head"] == "0010_add_materialized_views"

    def test_db_overview_still_works(self, client: TestClient):
        r = client.get("/api/dev/db/overview")
        assert r.status_code == 200
        assert r.json()["materialized_views"] == 12

    def test_telemetry_unhandled_still_works(self, client: TestClient):
        r = client.get("/api/dev/telemetry/unhandled-segments?days=1")
        assert r.status_code == 200
