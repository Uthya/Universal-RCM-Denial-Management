"""Uploads endpoints — recent ingest activity for the Home dashboard.

Source: edi_files table. Read-only. Empty-state safe.
"""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, HTTPException, Query

from rcm.core.config import settings
from rcm.schemas.dev import RecentUploadPoint, RecentUploadsResponse

router = APIRouter()


async def _connect() -> asyncpg.Connection:
    try:
        return await asyncpg.connect(dsn=settings.sync_database_url(), timeout=8)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database unreachable: {type(exc).__name__}: {exc}",
        )


@router.get(
    "/recent",
    response_model=RecentUploadsResponse,
    summary="Last N edi_files rows (most recent first)",
    description=(
        "Returns the most recent EDI uploads with parse_status + the "
        "claims_saved/claims_dropped fields extracted from parse_summary. "
        "Used by the Home dashboard's Recent Uploads tile."
    ),
)
async def uploads_recent(
    limit: int = Query(10, ge=1, le=100),
) -> RecentUploadsResponse:
    c = await _connect()
    try:
        rows = await c.fetch("""
            SELECT
                id,
                file_name,
                file_type::text AS file_type,
                service_variant_detected,
                claim_subtype_detected,
                parse_status::text AS parse_status,
                (parse_summary->>'claims_saved')::int  AS claims_saved,
                (parse_summary->>'claims_dropped')::int AS claims_dropped,
                to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS') AS uploaded_at
            FROM edi_files
            WHERE deleted_at IS NULL
            ORDER BY id DESC
            LIMIT $1
        """, limit)
    finally:
        await c.close()

    items = [
        RecentUploadPoint(
            id=int(r["id"]),
            file_name=r["file_name"],
            file_type=r["file_type"],
            service_variant_detected=r["service_variant_detected"],
            claim_subtype_detected=r["claim_subtype_detected"],
            parse_status=r["parse_status"],
            claims_saved=r["claims_saved"],
            claims_dropped=r["claims_dropped"],
            uploaded_at=r["uploaded_at"],
        )
        for r in rows
    ]
    return RecentUploadsResponse(items=items, total=len(items))
