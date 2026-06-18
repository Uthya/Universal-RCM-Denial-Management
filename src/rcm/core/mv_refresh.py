"""CR-090 — debounced background refresh of ``mv_claim_labels``.

Triggered by upload endpoints (public + dev) so the trainer's source-of-truth
MV stays close to current without per-upload write amplification (CR-050
incident pattern, also rejected by CR-083). The CR-083 refresh-before-/train
safety net is preserved — this module is additive.

Design (coalesce):
  - Every upload calls ``schedule_mv_refresh()``.
  - The first call lazily spawns a background worker task.
  - The worker waits ``DEBOUNCE_SECONDS`` of quiescence. Any new upload
    during that wait resets the timer.
  - After the quiet window, ONE REFRESH MATERIALIZED VIEW CONCURRENTLY runs.
  - For a 200-file bulk upload (≈28 s), this means ONE refresh ≈5 s after the
    last file, not 200 sequential refreshes.

Failure modes:
  - DB unreachable / refresh raises: logged at WARNING, no retry. Operator
    can manually call ``/api/dev/db/refresh-mv/mv_claim_labels?confirm=true``
    or invoke ``/api/predictions/train`` (which CR-083 makes refresh as a
    pre-step). Never raises out to the upload caller.
  - Backend killed during the debounce window: refresh is skipped; CR-083
    train-time refresh covers it on the next training run.
  - Multiple concurrent uploads: ``_pending`` is a sticky flag — every upload
    sets it; the worker clears + re-checks atomically inside the asyncio loop
    (single-threaded), so coalescing is race-free.
"""

from __future__ import annotations

import asyncio
import logging

import asyncpg

from rcm.core.config import settings

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 5
TARGET_MV = "mv_claim_labels"

# Module-level coalescing state. Safe because the FastAPI worker is a single
# event loop; asyncio is cooperatively scheduled so no two coroutines can
# interleave between `pending.set()` and `loop.create_task(...)` below.
_pending: asyncio.Event = asyncio.Event()
_worker_task: asyncio.Task | None = None


def schedule_mv_refresh() -> None:
    """Mark ``mv_claim_labels`` as needing refresh and ensure a background
    worker is running. Safe to call from any async context. Cheap, non-blocking.
    """
    global _worker_task
    _pending.set()
    if _worker_task is None or _worker_task.done():
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — caller is sync-only context (e.g. test). Skip;
            # CR-083 train-time refresh remains the backstop.
            return
        _worker_task = loop.create_task(_worker(), name="cr090-mv-refresh")


async def _worker() -> None:
    """Wait for ``DEBOUNCE_SECONDS`` of quiescence, then refresh once."""
    try:
        await _pending.wait()
        # Drain the debounce window — any new schedule_mv_refresh during the
        # sleep re-sets _pending and we loop.
        while True:
            _pending.clear()
            await asyncio.sleep(DEBOUNCE_SECONDS)
            if not _pending.is_set():
                break
        # Quiescent — run the refresh.
        await _do_refresh()
    except asyncio.CancelledError:
        # Shutdown path; CR-083 will catch any missed work on next /train.
        raise
    except Exception as exc:  # pragma: no cover — guard against unexpected
        logger.warning(
            "CR-090 mv_refresh worker exited unexpectedly: %s: %s",
            type(exc).__name__, exc,
        )


async def _do_refresh() -> None:
    """Run REFRESH MATERIALIZED VIEW CONCURRENTLY mv_claim_labels.

    Uses a fresh asyncpg connection (not the SQLAlchemy session pool) so the
    REFRESH runs in its own autocommit transaction with no side-effects on
    any ongoing upload's session.
    """
    try:
        conn = await asyncpg.connect(dsn=settings.sync_database_url(), timeout=10)
    except Exception as exc:
        logger.warning(
            "CR-090 mv_refresh: cannot reach DB; refresh skipped. %s: %s",
            type(exc).__name__, exc,
        )
        return
    try:
        try:
            await conn.execute(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {TARGET_MV}")
            row_count = await conn.fetchval(f"SELECT count(*) FROM {TARGET_MV}")
            logger.info(
                "CR-090 mv_refresh: %s refreshed (%d rows)", TARGET_MV, row_count,
            )
        except Exception as exc:
            logger.warning(
                "CR-090 mv_refresh: REFRESH failed on %s; %s: %s",
                TARGET_MV, type(exc).__name__, exc,
            )
    finally:
        await conn.close()
