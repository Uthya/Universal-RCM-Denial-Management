"""HTTP integration tests for /api/dev/* endpoints.

Pattern: spin up the FastAPI app with TestClient, hit the endpoint, assert
on response shape. The test client uses the real DATABASE_URL from the
test environment so the env endpoint actually queries PG.

Skipped unless RCM_INTEGRATION_DSN is set.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from fastapi.testclient import TestClient


pytestmark = pytest.mark.skipif(
    "RCM_INTEGRATION_DSN" not in os.environ,
    reason="RCM_INTEGRATION_DSN not set — dev endpoint tests skipped",
)


@pytest.fixture
def client():
    # Point settings at the integration DSN BEFORE importing main
    os.environ["DATABASE_URL"] = os.environ["RCM_INTEGRATION_DSN"]
    os.environ.setdefault("JWT_SECRET_KEY", "dev-console-test-secret-32-chars-long")
    from rcm.main import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


class TestEnvEndpoint:
    def test_env_info_shape(self, client: TestClient):
        r = client.get("/api/dev/env/info")
        assert r.status_code == 200
        body = r.json()

        # Top-level keys per CR-039
        assert {"database", "extensions", "pg_settings", "feature_flags",
                "versions", "feature_engineering_version", "parser_version"}.issubset(body)

        # Database block
        assert body["database"]["url_redacted"].startswith("postgresql+asyncpg://")
        assert ":***@" in body["database"]["url_redacted"]
        assert body["database"]["version"].startswith("PostgreSQL")
        assert body["database"]["alembic_head"] in (None, "0010_add_materialized_views")

        # Extensions
        required = body["extensions"]["required"]
        assert any(e["name"] == "vector" for e in required)
        for e in required:
            assert e["status"] in ("installed", "available", "not_available")

        # PG settings
        assert "shared_buffers" in body["pg_settings"]
        assert "max_wal_size" in body["pg_settings"]

        # Feature flags
        assert "DEBUG" in body["feature_flags"]
        assert "RAG_ENABLED" in body["feature_flags"]

        # Versions
        assert "python" in body["versions"]
        assert "fastapi" in body["versions"]

    def test_health(self, client: TestClient):
        r = client.get("/api/dev/env/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] in ("ok", "degraded")
        assert body["database"] in ("up", "down")


class TestConfirmationGuard:
    def test_unknown_endpoint_returns_404(self, client: TestClient):
        # Sanity: 404 (not 500) for nonexistent paths
        r = client.get("/api/dev/does/not/exist")
        assert r.status_code == 404
