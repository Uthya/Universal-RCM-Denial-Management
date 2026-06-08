"""Background-jobs summary endpoint — Home dashboard tile.

Source: background_jobs table. Until arq workers are wired (Phase 4+),
the table is empty and the endpoint reports all-zeroes — which is
correctly the desired empty-state behavior.

Future: this endpoint becomes the data source for the full Jobs page
(filterable list + per-row detail + retry/cancel actions).
"""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, HTTPException

from rcm.core.config import settings
from rcm.schemas.dev import JobsSummaryResponse, JobStatusCount

router = APIRouter()

_ALL_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")


async def _connect() -> asyncpg.Connection:
    try:
        return await asyncpg.connect(dsn=settings.sync_database_url(), timeout=8)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database unreachable: {type(exc).__name__}: {exc}",
        )


@router.get(
    "/summary",
    response_model=JobsSummaryResponse,
    summary="Background-job counts grouped by status",
    description=(
        "Returns counts of background_jobs rows grouped by status (all-time), "
        "plus break-outs for currently-queued, currently-running, "
        "succeeded-in-last-1h, and failed-in-last-1h. When the table is empty "
        "(arq not yet wired), every count is zero."
    ),
)
async def jobs_summary() -> JobsSummaryResponse:
    c = await _connect()
    try:
        rows = await c.fetch("""
            SELECT status::text AS status, count(*)::bigint AS n
            FROM background_jobs
            GROUP BY status
        """)
        succeeded_last_hour = await c.fetchval("""
            SELECT count(*) FROM background_jobs
            WHERE status='succeeded' AND completed_at >= now() - interval '1 hour'
        """)
        failed_last_hour = await c.fetchval("""
            SELECT count(*) FROM background_jobs
            WHERE status='failed' AND completed_at >= now() - interval '1 hour'
        """)
    finally:
        await c.close()

    by_status_dict = {r["status"]: int(r["n"]) for r in rows}
    # Always return all 5 statuses, even when count is 0
    by_status = [
        JobStatusCount(status=s, count=by_status_dict.get(s, 0))
        for s in _ALL_STATUSES
    ]
    return JobsSummaryResponse(
        by_status=by_status,
        queued=by_status_dict.get("queued", 0),
        running=by_status_dict.get("running", 0),
        succeeded_last_hour=int(succeeded_last_hour or 0),
        failed_last_hour=int(failed_last_hour or 0),
    )
