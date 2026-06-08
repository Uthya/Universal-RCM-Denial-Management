"""Database State endpoints — Page 9 of the dev console.

Per CR-041 requirements:
    * Backend is the source of truth — every metric is read from PG's
      catalog tables, no frontend aggregation.
    * Pydantic response schemas; typed contract.
    * SQL console enforces SELECT-only + statement_timeout + LIMIT cap.
    * Refresh-MV requires ?confirm=true.

Endpoints:
    GET  /db/overview            counts at a glance
    GET  /db/tables              paginated list with row counts + size
    GET  /db/tables/{name}       single-table drill-in (cols, idx, FKs, partitions)
    GET  /db/materialized-views  MV list + populated/unique-index status
    POST /db/refresh-mv/{name}?confirm=true   refresh one MV
    GET  /db/functions           PL/pgSQL function inventory
    GET  /db/indexes             paginated index inventory
    GET  /db/migrations          alembic revision history + current head
    POST /db/query?confirm=true  SELECT-only ad-hoc query
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
from fastapi import APIRouter, HTTPException, Query

from rcm.core.config import settings
from rcm.routers.dev import require_confirm
from rcm.routers.dev._sql_safety import UnsafeSqlError, assert_safe_select
from rcm.schemas.dev import (
    ColumnInfo,
    DbFunctionsResponse,
    DbIndexesResponse,
    DbMaterializedViewsResponse,
    DbMigrationsResponse,
    DbOverviewResponse,
    DbTablesListResponse,
    ForeignKeyInfo,
    FunctionInfo,
    GlobalIndexInfo,
    IndexInfo,
    MaterializedViewInfo,
    MigrationRevision,
    MvRefreshResponse,
    SqlQueryRequest,
    SqlQueryResponse,
    TableDetailResponse,
    TableInfo,
)

router = APIRouter()


# SQL-console limits
SQL_STATEMENT_TIMEOUT_MS = 30_000
SQL_ROW_LIMIT = 1_000


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

async def _connect() -> asyncpg.Connection:
    """One fresh asyncpg connection per request. SQLAlchemy pool reserved
    for ORM paths; dev console uses asyncpg directly so a stuck request
    doesn't block other dev panels."""
    raw_dsn = settings.sync_database_url()
    try:
        return await asyncpg.connect(dsn=raw_dsn, timeout=8)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database unreachable: {type(exc).__name__}: {exc}",
        )


# ---------------------------------------------------------------------------
# /db/overview
# ---------------------------------------------------------------------------

