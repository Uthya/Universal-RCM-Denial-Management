"""Integration tests for U7 — /api/dev/claims/* (Claims Browser)."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


pytestmark = pytest.mark.skipif(
    "RCM_INTEGRATION_DSN" not in os.environ,
    reason="RCM_INTEGRATION_DSN not set — claims browser endpoint tests skipped",
)


@pytest.fixture
def client():
    os.environ["DATABASE_URL"] = os.environ["RCM_INTEGRATION_DSN"]
    os.environ.setdefault("JWT_SECRET_KEY", "dev-console-test-secret-32-chars-long")
    from rcm.main import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


class TestClaimsList:
    def test_default_shape(self, client: TestClient):
        r = client.get("/api/dev/claims")
        assert r.status_code == 200
        b = r.json()
        assert {"items", "total", "limit", "offset"} <= set(b)
        for item in b["items"]:
            assert {"id", "claim_number", "service_variant", "claim_subtype",
                     "claim_status", "total_charge_amount", "submission_date",
                     "edi_file_id", "line_count", "diagnosis_count",
                     "has_remittance"} <= set(item)

    def test_variant_filter(self, client: TestClient):
        r = client.get("/api/dev/claims?variant=837P&limit=5")
        assert r.status_code == 200
        for item in r.json()["items"]:
            assert item["service_variant"] == "837P"

    def test_q_substring_no_match(self, client: TestClient):
        r = client.get("/api/dev/claims?q=zzzzz_definitely_not_a_claim")
        assert r.status_code == 200
        assert r.json()["items"] == []

    def test_limit_too_large_422(self, client: TestClient):
        r = client.get("/api/dev/claims?limit=999")
        assert r.status_code == 422

    def test_invalid_payer_id_422(self, client: TestClient):
        r = client.get("/api/dev/claims?payer_id=abc")
        assert r.status_code == 422


class TestClaimDetail:
    def test_404_missing(self, client: TestClient):
        r = client.get("/api/dev/claims/999999999")
        assert r.status_code == 404

    def test_drill_in_all_tabs(self, client: TestClient):
        listing = client.get("/api/dev/claims?limit=1").json()
        if not listing["items"]:
            pytest.skip("no claims in DB to drill into")
        claim_id = listing["items"][0]["id"]

        detail = client.get(f"/api/dev/claims/{claim_id}")
        assert detail.status_code == 200
        d = detail.json()
        assert {"id", "edi_file_id", "claim_number", "service_variant",
                 "claim_subtype", "line_count", "diagnosis_count",
                 "remittance_count", "raw_segment_count",
                 "parse_event_count"} <= set(d)

        lines = client.get(f"/api/dev/claims/{claim_id}/lines")
        assert lines.status_code == 200
        assert lines.json()["total"] == d["line_count"]

        diags = client.get(f"/api/dev/claims/{claim_id}/diagnoses")
        assert diags.status_code == 200
        assert diags.json()["total"] == d["diagnosis_count"]

        remits = client.get(f"/api/dev/claims/{claim_id}/remits")
        assert remits.status_code == 200
        assert remits.json()["total"] == d["remittance_count"]


class TestNoRegression:
    def test_env_info_still_works(self, client: TestClient):
        r = client.get("/api/dev/env/info")
        assert r.status_code == 200

    def test_edi_files_list_still_works(self, client: TestClient):
        r = client.get("/api/dev/edi/files?limit=1")
        assert r.status_code == 200
