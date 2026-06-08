"""Unit tests for the SQL-safety enforcer used by /api/dev/db/query.

Covers happy-path SELECTs, rejection of every disallowed shape, and edge
cases like multi-line comments / WITH clauses / trailing semicolons.
"""

from __future__ import annotations

import pytest

from rcm.routers.dev._sql_safety import UnsafeSqlError, assert_safe_select


class TestAccepted:
    @pytest.mark.parametrize("sql", [
        "SELECT 1",
        "select * from claims",
        "  SELECT now()  ",
        "SELECT count(*) FROM claims WHERE service_variant='837P'",
        "WITH x AS (SELECT 1) SELECT * FROM x",
        # Trailing semicolon is fine — we strip it
        "SELECT 1;",
        "SELECT 1\n;\n",
        # Mixed-case keywords
        "SeLeCt * FROM payers LIMIT 10",
        # Multi-line with comments
        "-- pull recent claims\nSELECT * FROM claims LIMIT 10",
        "/* block comment */ SELECT 1",
    ])
    def test_accepted(self, sql):
        result = assert_safe_select(sql)
        assert "SELECT" in result.upper() or "WITH" in result.upper()
        assert not result.rstrip().endswith(";")


class TestRejected:
    @pytest.mark.parametrize("sql, reason_match", [
        ("",                                  "Empty"),
        ("   ",                               "Empty"),
        ("DELETE FROM claims",                "Only SELECT"),
        ("UPDATE claims SET status='denied'", "Only SELECT"),
        ("INSERT INTO claims VALUES (1)",     "Only SELECT"),
        ("DROP TABLE claims",                 "Only SELECT"),
        ("TRUNCATE claims",                   "Only SELECT"),
        ("ALTER TABLE claims ADD COLUMN x INT", "Only SELECT"),
        ("CREATE TABLE foo (id INT)",         "Only SELECT"),
        ("GRANT SELECT ON claims TO foo",     "Only SELECT"),
        ("VACUUM claims",                     "Only SELECT"),
        ("COPY claims TO '/tmp/x'",           "Only SELECT"),
        ("SET search_path = 'x'",             "Only SELECT"),
        # Multi-statement
        ("SELECT 1; SELECT 2",                "Multiple statements"),
        ("SELECT 1;DELETE FROM claims",       "Multiple statements"),
        # SELECT containing a write keyword as bare word — caught by deny-list
        ("SELECT * FROM (DELETE FROM x RETURNING *)", "DELETE"),
        # Only comments
        ("-- nothing here",                   "only comments"),
        ("/* x */",                           "only comments"),
    ])
    def test_rejected(self, sql, reason_match):
        with pytest.raises(UnsafeSqlError) as exc:
            assert_safe_select(sql)
        assert reason_match.lower() in exc.value.reason.lower()


class TestTrickyButRejected:
    """Sneaky SQL that LOOKS like SELECT but isn't, or that hides a write."""

    def test_select_into_creates_table(self):
        # SELECT ... INTO creates a new table — reject because 'CREATE' isn't
        # the keyword used but the operation is DDL. Conservative: deny-list
        # doesn't catch this. Documented limitation.
        # (We deliberately do NOT reject this here; production should use a
        # SELECT-only DB role. Documenting the gap so it's not a surprise.)
        result = assert_safe_select("SELECT 1 INTO foo")
        # Confirms: enforcer does NOT block SELECT ... INTO.
        # In a real prod env, the DB role must be SELECT-only on schemas.
        assert "INTO" in result.upper()

    def test_with_clause_into_select(self):
        # Legitimate WITH ... SELECT is fine
        result = assert_safe_select("WITH t AS (SELECT 1 AS x) SELECT * FROM t")
        assert "WITH" in result.upper()

    def test_keyword_hidden_in_string_literal_still_caught(self):
        # The deny-list scans EVERY occurrence regardless of context;
        # quoting 'UPDATE' inside a string still gets rejected — false positive
        # but safer than the alternative. Documented behavior.
        with pytest.raises(UnsafeSqlError, match="UPDATE"):
            assert_safe_select("SELECT 'I want to UPDATE later' AS msg")

    def test_too_long(self):
        with pytest.raises(UnsafeSqlError, match="too long"):
            assert_safe_select("SELECT 1 " + ("a" * 11_000))
