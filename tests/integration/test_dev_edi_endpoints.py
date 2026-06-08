"""Integration tests for U6 — /api/dev/edi/* (EDI Inspector).

Verification matrix (CR-041 §5):
    A. Endpoint tests — happy-path + shape
    B. Empty-state tests — files list / segments / events with no matches
    C. Failure tests — invalid limit, missing confirm on upload, 404 on bad id
    D. Remote PG compat via RCM_INTEGRATION_DSN
    E. No regression — /env/info still works
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


pytestmark = pytest.mark.skipif(
    "RCM_INTEGRATION_DSN" not in os.environ,
    reason="RCM_INTEGRATION_DSN not set — EDI inspector endpoint tests skipped",
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
# GET /edi/files
# ---------------------------------------------------------------------------

class TestFilesList:
    def test_default_shape(self, client: TestClient):
        r = client.get("/api/dev/edi/files")
        assert r.status_code == 200
        b = r.json()
        assert {"items", "total", "limit", "offset"} <= set(b)
        for item in b["items"]:
            assert {"id", "file_name", "file_type", "parse_status",
                     "parser_version", "raw_size_bytes", "uploaded_at"} <= set(item)

    def test_status_filter(self, client: TestClient):
        r = client.get("/api/dev/edi/files?status=parsed&limit=20")
        assert r.status_code == 200
        for item in r.json()["items"]:
            assert item["parse_status"] == "parsed"

    def test_variant_filter(self, client: TestClient):
        r = client.get("/api/dev/edi/files?variant=837P&limit=10")
        assert r.status_code == 200
        for item in r.json()["items"]:
            assert item["service_variant_detected"] == "837P"

    def test_q_substring(self, client: TestClient):
        r = client.get("/api/dev/edi/files?q=zzzzz_nothing_matches_this")
        assert r.status_code == 200
        assert r.json()["items"] == []

    def test_limit_too_large_422(self, client: TestClient):
        r = client.get("/api/dev/edi/files?limit=999")
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# GET /edi/files/{id} + segments + events
# ---------------------------------------------------------------------------

class TestFileDetail:
    def test_404_missing(self, client: TestClient):
        r = client.get("/api/dev/edi/files/999999999")
        assert r.status_code == 404

    def test_detail_then_segments_then_events(self, client: TestClient):
        listing = client.get("/api/dev/edi/files?limit=1").json()
        if not listing["items"]:
            pytest.skip("no edi_files in DB to drill into")
        file_id = listing["items"][0]["id"]

        detail = client.get(f"/api/dev/edi/files/{file_id}")
        assert detail.status_code == 200
        d = detail.json()
        assert {"id", "raw_segments_count", "parse_events_count",
                 "claims_persisted_count", "remittance_claims_persisted_count"} <= set(d)
        assert d["id"] == file_id

        segs = client.get(f"/api/dev/edi/files/{file_id}/segments?limit=10")
        assert segs.status_code == 200
        sb = segs.json()
        assert {"items", "total", "limit", "offset"} <= set(sb)
        assert sb["total"] == d["raw_segments_count"]

        evs = client.get(f"/api/dev/edi/files/{file_id}/events?limit=10")
        assert evs.status_code == 200
        eb = evs.json()
        assert {"items", "total"} <= set(eb)
        assert eb["total"] == d["parse_events_count"]

    def test_segments_status_filter(self, client: TestClient):
        listing = client.get("/api/dev/edi/files?limit=1").json()
        if not listing["items"]:
            pytest.skip("no edi_files in DB")
        file_id = listing["items"][0]["id"]
        r = client.get(
            f"/api/dev/edi/files/{file_id}/segments"
            "?handler_status=skipped_unhandled&limit=5"
        )
        assert r.status_code == 200
        for item in r.json()["items"]:
            assert item["handler_status"] == "skipped_unhandled"


# ---------------------------------------------------------------------------
# POST /edi/upload — confirm guard
# ---------------------------------------------------------------------------

class TestUpload:
    def test_missing_confirm_400(self, client: TestClient):
        r = client.post(
            "/api/dev/edi/upload",
            files={"file": ("x.edi", b"not really edi", "text/plain")},
        )
        # Without ?confirm=true, must be rejected before parsing
        assert r.status_code == 400
        assert "confirm" in r.text.lower()


# ---------------------------------------------------------------------------
# No regression
# ---------------------------------------------------------------------------

class TestNoRegression:
    def test_env_info_still_works(self, client: TestClient):
        r = client.get("/api/dev/env/info")
        assert r.status_code == 200

    def test_uploads_recent_still_works(self, client: TestClient):
        r = client.get("/api/dev/uploads/recent?limit=3")
        assert r.status_code == 200
