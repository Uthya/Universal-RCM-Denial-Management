"""SQL-safety enforcer for the dev console's read-only query endpoint.

Conservative deny-list approach:
    * Must be exactly one statement (no semicolons except optional trailing)
    * Must begin with SELECT or WITH (case-insensitive, after leading whitespace
      and comments)
    * Must NOT contain any of the write/DDL keywords as bare words

A motivated attacker can usually find a clever bypass, but this is a
developer-only LOCAL surface and the deny-list filters out 100% of accidents
and most casual attempts. The real defense is database role permissions in
production (the dev console should connect as a SELECT-only role); this
enforcer is a usability + sanity layer on top of that.
"""

from __future__ import annotations

import re


class UnsafeSqlError(ValueError):
    """Raised when a SQL string fails the safety check."""

    def __init__(self, reason: str, sql: str):
        super().__init__(reason)
        self.reason = reason
        self.sql = sql


# Words that, if present as a bare token, are immediate rejections.
# Matched case-insensitively, only at word boundaries.
_DENIED_KEYWORDS: frozenset[str] = frozenset({
    "INSERT", "UPDATE", "DELETE", "MERGE", "UPSERT",
    "DROP", "TRUNCATE", "ALTER", "CREATE",
    "GRANT", "REVOKE",
    "COMMIT", "ROLLBACK", "SAVEPOINT",
    "VACUUM", "ANALYZE", "CLUSTER", "REINDEX",
    "COPY",           # PG's COPY can write
    "CALL", "DO",     # CALL can invoke side-effectful procs
    "LISTEN", "NOTIFY", "UNLISTEN",
    "LOCK", "REFRESH",
    "DISCARD", "RESET", "SET",      # SET LOCAL only allowed internally; user can't use it
    "SECURITY",       # SECURITY DEFINER bypass
})

_DENIED_PATTERN = re.compile(
    r"\b(" + "|".join(sorted(_DENIED_KEYWORDS)) + r")\b",
    re.IGNORECASE,
)

# Match SQL line comments (-- ...) and block comments (/* ... */)
_LINE_COMMENT = re.compile(r"--[^\n]*", re.MULTILINE)
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_comments(sql: str) -> str:
    s = _BLOCK_COMMENT.sub(" ", sql)
    s = _LINE_COMMENT.sub(" ", s)
    return s


def assert_safe_select(sql: str) -> str:
    """Raise UnsafeSqlError if `sql` is not a safe read-only single statement.

    Returns the trimmed SQL with any trailing semicolon removed (callers can
    pass directly to asyncpg).
    """
    if sql is None:
        raise UnsafeSqlError("Empty SQL", sql or "")

    stripped = sql.strip()
    if not stripped:
        raise UnsafeSqlError("Empty SQL", sql)
    if len(stripped) > 10_000:
        raise UnsafeSqlError("SQL too long (>10000 chars)", sql)

    # Strip comments for the keyword scan; keep the original for execution
    no_comments = _strip_comments(stripped).strip()
    if not no_comments:
        raise UnsafeSqlError("SQL is only comments", sql)

    # Single-statement rule: at most one trailing semicolon
    trailing_semi_stripped = no_comments.rstrip(";").rstrip()
    if ";" in trailing_semi_stripped:
        raise UnsafeSqlError(
            "Multiple statements are not allowed (only one SELECT per request)",
            sql,
        )

    # Must start with SELECT or WITH
    first_token = re.split(r"\s+", trailing_semi_stripped, maxsplit=1)[0].upper()
    if first_token not in ("SELECT", "WITH"):
        raise UnsafeSqlError(
            f"Only SELECT (and WITH ... SELECT) queries are allowed; got {first_token!r}",
            sql,
        )

    # Deny-list keyword scan over the comment-stripped version
    match = _DENIED_PATTERN.search(no_comments)
    if match:
        raise UnsafeSqlError(
            f"Disallowed keyword {match.group(1).upper()!r} in SQL "
            "(this endpoint is read-only)",
            sql,
        )

    # Returned form: original trimmed minus any trailing semicolons
    return stripped.rstrip(";").rstrip()
