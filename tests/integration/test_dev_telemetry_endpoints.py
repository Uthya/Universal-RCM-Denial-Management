"""Integration tests for /api/dev/telemetry/* endpoints.

Per CR-041 verification matrix:
    A. Endpoint tests — happy-path shape per endpoint
    B. Empty-state tests — endpoints don't crash when the remote DB has 0
       rows in the look-back window
    C. API failure tests — invalid `days` / invalid `group_by`
    D. Remote PG compat — runs against `RCM_INTEGRATION_DSN`
    E. No regression — env + db endpoints still work alongside

The remote DB has limited parse_event history (only what prior test runs
left behind, mostly cleaned up). These tests therefore assert SHAPE +
EMPTY-STATE behavior, not specific counts.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


pytestmark = pytest.mark.skipif(
    "RCM_INTEGRATION_DSN" not in os.environ,
    reason="RCM_INTEGRATION_DSN not set — telemetry endpoint tests skipped",
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
# /telemetry/drops
# ---------------------------------------------------------------------------

class TestDrops:
    def test_default_shape(self, client: TestClient):
        r = client.get("/api/dev/telemetry/drops")
        assert r.status_code == 200
        b = r.json()
        assert {"days", "group_by", "items"} <= set(b)
        assert b["days"] == 30
        assert b["group_by"] == "day"
        assert isinstance(b["items"], list)
        for item in b["items"]:
            assert {"bucket", "service_variant", "dropped_count"} <= set(item)
            assert isinstance(item["dropped_count"], int)

    def test_empty_state(self, client: TestClient):
        """1-day window on a quiet test DB should produce 0 or few rows
        without crashing."""
        r = client.get("/api/dev/telemetry/drops?days=1")
        assert r.status_code == 200
        assert isinstance(r.json()["items"], list)

    def test_hour_grouping(self, client: TestClient):
        r = client.get("/api/dev/telemetry/drops?group_by=hour&days=1")
        assert r.status_code == 200
        assert r.json()["group_by"] == "hour"

    def test_invalid_group_by(self, client: TestClient):
        r = client.get("/api/dev/telemetry/drops?group_by=week")
        assert r.status_code == 400

    def test_invalid_days(self, client: TestClient):
        r = client.get("/api/dev/telemetry/drops?days=0")
        assert r.status_code == 422
        r = client.get("/api/dev/telemetry/drops?days=99999")
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# /telemetry/drop-reasons
# ---------------------------------------------------------------------------

class TestDropReasons:
    def test_default_shape(self, client: TestClient):
        r = client.get("/api/dev/telemetry/drop-reasons")
        assert r.status_code == 200
        b = r.json()
        assert {"days", "total_errors", "items"} <= set(b)
        assert isinstance(b["total_errors"], int)
        for item in b["items"]:
            assert {"segment", "field", "count"} <= set(item)

    def test_empty_state_returns_empty_list(self, client: TestClient):
        r = client.get("/api/dev/telemetry/drop-reasons?days=1")
        assert r.status_code == 200
        b = r.json()
        # Either empty or some, but always a list and well-typed
        assert isinstance(b["items"], list)
        assert b["total_errors"] >= 0


# ---------------------------------------------------------------------------
# /telemetry/unhandled-segments
# ---------------------------------------------------------------------------

class TestUnhandledSegments:
    def test_shape(self, client: TestClient):
        r = client.get("/api/dev/telemetry/unhandled-segments")
        assert r.status_code == 200
        b = r.json()
        assert {"days", "items"} <= set(b)
        for item in b["items"]:
            assert {"segment_name", "count", "last_seen_at",
                     "last_seen_in_file_id", "last_seen_text"} <= set(item)
            assert isinstance(item["count"], int)

    def test_top_parameter(self, client: TestClient):
        r = client.get("/api/dev/telemetry/unhandled-segments?top=5")
        assert r.status_code == 200
        assert len(r.json()["items"]) <= 5

    def test_empty_state(self, client: TestClient):
        r = client.get("/api/dev/telemetry/unhandled-segments?days=1")
        assert r.status_code == 200
        assert isinstance(r.json()["items"], list)


# ---------------------------------------------------------------------------
# /telemetry/cas-stride-distribution
# ---------------------------------------------------------------------------

class TestCasStride:
    def test_shape(self, client: TestClient):
        r = client.get("/api/dev/telemetry/cas-stride-distribution")
        assert r.status_code == 200
        b = r.json()
        assert {"days", "total_warnings", "items"} <= set(b)
        for item in b["items"]:
            assert {"stride", "count"} <= set(item)
            assert item["stride"] in (2, 3)


# ---------------------------------------------------------------------------
# /telemetry/encoding-distribution
# ---------------------------------------------------------------------------

class TestEncoding:
    def test_returns_pending_note(self, client: TestClient):
        """Per CR-045: encoding metric not yet instrumented; endpoint returns
        an empty list + a structured note explaining what's needed."""
        r = client.get("/api/dev/telemetry/encoding-distribution")
        assert r.status_code == 200
        b = r.json()
        assert b["items"] == []
        assert b["note"] is not None
        assert "telemetry not yet collected" in b["note"].lower()


# ---------------------------------------------------------------------------
# /telemetry/validator-tiers
# ---------------------------------------------------------------------------

class TestValidatorTiers:
    def test_shape(self, client: TestClient):
        r = client.get("/api/dev/telemetry/validator-tiers")
        assert r.status_code == 200
        b = r.json()
        assert {"days", "items"} <= set(b)
        for item in b["items"]:
            assert {"service_variant", "validator", "severity", "count"} <= set(item)
            assert item["severity"] in ("ERROR", "WARNING", "INFO")


# ---------------------------------------------------------------------------
# /telemetry/reparse-log
# ---------------------------------------------------------------------------

class TestReparseLog:
    def test_shape(self, client: TestClient):
        r = client.get("/api/dev/telemetry/reparse-log")
        assert r.status_code == 200
        b = r.json()
        assert {"days", "items"} <= set(b)
        for item in b["items"]:
            assert {"edi_file_id", "file_name", "parser_version",
                     "parse_status", "parse_completed_at",
                     "claims_saved", "claims_dropped"} <= set(item)

    def test_limit_param(self, client: TestClient):
        r = client.get("/api/dev/telemetry/reparse-log?limit=5")
        assert r.status_code == 200
        assert len(r.json()["items"]) <= 5


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
