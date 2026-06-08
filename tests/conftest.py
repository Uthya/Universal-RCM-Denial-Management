"""Pytest configuration.

Most unit tests don't need a real PG instance — they exercise pure functions
(safe_*, envelope detection, feature builders). The fixtures below provide a
clean in-process SQLite for tests that DO want a DB, with the caveat that
PostgreSQL-only features (JSONB GIN indexes, partitioning, native ENUMs,
pgvector) cannot be exercised this way. Such tests should be marked
``@pytest.mark.requires_postgres`` and skipped unless DATABASE_URL points
at a real PG instance.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Force tests to use a local SQLite (skip remote DB even if .env points there)
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-16-characters-long")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURES_DIR = _REPO_ROOT / "tests" / "fixtures"


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return _FIXTURES_DIR


@pytest_asyncio.fixture
async def sqlite_session() -> AsyncIterator[AsyncSession]:
    """In-memory SQLite session — for testing pure ORM logic, not PG features."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "requires_postgres: test needs a real PostgreSQL (skipped under SQLite)",
    )
