"""Validate config loads with the test env."""

from __future__ import annotations


def test_settings_loads():
    from rcm.core.config import settings
    assert settings.DATABASE_URL.startswith("postgresql+asyncpg://")
    assert len(settings.JWT_SECRET_KEY) >= 16
    assert 0.0 < settings.PRECISION_FLOOR < 1.0
    assert 0.0 < settings.LOW_PROB_CUTOFF < 1.0


def test_database_url_redacted_masks_password():
    from rcm.core.config import settings
    redacted = settings.database_url_redacted()
    assert "@" in redacted
    # Password should not appear after the user:
    # postgresql+asyncpg://user:***@host
    assert ":***@" in redacted


def test_sync_database_url_strips_async_driver():
    from rcm.core.config import settings
    sync = settings.sync_database_url()
    assert sync.startswith("postgresql://")
    assert "asyncpg" not in sync
