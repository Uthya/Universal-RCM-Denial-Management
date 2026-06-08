"""Pre-flight check.

Run before `alembic upgrade head` to confirm:
  * .env loads, DATABASE_URL parses
  * Network reaches the DB
  * Required extensions are installed (or note absence)
  * Tight cluster settings are visible
  * Where the alembic chain currently stands

Usage:
    python scripts/verify_db_connection.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Make `rcm` importable when run from project root
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import asyncpg  # noqa: E402

from rcm.core.config import settings  # noqa: E402


REQUIRED_EXTENSIONS = ("vector",)
OPTIONAL_EXTENSIONS = ("pg_partman", "pg_cron", "pgcrypto")
PG_SETTINGS_TO_REPORT = (
    "server_version",
    "max_connections",
    "shared_buffers",
    "work_mem",
    "maintenance_work_mem",
    "max_wal_size",
)


async def _check_extension(conn: asyncpg.Connection, name: str) -> bool:
    return bool(await conn.fetchval("SELECT 1 FROM pg_extension WHERE extname = $1", name))


async def _check_extension_available(conn: asyncpg.Connection, name: str) -> bool:
    return bool(
        await conn.fetchval("SELECT 1 FROM pg_available_extensions WHERE name = $1", name)
    )


async def verify() -> int:
    print(f"DATABASE_URL: {settings.database_url_redacted()}")
    raw_dsn = settings.sync_database_url()
    try:
        conn = await asyncpg.connect(raw_dsn)
    except Exception as exc:
        print(f"  ✗ FAILED to connect: {exc}")
        return 2

    rc = 0
    try:
        db_name = await conn.fetchval("SELECT current_database()")
        size = await conn.fetchval(
            f"SELECT pg_size_pretty(pg_database_size('{db_name}'))"
        )
        print(f"Database: {db_name}    Size: {size}")

        public_tables = await conn.fetchval(
            "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"
        )
        print(f"Public tables: {public_tables}")

        for setting in PG_SETTINGS_TO_REPORT:
            try:
                v = await conn.fetchval(f"SHOW {setting}")
                print(f"  {setting}: {v}")
            except Exception:
                pass

        print()
        for ext in REQUIRED_EXTENSIONS:
            installed = await _check_extension(conn, ext)
            available = await _check_extension_available(conn, ext)
            if installed:
                print(f"  ✓ {ext}: installed")
            elif available:
                print(f"  ! {ext}: available but NOT yet installed (CREATE EXTENSION {ext})")
                rc = 1
            else:
                print(f"  ✗ {ext}: NOT AVAILABLE — required for RAG scaffolding (mig 0007)")
                rc = 1

        for ext in OPTIONAL_EXTENSIONS:
            installed = await _check_extension(conn, ext)
            available = await _check_extension_available(conn, ext)
            tag = "✓ installed" if installed else (
                "○ available, not installed" if available else "○ not available"
            )
            print(f"  {tag}: {ext}")

        print()
        head = None
        try:
            has = await conn.fetchval(
                "SELECT to_regclass('public.alembic_version') IS NOT NULL"
            )
            if has:
                head = await conn.fetchval("SELECT version_num FROM alembic_version LIMIT 1")
        except Exception:
            pass
        print(f"alembic head: {head or '(none — fresh DB; run `alembic upgrade head`)'}")

        print()
        if rc == 0:
            print("OK — ready for migrations.")
        else:
            print("Some extensions are missing; coordinate `CREATE EXTENSION` with DBA.")
    finally:
        await conn.close()

    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(verify()))