@router.get(
    "/overview",
    response_model=DbOverviewResponse,
    summary="Counts at a glance",
    description=(
        "Returns top-level counts across the public schema: base tables, "
        "partitioned parents + children, materialized views, enum types, "
        "functions, foreign keys, indexes, unique constraints, total DB size, "
        "and the current alembic head. Computed from pg_class / pg_type / "
        "pg_constraint / pg_indexes / pg_proc / pg_inherits / alembic_version."
    ),
)
async def db_overview() -> DbOverviewResponse:
    c = await _connect()
    try:
        base_tables = await c.fetchval("""
            SELECT count(*) FROM pg_class
            WHERE relnamespace = 'public'::regnamespace
              AND relkind = 'r'
              AND relname NOT LIKE '%_p20%' AND relname NOT LIKE '%_default'
              AND relname <> 'alembic_version'
        """)
        partitioned_parents = await c.fetchval("""
            SELECT count(*) FROM pg_class
            WHERE relnamespace = 'public'::regnamespace AND relkind = 'p'
        """)
        partition_children = await c.fetchval("""
            SELECT count(*) FROM pg_inherits
            WHERE inhparent IN (
                SELECT oid FROM pg_class
                WHERE relnamespace='public'::regnamespace AND relkind='p'
            )
        """)
        materialized_views = await c.fetchval(
            "SELECT count(*) FROM pg_matviews WHERE schemaname='public'"
        )
        enum_types = await c.fetchval("""
            SELECT count(*) FROM pg_type t
            JOIN pg_namespace n ON n.oid=t.typnamespace
            WHERE n.nspname='public' AND t.typtype='e'
        """)
        plpgsql_functions = await c.fetchval("""
            SELECT count(*) FROM pg_proc p
            JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.prokind='f'
        """)
        foreign_keys = await c.fetchval("""
            SELECT count(*) FROM information_schema.table_constraints
            WHERE table_schema='public' AND constraint_type='FOREIGN KEY'
        """)
        indexes = await c.fetchval(
            "SELECT count(*) FROM pg_indexes WHERE schemaname='public'"
        )
        unique_constraints = await c.fetchval("""
            SELECT count(*) FROM information_schema.table_constraints
            WHERE table_schema='public' AND constraint_type='UNIQUE'
        """)
        db_size_bytes = await c.fetchval(
            "SELECT pg_database_size(current_database())"
        )
        db_size_pretty = await c.fetchval(
            "SELECT pg_size_pretty(pg_database_size(current_database()))"
        )
        alembic_head = await c.fetchval(
            "SELECT version_num FROM alembic_version LIMIT 1"
        ) if await c.fetchval(
            "SELECT to_regclass('public.alembic_version') IS NOT NULL"
        ) else None
    finally:
        await c.close()

    return DbOverviewResponse(
        alembic_head=alembic_head,
        base_tables=int(base_tables or 0),
        partitioned_parents=int(partitioned_parents or 0),
        partition_children=int(partition_children or 0),
        materialized_views=int(materialized_views or 0),
        enum_types=int(enum_types or 0),
        pl_pgsql_functions=int(plpgsql_functions or 0),
        foreign_keys=int(foreign_keys or 0),
        indexes=int(indexes or 0),
        unique_constraints=int(unique_constraints or 0),
        database_size_bytes=int(db_size_bytes or 0),
        database_size_pretty=str(db_size_pretty or ""),
    )


# ---------------------------------------------------------------------------
# /db/tables (list)
# ---------------------------------------------------------------------------

