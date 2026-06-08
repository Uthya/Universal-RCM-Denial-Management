"""Integration tests for /api/dev/db/* endpoints.

Per CR-041:
    * Happy-path response shape per endpoint
    * Empty-state behavior (MV with 0 rows, table with 0 rows)
    * API failure paths (write SQL rejected, multi-statement rejected, etc.)
    * Confirmation requirement on POSTs
    * Remote PG compatibility (tests run against the actual remote when
      RCM_INTEGRATION_DSN is set)

Skipped unless RCM_INTEGRATION_DSN is set.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


pytestmark = pytest.mark.skipif(
    "RCM_INTEGRATION_DSN" not in os.environ,
    reason="RCM_INTEGRATION_DSN not set — DB endpoint tests skipped",
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
# /db/overview
# ---------------------------------------------------------------------------

class TestOverview:
    def test_shape(self, client: TestClient):
        r = client.get("/api/dev/db/overview")
        assert r.status_code == 200
        b = r.json()
        # Required fields per DbOverviewResponse
        for key in (
            "alembic_head", "base_tables", "partitioned_parents", "partition_children",
            "materialized_views", "enum_types", "pl_pgsql_functions",
            "foreign_keys", "indexes", "unique_constraints",
            "database_size_bytes", "database_size_pretty",
        ):
            assert key in b, f"Missing field {key}"

        # Sanity: known counts (verified in CR-031 inventory)
        assert b["alembic_head"] == "0010_add_materialized_views"
        assert b["base_tables"] >= 25, f"Expected >=25 base tables, got {b['base_tables']}"
        assert b["partitioned_parents"] == 5, "5 partitioned parents per CR-008 inventory"
        assert b["partition_children"] >= 20
        assert b["materialized_views"] == 12, "12 MVs per CR-010 (0010_add_materialized_views)"
        assert b["enum_types"] == 23, "23 ENUMs per CR-014 remote validation"
        assert b["pl_pgsql_functions"] >= 6, "6 PL/pgSQL helpers per CR-009"
        assert b["indexes"] >= 100
        assert b["database_size_bytes"] > 0


# ---------------------------------------------------------------------------
# /db/tables
# ---------------------------------------------------------------------------

class TestTables:
    def test_default_shape(self, client: TestClient):
        r = client.get("/api/dev/db/tables")
        assert r.status_code == 200
        b = r.json()
        assert {"items", "total", "limit", "offset"} <= set(b)
        assert b["limit"] == 200
        assert b["offset"] == 0
        assert isinstance(b["items"], list)
        if b["items"]:
            row = b["items"][0]
            for key in ("schema", "name", "kind", "row_count_estimate",
                        "total_size_bytes", "total_size_pretty", "index_count"):
                assert key in row, f"Missing {key} in TableInfo row"
            assert row["kind"] in ("regular", "partitioned_parent", "partition_child")

    def test_filter_partitioned_parents(self, client: TestClient):
        r = client.get("/api/dev/db/tables?kind=partitioned_parent")
        assert r.status_code == 200
        b = r.json()
        names = {row["name"] for row in b["items"]}
        # Per CR-008/CR-010 these are the 5 known partitioned parents
        assert names == {"raw_segments", "parse_events", "prediction_log",
                          "audit_log", "request_log"}

    def test_filter_partition_children(self, client: TestClient):
        r = client.get("/api/dev/db/tables?kind=partition_child&limit=500")
        assert r.status_code == 200
        b = r.json()
        # Each of 5 parents has at least 5 children (default + 4 months)
        assert b["total"] >= 25

    def test_invalid_kind_rejected(self, client: TestClient):
        r = client.get("/api/dev/db/tables?kind=bogus")
        assert r.status_code == 400
        assert "kind" in r.json()["detail"].lower()

    def test_invalid_sort_rejected(self, client: TestClient):
        r = client.get("/api/dev/db/tables?sort=bogus")
        assert r.status_code == 400


class TestTableDetail:
    def test_known_table(self, client: TestClient):
        r = client.get("/api/dev/db/tables/claims")
        assert r.status_code == 200
        b = r.json()
        assert b["name"] == "claims"
        assert b["kind"] == "regular"
        # claims has many columns + indexes + FKs (verified CR-031)
        assert len(b["columns"]) >= 20
        assert len(b["indexes"]) >= 5
        assert any(c["name"] == "claim_number" for c in b["columns"])
        # Both inbound and outbound FKs exist
        assert len(b["foreign_keys_out"]) >= 6   # payer, patient, subscriber, 3× provider
        assert len(b["foreign_keys_in"]) >= 1    # claim_lines, etc.

    def test_partitioned_parent_shows_children(self, client: TestClient):
        r = client.get("/api/dev/db/tables/raw_segments")
        assert r.status_code == 200
        b = r.json()
        assert b["kind"] == "partitioned_parent"
        assert len(b["partition_children"]) >= 5

    def test_partition_child_shows_bounds(self, client: TestClient):
        # Get any partition child by listing first
        r = client.get("/api/dev/db/tables?kind=partition_child&limit=1")
        child_name = r.json()["items"][0]["name"]
        r = client.get(f"/api/dev/db/tables/{child_name}")
        assert r.status_code == 200
        b = r.json()
        assert b["kind"] == "partition_child"
        if not child_name.endswith("_default"):
            assert b["partition_bounds"] is not None
            assert "FROM" in b["partition_bounds"]

    def test_unknown_table_404(self, client: TestClient):
        r = client.get("/api/dev/db/tables/does_not_exist")
        assert r.status_code == 404

    def test_invalid_table_name_400(self, client: TestClient):
        r = client.get("/api/dev/db/tables/foo;DROP")
        # The path-param regex of FastAPI accepts this, the inner _is_valid_identifier rejects
        assert r.status_code in (400, 404)


# ---------------------------------------------------------------------------
# /db/materialized-views
# ---------------------------------------------------------------------------

class TestMaterializedViews:
    def test_list(self, client: TestClient):
        r = client.get("/api/dev/db/materialized-views")
        assert r.status_code == 200
        b = r.json()
        assert b["total"] == 12, "12 MVs per CR-010"
        names = {row["name"] for row in b["items"]}
        assert "mv_claim_labels" in names
        assert "mv_payer_cpt_denial_rate" in names

    def test_empty_state_attributes_set(self, client: TestClient):
        """Newly-created MVs that haven't been REFRESHED have is_populated=False
        and row_count_estimate=-1 (PG's reltuples convention) or 0."""
        r = client.get("/api/dev/db/materialized-views")
        assert r.status_code == 200
        for row in r.json()["items"]:
            assert "is_populated" in row
            assert "row_count_estimate" in row
            assert "has_unique_index" in row
            # All 12 MVs have unique indexes per CR-010
            assert row["has_unique_index"] is True


class TestMvRefresh:
    def test_requires_confirm(self, client: TestClient):
        r = client.post("/api/dev/db/refresh-mv/mv_claim_labels")
        assert r.status_code == 400
        assert "confirm" in r.json()["detail"].lower()

    def test_404_for_unknown_mv(self, client: TestClient):
        r = client.post("/api/dev/db/refresh-mv/nope?confirm=true")
        assert r.status_code == 404

    def test_400_for_invalid_name(self, client: TestClient):
        r = client.post("/api/dev/db/refresh-mv/foo$bar?confirm=true")
        assert r.status_code == 400

    def test_refresh_succeeds(self, client: TestClient):
        r = client.post("/api/dev/db/refresh-mv/mv_drift_baselines?confirm=true")
        assert r.status_code == 200
        b = r.json()
        assert b["name"] == "mv_drift_baselines"
        assert b["concurrent"] is True   # has unique index
        assert b["duration_ms"] >= 0
        assert b["row_count_estimate_after"] >= 0


# ---------------------------------------------------------------------------
# /db/functions
# ---------------------------------------------------------------------------

class TestFunctions:
    def test_lists_expected_functions(self, client: TestClient):
        r = client.get("/api/dev/db/functions")
        assert r.status_code == 200
        b = r.json()
        names = {row["name"] for row in b["items"]}
        # 6 PL/pgSQL helpers per CR-009
        for expected in ("audit_trigger_fn", "touch_updated_at_fn",
                          "derive_service_variant", "is_denied",
                          "claim_lifecycle_resolved", "find_similar_claims"):
            assert expected in names, f"Function {expected} not found in {names}"

        for row in b["items"]:
            assert row["language"] in ("plpgsql", "sql", "c", "internal")
            assert row["volatility"] in ("immutable", "stable", "volatile")


# ---------------------------------------------------------------------------
# /db/indexes
# ---------------------------------------------------------------------------

class TestIndexes:
    def test_pagination(self, client: TestClient):
        r = client.get("/api/dev/db/indexes?limit=10&offset=0")
        assert r.status_code == 200
        b = r.json()
        assert b["limit"] == 10
        assert len(b["items"]) <= 10
        assert b["total"] >= 100

    def test_index_row_shape(self, client: TestClient):
        r = client.get("/api/dev/db/indexes?limit=5")
        for row in r.json()["items"]:
            assert "name" in row
            assert "table_name" in row
            assert row["definition"].lower().startswith("create")
            assert "is_unique" in row
            assert "is_partial" in row
            assert row["size_bytes"] >= 0


# ---------------------------------------------------------------------------
# /db/migrations
# ---------------------------------------------------------------------------

class TestMigrations:
    def test_lists_all_revisions(self, client: TestClient):
        r = client.get("/api/dev/db/migrations")
        assert r.status_code == 200
        b = r.json()
        assert b["current_head"] == "0010_add_materialized_views"
        # 10 revisions (per CR-004)
        assert b["total_revisions"] == 10
        revisions = [item["revision"] for item in b["items"]]
        assert revisions[0] == "0001_init_core"
        assert revisions[-1] == "0010_add_materialized_views"

    def test_current_flag_set(self, client: TestClient):
        r = client.get("/api/dev/db/migrations")
        b = r.json()
        currents = [item for item in b["items"] if item["is_current"]]
        assert len(currents) == 1
        assert currents[0]["revision"] == "0010_add_materialized_views"


# ---------------------------------------------------------------------------
# /db/query (SQL console)
# ---------------------------------------------------------------------------

class TestSqlConsoleHappyPath:
    def test_simple_select(self, client: TestClient):
        r = client.post(
            "/api/dev/db/query?confirm=true",
            json={"sql": "SELECT 1 AS x, 'hi' AS y"},
        )
        assert r.status_code == 200
        b = r.json()
        assert b["columns"] == ["x", "y"]
        assert b["rows"] == [[1, "hi"]]
        assert b["row_count"] == 1
        assert b["truncated"] is False
        assert b["row_limit"] == 1000

    def test_empty_result_set(self, client: TestClient):
        r = client.post(
            "/api/dev/db/query?confirm=true",
            json={"sql": "SELECT 1 WHERE false"},
        )
        assert r.status_code == 200
        b = r.json()
        assert b["row_count"] == 0
        assert b["rows"] == []
        assert b["columns"] == []   # no rows → empty columns


class TestSqlConsoleSafety:
    def test_requires_confirm(self, client: TestClient):
        r = client.post("/api/dev/db/query", json={"sql": "SELECT 1"})
        assert r.status_code == 400
        assert "confirm" in r.json()["detail"].lower()

    @pytest.mark.parametrize("sql", [
        "DELETE FROM claims",
        "UPDATE claims SET status='denied'",
        "INSERT INTO claims VALUES (1)",
        "DROP TABLE claims",
        "TRUNCATE claims",
        "ALTER TABLE claims ADD COLUMN x INT",
        "CREATE TABLE foo (id INT)",
        "VACUUM claims",
        "COPY claims TO '/tmp/x'",
        "GRANT SELECT ON claims TO postgres",
    ])
    def test_rejects_write_statements(self, client: TestClient, sql):
        r = client.post("/api/dev/db/query?confirm=true", json={"sql": sql})
        assert r.status_code == 400
        assert "rejected" in r.json()["detail"].lower() or "only select" in r.json()["detail"].lower()

    def test_rejects_multiple_statements(self, client: TestClient):
        r = client.post(
            "/api/dev/db/query?confirm=true",
            json={"sql": "SELECT 1; SELECT 2"},
        )
        assert r.status_code == 400

    def test_rejects_empty(self, client: TestClient):
        # Whitespace-only — caught by the safety enforcer (after strip).
        # Truly empty "" would hit Pydantic min_length=1 first → 422.
        r = client.post("/api/dev/db/query?confirm=true", json={"sql": "   "})
        assert r.status_code == 400

    def test_rejects_truly_empty_via_pydantic(self, client: TestClient):
        r = client.post("/api/dev/db/query?confirm=true", json={"sql": ""})
        assert r.status_code == 422


class TestSqlConsoleFailure:
    def test_invalid_sql_returns_500(self, client: TestClient):
        r = client.post(
            "/api/dev/db/query?confirm=true",
            json={"sql": "SELECT nonexistent_column FROM nonexistent_table"},
        )
        assert r.status_code == 500
        assert "PG error" in r.json()["detail"]


# ---------------------------------------------------------------------------
# No-regression sentinel
# ---------------------------------------------------------------------------

class TestNoRegression:
    """Confirms that loading the dev console didn't break the env endpoint."""

    def test_env_info_still_works(self, client: TestClient):
        r = client.get("/api/dev/env/info")
        assert r.status_code == 200
        assert r.json()["database"]["alembic_head"] == "0010_add_materialized_views"
