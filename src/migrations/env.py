"""Alembic environment.

Single source of truth for DATABASE_URL is ``rcm.core.config.settings``.
The static ``sqlalchemy.url`` in alembic.ini is intentionally a placeholder
so a stale value cannot silently target the wrong database.

configparser interpolates ``%`` characters — URL-encoded passwords contain
``%21``, ``%23`` etc. — so we double them via ``str.replace('%', '%%')``
before set_main_option.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from pathlib import Path
import sys

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Make `rcm` importable when alembic is invoked from project root
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from rcm.core.config import settings  # noqa: E402
from rcm.core.database import Base  # noqa: E402

# Register every mapped class with Base.metadata
import rcm.models  # noqa: F401,E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Critical: escape % for configparser; override the static placeholder.
config.set_main_option(
    "sqlalchemy.url",
    settings.DATABASE_URL.replace("%", "%%"),
)

target_metadata = Base.metadata


def _include_object(obj, name, type_, reflected, compare_to):
    """Skip alembic's introspection of partition children created at runtime."""
    if type_ == "table" and name.startswith(("raw_segments_p", "parse_events_p",
                                              "prediction_log_p", "audit_log_p",
                                              "request_log_p")):
        return False
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=_include_object,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=_include_object,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