@router.get(
    "/tables",
    response_model=DbTablesListResponse,
    summary="Table inventory (paginated)",
    description=(
        "Returns one row per table in the public schema: name, kind (regular / "
        "partitioned_parent / partition_child), row-count ESTIMATE from "
        "pg_stat_user_tables.n_live_tup (not exact — analyzer-driven), total "
        "size (table + indexes + TOAST), and index count. Supports filtering by "
        "kind and sorting by size/row_count/name."
    ),
)
async def db_tables(
    kind: str | None = Query(None, description="regular | partitioned_parent | partition_child"),
    sort: str = Query("name", description="name | row_count | size"),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> DbTablesListResponse:
    if sort not in ("name", "row_count", "size"):
        raise HTTPException(400, detail=f"sort must be one of name|row_count|size; got {sort!r}")

    where = ""
    if kind == "regular":
        where = """
            AND c.relkind = 'r'
            AND c.relname NOT LIKE '%_p20%' AND c.relname NOT LIKE '%_default'
        """
    elif kind == "partitioned_parent":
        where = "AND c.relkind = 'p'"
    elif kind == "partition_child":
        where = """
            AND c.relkind = 'r'
            AND (c.relname LIKE '%_p20%' OR c.relname LIKE '%_default')
        """
    elif kind is not None:
        raise HTTPException(400, detail=f"kind must be regular|partitioned_parent|partition_child; got {kind!r}")

    order_sql = {
        "name": "c.relname",
        "row_count": "n_live_tup DESC NULLS LAST",
        "size": "total_bytes DESC",
    }[sort]

    sql = f"""
        SELECT
            n.nspname  AS schema,
            c.relname  AS name,
            CASE c.relkind
                WHEN 'p' THEN 'partitioned_parent'
                WHEN 'r' THEN CASE
                    WHEN c.relname LIKE '%_p20%' OR c.relname LIKE '%_default' THEN 'partition_child'
                    ELSE 'regular'
                END
            END AS kind,
            (SELECT inhparent::regclass::text FROM pg_inherits WHERE inhrelid = c.oid LIMIT 1) AS parent,
            COALESCE(s.n_live_tup, 0)::bigint AS row_count_estimate,
            pg_total_relation_size(c.oid)::bigint AS total_bytes,
            pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size_pretty,
            (SELECT count(*) FROM pg_indexes i WHERE i.tablename = c.relname AND i.schemaname='public')::int AS index_count
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
        WHERE n.nspname = 'public' AND c.relkind IN ('r','p')
          AND c.relname <> 'alembic_version'
          {where}
        ORDER BY {order_sql}
        LIMIT $1 OFFSET $2
    """

    count_sql = f"""
        SELECT count(*) FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p')
          AND c.relname <> 'alembic_version'
          {where}
    """

    c = await _connect()
    try:
        total = await c.fetchval(count_sql)
        rows = await c.fetch(sql, limit, offset)
    finally:
        await c.close()

    items = [
        TableInfo(
            schema=r["schema"],
            name=r["name"],
            kind=r["kind"],
            parent=r["parent"],
            row_count_estimate=int(r["row_count_estimate"]),
            total_size_bytes=int(r["total_bytes"]),
            total_size_pretty=r["total_size_pretty"],
            index_count=int(r["index_count"]),
        )
        for r in rows
    ]
    return DbTablesListResponse(items=items, total=int(total or 0), limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# /db/tables/{name}
# ---------------------------------------------------------------------------

@router.get(
    "/tables/{name}",
    response_model=TableDetailResponse,
    summary="Single-table detail (columns, indexes, FKs, partitions)",
    description=(
        "Returns the structural detail of one table: column list with types + "
        "nullability + defaults, indexes with full definitions + size, foreign "
        "keys both outbound and inbound, partition children (if this is a "
        "parent), and the FOR VALUES bounds (if this is a child)."
    ),
    responses={404: {"description": "Table not found"}},
)
async def db_table_detail(name: str) -> TableDetailResponse:
    if not _is_valid_identifier(name):
        raise HTTPException(400, detail=f"Invalid table name {name!r}")

    c = await _connect()
    try:
        head = await c.fetchrow("""
            SELECT
                n.nspname AS schema,
                c.relname AS name,
                CASE c.relkind
                    WHEN 'p' THEN 'partitioned_parent'
                    WHEN 'r' THEN CASE
                        WHEN c.relname LIKE '%_p20%' OR c.relname LIKE '%_default' THEN 'partition_child'
                        ELSE 'regular'
                    END
                END AS kind,
                COALESCE(s.n_live_tup, 0)::bigint AS row_count_estimate,
                pg_total_relation_size(c.oid)::bigint AS total_bytes,
                pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size_pretty
            FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace
            LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
            WHERE n.nspname='public' AND c.relname=$1 AND c.relkind IN ('r','p')
        """, name)
        if head is None:
            raise HTTPException(404, detail=f"Table {name!r} not found in public schema")

        col_rows = await c.fetch("""
            SELECT
                a.attname AS name,
                format_type(a.atttypid, a.atttypmod) AS data_type,
                NOT a.attnotnull AS is_nullable,
                ad.adbin IS NOT NULL AS has_default,
                pg_get_expr(ad.adbin, ad.adrelid) AS column_default
            FROM pg_attribute a
            LEFT JOIN pg_attrdef ad ON ad.adrelid=a.attrelid AND ad.adnum=a.attnum
            WHERE a.attrelid = ($1::regclass) AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum
        """, f"public.{name}")
        columns = [
            ColumnInfo(
                name=r["name"], data_type=r["data_type"],
                is_nullable=bool(r["is_nullable"]),
                has_default=bool(r["has_default"]),
                column_default=r["column_default"],
            )
            for r in col_rows
        ]

        idx_rows = await c.fetch("""
            SELECT
                i.indexname AS name,
                i.indexdef AS definition,
                ix.indisunique AS is_unique,
                pg_relation_size(c2.oid) AS size_bytes,
                pg_size_pretty(pg_relation_size(c2.oid)) AS size_pretty,
                position('WHERE' IN i.indexdef) > 0 AS is_partial
            FROM pg_indexes i
            JOIN pg_class c2 ON c2.relname=i.indexname
            JOIN pg_index ix ON ix.indexrelid=c2.oid
            WHERE i.schemaname='public' AND i.tablename=$1
        """, name)
        indexes = [
            IndexInfo(
                name=r["name"], definition=r["definition"],
                is_unique=bool(r["is_unique"]), is_partial=bool(r["is_partial"]),
                size_bytes=int(r["size_bytes"]), size_pretty=r["size_pretty"],
            )
            for r in idx_rows
        ]

        fk_out = await c.fetch("""
            SELECT
                tc.constraint_name AS name,
                kcu.column_name AS column,
                ccu.table_name AS referenced_table,
                ccu.column_name AS referenced_column,
                rc.delete_rule AS on_delete,
                rc.update_rule AS on_update
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu ON kcu.constraint_name=tc.constraint_name
            JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name=tc.constraint_name
            JOIN information_schema.referential_constraints rc ON rc.constraint_name=tc.constraint_name
            WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_schema='public' AND tc.table_name=$1
        """, name)
        fk_in = await c.fetch("""
            SELECT
                tc.constraint_name AS name,
                kcu.column_name AS column,
                tc.table_name AS referenced_table,
                ccu.column_name AS referenced_column,
                rc.delete_rule AS on_delete,
                rc.update_rule AS on_update
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu ON kcu.constraint_name=tc.constraint_name
            JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name=tc.constraint_name
            JOIN information_schema.referential_constraints rc ON rc.constraint_name=tc.constraint_name
            WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_schema='public' AND ccu.table_name=$1
        """, name)

        children = []
        partition_bounds = None
        if head["kind"] == "partitioned_parent":
            child_rows = await c.fetch("""
                SELECT inhrelid::regclass::text AS child FROM pg_inherits
                WHERE inhparent = ($1::regclass) ORDER BY child
            """, f"public.{name}")
            children = [r["child"] for r in child_rows]
        elif head["kind"] == "partition_child":
            partition_bounds = await c.fetchval("""
                SELECT pg_get_expr(c.relpartbound, c.oid)
                FROM pg_class c WHERE c.relname=$1 AND c.relnamespace='public'::regnamespace
            """, name)
    finally:
        await c.close()

    return TableDetailResponse(
        schema=head["schema"],
        name=head["name"],
        kind=head["kind"],
        row_count_estimate=int(head["row_count_estimate"]),
        total_size_bytes=int(head["total_bytes"]),
        total_size_pretty=head["total_size_pretty"],
        columns=columns,
        indexes=indexes,
        foreign_keys_out=[
            ForeignKeyInfo(
                name=r["name"], column=r["column"],
                referenced_table=r["referenced_table"], referenced_column=r["referenced_column"],
                on_delete=r["on_delete"], on_update=r["on_update"],
            ) for r in fk_out
        ],
        foreign_keys_in=[
            ForeignKeyInfo(
                name=r["name"], column=r["column"],
                referenced_table=r["referenced_table"], referenced_column=r["referenced_column"],
                on_delete=r["on_delete"], on_update=r["on_update"],
            ) for r in fk_in
        ],
        partition_children=children,
        partition_bounds=partition_bounds,
    )


def _is_valid_identifier(name: str) -> bool:
    """Defensive: PG identifier shape only. Used for path params we use
    inline in SQL string-formatting (none currently — every query uses
    parameter binding — but kept for additional safety)."""
    if not name or len(name) > 63:
        return False
    return all(ch.isalnum() or ch == "_" for ch in name)


# ---------------------------------------------------------------------------
# /db/materialized-views
# ---------------------------------------------------------------------------

@router.get(
    "/materialized-views",
    response_model=DbMaterializedViewsResponse,
    summary="Materialized view inventory",
    description=(
        "Returns each MV with: name, populated status (true once REFRESH has "
        "run at least once), row-count estimate from pg_class.reltuples, total "
        "size, and whether it has a unique index (required for REFRESH "
        "CONCURRENTLY — without one, only blocking refresh works)."
    ),
)
async def db_materialized_views() -> DbMaterializedViewsResponse:
    c = await _connect()
    try:
        rows = await c.fetch("""
            SELECT
                m.matviewname AS name,
                m.ispopulated AS is_populated,
                COALESCE(c.reltuples, 0)::bigint AS row_count_estimate,
                pg_total_relation_size(c.oid)::bigint AS size_bytes,
                pg_size_pretty(pg_total_relation_size(c.oid)) AS size_pretty,
                EXISTS (
                    SELECT 1 FROM pg_indexes i
                    WHERE i.tablename=m.matviewname AND i.schemaname='public'
                      AND i.indexdef ILIKE 'CREATE UNIQUE%'
                ) AS has_unique_index
            FROM pg_matviews m
            JOIN pg_class c ON c.relname=m.matviewname
                AND c.relnamespace='public'::regnamespace
            WHERE m.schemaname='public'
            ORDER BY m.matviewname
        """)
    finally:
        await c.close()

    items = [
        MaterializedViewInfo(
            name=r["name"],
            is_populated=bool(r["is_populated"]),
            row_count_estimate=int(r["row_count_estimate"]),
            total_size_bytes=int(r["size_bytes"]),
            total_size_pretty=r["size_pretty"],
            has_unique_index=bool(r["has_unique_index"]),
        )
        for r in rows
    ]
    return DbMaterializedViewsResponse(items=items, total=len(items))


# ---------------------------------------------------------------------------
# POST /db/refresh-mv/{name}?confirm=true
# ---------------------------------------------------------------------------

@router.post(
    "/refresh-mv/{name}",
    response_model=MvRefreshResponse,
    summary="Refresh a materialized view (requires ?confirm=true)",
    description=(
        "Runs REFRESH MATERIALIZED VIEW CONCURRENTLY when the MV has a unique "
        "index, falling back to a blocking refresh otherwise. Requires "
        "?confirm=true. Returns duration_ms + row count after refresh."
    ),
    responses={
        400: {"description": "?confirm=true missing or invalid MV name"},
        404: {"description": "Materialized view does not exist"},
        500: {"description": "PG raised during refresh"},
    },
)
async def db_refresh_mv(name: str, confirm: bool = Query(False)) -> MvRefreshResponse:
    require_confirm(confirm, f"refresh-mv:{name}")
    if not _is_valid_identifier(name):
        raise HTTPException(400, detail=f"Invalid MV name {name!r}")

    c = await _connect()
    try:
        exists = await c.fetchval(
            "SELECT 1 FROM pg_matviews WHERE matviewname=$1 AND schemaname='public'",
            name,
        )
        if not exists:
            raise HTTPException(404, detail=f"Materialized view {name!r} not found")

        has_unique = await c.fetchval("""
            SELECT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE schemaname='public' AND tablename=$1
                  AND indexdef ILIKE 'CREATE UNIQUE%'
            )
        """, name)
        concurrent = bool(has_unique)

        stmt = (
            f'REFRESH MATERIALIZED VIEW CONCURRENTLY "{name}"'
            if concurrent
            else f'REFRESH MATERIALIZED VIEW "{name}"'
        )
        t0 = time.perf_counter()
        try:
            await c.execute(stmt)
        except Exception as exc:
            raise HTTPException(500, detail=f"Refresh failed: {exc}")
        duration_ms = int((time.perf_counter() - t0) * 1000)

        row_count = await c.fetchval(
            f'SELECT count(*) FROM "{name}"'  # safe — name validated above
        )
    finally:
        await c.close()

    return MvRefreshResponse(
        name=name, concurrent=concurrent, duration_ms=duration_ms,
        row_count_estimate_after=int(row_count or 0),
    )


# ---------------------------------------------------------------------------
# /db/functions
# ---------------------------------------------------------------------------

@router.get(
    "/functions",
    response_model=DbFunctionsResponse,
    summary="PL/pgSQL function inventory",
    description=(
        "Lists user-defined functions in the public schema: name, language "
        "(plpgsql/sql/c), return type, argument signature, and volatility "
        "(immutable/stable/volatile)."
    ),
)
async def db_functions() -> DbFunctionsResponse:
    c = await _connect()
    try:
        rows = await c.fetch("""
            SELECT
                p.proname AS name,
                l.lanname AS language,
                pg_get_function_result(p.oid) AS return_type,
                pg_get_function_arguments(p.oid) AS argument_signature,
                CASE p.provolatile WHEN 'i' THEN 'immutable'
                                    WHEN 's' THEN 'stable'
                                    WHEN 'v' THEN 'volatile' END AS volatility
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid=p.pronamespace
            JOIN pg_language l ON l.oid=p.prolang
            WHERE n.nspname='public' AND p.prokind='f'
            ORDER BY p.proname
        """)
    finally:
        await c.close()

    items = [
        FunctionInfo(
            name=r["name"], language=r["language"],
            return_type=r["return_type"],
            argument_signature=r["argument_signature"],
            volatility=r["volatility"],
        )
        for r in rows
    ]
    return DbFunctionsResponse(items=items, total=len(items))


# ---------------------------------------------------------------------------
# /db/indexes
# ---------------------------------------------------------------------------

@router.get(
    "/indexes",
    response_model=DbIndexesResponse,
    summary="All indexes across all tables (paginated)",
    description=(
        "One row per index in the public schema, including partition-child copies. "
        "Returns table_name, full definition, is_unique, is_partial (WHERE clause "
        "present), and index size. Useful for finding bloated or duplicate indexes."
    ),
)
async def db_indexes(
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> DbIndexesResponse:
    c = await _connect()
    try:
        total = await c.fetchval(
            "SELECT count(*) FROM pg_indexes WHERE schemaname='public'"
        )
        rows = await c.fetch("""
            SELECT
                i.indexname AS name,
                i.tablename AS table_name,
                i.indexdef AS definition,
                ix.indisunique AS is_unique,
                position('WHERE' IN i.indexdef) > 0 AS is_partial,
                pg_relation_size(c.oid)::bigint AS size_bytes
            FROM pg_indexes i
            JOIN pg_class c ON c.relname=i.indexname AND c.relnamespace='public'::regnamespace
            JOIN pg_index ix ON ix.indexrelid=c.oid
            WHERE i.schemaname='public'
            ORDER BY i.tablename, i.indexname
            LIMIT $1 OFFSET $2
        """, limit, offset)
    finally:
        await c.close()

    items = [
        GlobalIndexInfo(
            name=r["name"], table_name=r["table_name"],
            definition=r["definition"], is_unique=bool(r["is_unique"]),
            is_partial=bool(r["is_partial"]), size_bytes=int(r["size_bytes"]),
        )
        for r in rows
    ]
    return DbIndexesResponse(items=items, total=int(total or 0), limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# /db/migrations
# ---------------------------------------------------------------------------

@router.get(
    "/migrations",
    response_model=DbMigrationsResponse,
    summary="Alembic revision history",
    description=(
        "Returns every revision file under src/migrations/versions/ plus the "
        "current head from the alembic_version table. The is_current flag "
        "marks the applied head."
    ),
)
async def db_migrations() -> DbMigrationsResponse:
    c = await _connect()
    try:
        head = await c.fetchval(
            "SELECT version_num FROM alembic_version LIMIT 1"
        ) if await c.fetchval(
            "SELECT to_regclass('public.alembic_version') IS NOT NULL"
        ) else None
    finally:
        await c.close()

    items: list[MigrationRevision] = []
    versions_dir = Path(__file__).resolve().parents[3] / "migrations" / "versions"
    if versions_dir.exists():
        for f in sorted(versions_dir.glob("*.py")):
            try:
                src = f.read_text(encoding="utf-8")
            except Exception:
                continue
            revision = _extract_var(src, "revision")
            down = _extract_var(src, "down_revision")
            title = _extract_docstring_title(src) or f.stem
            if not revision:
                continue
            items.append(MigrationRevision(
                revision=revision,
                down_revision=down,
                is_current=(revision == head),
                title=title,
            ))

    return DbMigrationsResponse(
        current_head=head,
        total_revisions=len(items),
        items=items,
    )


def _extract_var(src: str, name: str) -> str | None:
    """Cheap regex extractor: revision = "0010_..."  /  down_revision = "0009_..."."""
    import re
    m = re.search(
        rf'^{re.escape(name)}\s*(?::\s*[^=]+)?=\s*[\'"]([^\'"]*)[\'"]',
        src, flags=re.MULTILINE,
    )
    if not m:
        return None
    v = m.group(1)
    return v if v else None


def _extract_docstring_title(src: str) -> str | None:
    """First non-empty line of the file's module docstring."""
    import re
    m = re.match(r'\s*"""([^"]*?)"""', src, re.DOTALL)
    if not m:
        return None
    for line in m.group(1).splitlines():
        s = line.strip()
        if s:
            return s
    return None


# ---------------------------------------------------------------------------
# POST /db/query (SQL console)
# ---------------------------------------------------------------------------

@router.post(
    "/query",
    response_model=SqlQueryResponse,
    summary="Ad-hoc SELECT query (requires ?confirm=true)",
    description=(
        f"Runs a single SELECT statement with statement_timeout={SQL_STATEMENT_TIMEOUT_MS}ms "
        f"and a {SQL_ROW_LIMIT}-row cap. Rejects multiple statements, any non-SELECT "
        "first token, and any of the keywords: INSERT / UPDATE / DELETE / DROP / "
        "TRUNCATE / ALTER / CREATE / GRANT / REVOKE / COPY / VACUUM / SET / etc."
    ),
    responses={
        400: {"description": "?confirm=true missing OR SQL failed safety check"},
        408: {"description": "Statement exceeded timeout"},
        500: {"description": "PG raised during execution"},
    },
)
async def db_query(
    body: SqlQueryRequest, confirm: bool = Query(False),
) -> SqlQueryResponse:
    require_confirm(confirm, "sql-query")

    try:
        safe_sql = assert_safe_select(body.sql)
    except UnsafeSqlError as e:
        raise HTTPException(400, detail=f"SQL rejected: {e.reason}")

    c = await _connect()
    try:
        # Per-session timeout
        await c.execute(f"SET LOCAL statement_timeout = {SQL_STATEMENT_TIMEOUT_MS}")
        t0 = time.perf_counter()
        try:
            records = await c.fetch(safe_sql)
        except asyncpg.exceptions.QueryCanceledError:
            raise HTTPException(
                408, detail=f"Query exceeded {SQL_STATEMENT_TIMEOUT_MS}ms timeout",
            )
        except asyncpg.exceptions.PostgresError as exc:
            raise HTTPException(500, detail=f"PG error: {type(exc).__name__}: {exc}")
        duration_ms = int((time.perf_counter() - t0) * 1000)
    finally:
        await c.close()

    truncated = len(records) > SQL_ROW_LIMIT
    capped = records[:SQL_ROW_LIMIT]
    columns = list(capped[0].keys()) if capped else []
    rows = [[_to_jsonable(r[k]) for k in columns] for r in capped]

    return SqlQueryResponse(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
        row_limit=SQL_ROW_LIMIT,
        duration_ms=duration_ms,
    )


def _to_jsonable(v):
    """asyncpg returns Decimal/UUID/datetime which JSON can't serialize directly.
    Coerce to strings; the SQL console is for human inspection, not for ML."""
    if v is None:
        return None
    if isinstance(v, (str, int, float, bool, list)):
        return v
    if isinstance(v, dict):
        return v
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    return str(v)
