"""GET /api/dev/env/info — operational sanity-check (Page 12).

Returns:
    {
      "database": {url_redacted, version, current_database, size, alembic_head},
      "extensions": {required: [{name, installed_version, available}], optional: [...]},
      "pg_settings": {server_version, shared_buffers, ...},
      "feature_flags": {RAG_ENABLED, DEBUG, ...},
      "versions": {python, fastapi, sqlalchemy, asyncpg, alembic, pgvector},
      "feature_engineering_version": "v1.0.0",
      "parser_version": "v2.0.0",
    }

Source of truth: hits PG directly with asyncpg + reads from settings.
Mirrors what scripts/verify_db_connection.py reports, just as JSON.
"""

from __future__ import annotations

import sys
from typing import Any

import asyncpg
import fastapi
import sqlalchemy
from fastapi import APIRouter, HTTPException

from rcm.core.config import settings
from rcm.features.constants import FEATURE_ENGINEERING_VERSION

router = APIRouter()


_REQUIRED_EXTENSIONS = ("vector",)
_OPTIONAL_EXTENSIONS = ("pg_partman", "pg_cron", "pgcrypto")
_PG_SETTINGS_TO_REPORT = (
    "server_version",
    "max_connections",
    "shared_buffers",
    "work_mem",
    "maintenance_work_mem",
    "max_wal_size",
    "statement_timeout",
)


async def _check_extension(conn: asyncpg.Connection, name: str) -> dict[str, Any]:
    installed = await conn.fetchval(
        "SELECT extversion FROM pg_extension WHERE extname = $1", name,
    )
    available = await conn.fetchval(
        "SELECT default_version FROM pg_available_extensions WHERE name = $1", name,
    )
    return {
        "name": name,
        "installed_version": installed,
        "available_version": available,
        "status": ("installed" if installed
                   else "available" if available
                   else "not_available"),
    }


async def _alembic_head(conn: asyncpg.Connection) -> str | None:
    has = await conn.fetchval(
        "SELECT to_regclass('public.alembic_version') IS NOT NULL"
    )
    if not has:
        return None
    return await conn.fetchval("SELECT version_num FROM alembic_version LIMIT 1")


@router.get("/info")
async def env_info() -> dict[str, Any]:
    """Full environment + system snapshot. Single endpoint = single panel.

    The DSN is masked. PG-side data is fetched via a fresh asyncpg connection
    rather than the SQLAlchemy pool so a hung session doesn't break the panel.
    """
    raw_dsn = settings.sync_database_url()
    try:
        conn = await asyncpg.connect(dsn=raw_dsn, timeout=8)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Cannot connect to PG: {type(exc).__name__}: {exc}",
        )

    try:
        version = await conn.fetchval("SELECT version()")
        db_name = await conn.fetchval("SELECT current_database()")
        size_bytes = await conn.fetchval(
            "SELECT pg_database_size(current_database())"
        )
        size_pretty = await conn.fetchval(
            "SELECT pg_size_pretty(pg_database_size(current_database()))"
        )

        pg_settings: dict[str, str] = {}
        for s in _PG_SETTINGS_TO_REPORT:
            try:
                pg_settings[s] = await conn.fetchval(f"SHOW {s}")
            except Exception:
                pg_settings[s] = "n/a"

        required_ext = [await _check_extension(conn, n) for n in _REQUIRED_EXTENSIONS]
        optional_ext = [await _check_extension(conn, n) for n in _OPTIONAL_EXTENSIONS]
        alembic_head = await _alembic_head(conn)
    finally:
        await conn.close()

    try:
        import pgvector
        pgvector_version = pgvector.__version__
    except Exception:
        pgvector_version = None

    return {
        "database": {
            "url_redacted": settings.database_url_redacted(),
            "version": version,
            "current_database": db_name,
            "size_bytes": int(size_bytes) if size_bytes else None,
            "size_pretty": size_pretty,
            "alembic_head": alembic_head,
        },
        "extensions": {
            "required": required_ext,
            "optional": optional_ext,
        },
        "pg_settings": pg_settings,
        "feature_flags": {
            "DEBUG": settings.DEBUG,
            "RAG_ENABLED": settings.RAG_ENABLED,
            "LLM_PROVIDER": settings.LLM_PROVIDER or None,
        },
        "versions": {
            "python": sys.version.split()[0],
            "fastapi": fastapi.__version__,
            "sqlalchemy": sqlalchemy.__version__,
            "asyncpg": asyncpg.__version__,
            "pgvector": pgvector_version,
        },
        "feature_engineering_version": FEATURE_ENGINEERING_VERSION,
        "parser_version": settings.PARSER_VERSION,
        "min_training_size": settings.MIN_TRAINING_SIZE,
        "precision_floor": settings.PRECISION_FLOOR,
        "low_prob_cutoff": settings.LOW_PROB_CUTOFF,
    }


@router.get("/health")
async def health() -> dict[str, Any]:
    """Minimal liveness probe — used by TopBar's live indicator (5s polling)."""
    raw_dsn = settings.sync_database_url()
    try:
        conn = await asyncpg.connect(dsn=raw_dsn, timeout=3)
        try:
            await conn.fetchval("SELECT 1")
        finally:
            await conn.close()
        db_ok = True
    except Exception:
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "database": "up" if db_ok else "down",
    }
