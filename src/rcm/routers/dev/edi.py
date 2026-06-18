"""EDI Inspector endpoints — Page 2 of the dev console.

Per CR-041 contract:
    * Backend is source of truth — counts (segments, events, claims) are
      computed live from the partitioned tables, NOT from parse_summary (which
      is a snapshot at parse time and may be stale after re-parse).
    * Read-only by default; the single write surface (upload) requires
      `?confirm=true` per CR-038.
    * Empty-state safe — every list endpoint returns a typed empty Page
      when no rows match.

Endpoints:
    POST /api/dev/edi/upload?confirm=true       multipart upload → parse_and_save
    GET  /api/dev/edi/files                     filterable list (status, variant, q)
    GET  /api/dev/edi/files/{id}                single-file metadata + live counters
    GET  /api/dev/edi/files/{id}/segments       raw_segments page (handler_status filter)
    GET  /api/dev/edi/files/{id}/events         parse_events page (event_type filter)
"""

from __future__ import annotations

import time

import asyncpg
from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from rcm.core.config import settings
from rcm.core.database import async_session
from rcm.parsing.envelope import EnvelopeError
from rcm.parsing.parser import parse_and_save
from rcm.parsing.persistence import DuplicateFileError
from rcm.routers.dev import require_confirm
from rcm.schemas.dev import (
    EdiFileDetailResponse,
    EdiFileListItem,
    EdiFilesListResponse,
    EdiUploadResponse,
    ParseEventItem,
    ParseEventsListResponse,
    RawSegmentItem,
    RawSegmentsListResponse,
)

router = APIRouter()


async def _connect() -> asyncpg.Connection:
    try:
        return await asyncpg.connect(dsn=settings.sync_database_url(), timeout=8)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database unreachable: {type(exc).__name__}: {exc}",
        )


# ---------------------------------------------------------------------------
# POST /upload
# ---------------------------------------------------------------------------

@router.post(
    "/upload",
    response_model=EdiUploadResponse,
    summary="Upload + parse + persist a single EDI file",
    description=(
        "Multipart upload of one EDI file. Runs `parse_and_save` end-to-end "
        "(decode → tokenize → dispatch → 4-tier validate → persist). Requires "
        "`?confirm=true` per CR-038 because this is a write operation. Returns "
        "the resulting `edi_file_id` plus parse summary. On duplicate "
        "content_hash returns 409 with `duplicate_of_file_id`. On envelope "
        "failure returns 400 with `error` set."
    ),
    responses={
        400: {"description": "Confirmation missing OR envelope/decode failure"},
        409: {"description": "Duplicate content_hash (file already uploaded)"},
        413: {"description": "File exceeds MAX_UPLOAD_SIZE_MB"},
    },
)
async def upload_edi(
    file: UploadFile = File(..., description="One EDI file (837P/I/D or 835)"),
    confirm: bool = Query(False, description="Required write-confirmation per CR-038"),
) -> EdiUploadResponse:
    require_confirm(confirm, f"upload-edi:{file.filename}")

    raw = await file.read()
    if len(raw) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=(
                f"File size {len(raw)} bytes exceeds "
                f"MAX_UPLOAD_SIZE_MB={settings.MAX_UPLOAD_SIZE_MB}MB"
            ),
        )

    started = time.perf_counter()
    try:
        async with async_session() as session:
            edi_file = await parse_and_save(
                session, raw, file_name=file.filename or "uploaded.edi",
            )
            await session.commit()
        # CR-090: same debounced auto-refresh as the public /upload path.
        from rcm.core.mv_refresh import schedule_mv_refresh
        schedule_mv_refresh()
    except EnvelopeError as exc:
        return EdiUploadResponse(
            edi_file_id=None,
            file_name=file.filename or "uploaded.edi",
            file_type=None,
            service_variant_detected=None,
            claim_subtype_detected=None,
            parse_status=None,
            claims_saved=None,
            claims_dropped=None,
            parser_version=settings.PARSER_VERSION,
            parse_duration_ms=int((time.perf_counter() - started) * 1000),
            error=f"EnvelopeError: {exc}",
        )
    except DuplicateFileError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Duplicate upload: content_hash already exists "
                   f"(existing edi_file_id={exc.existing_id})",
            headers={"X-Duplicate-Of": str(exc.existing_id)},
        )

    duration_ms = int((time.perf_counter() - started) * 1000)
    summary = edi_file.parse_summary or {}
    return EdiUploadResponse(
        edi_file_id=edi_file.id,
        file_name=edi_file.file_name,
        file_type=edi_file.file_type.value if edi_file.file_type else None,
        service_variant_detected=edi_file.service_variant_detected,
        claim_subtype_detected=edi_file.claim_subtype_detected,
        parse_status=edi_file.parse_status.value if edi_file.parse_status else None,
        claims_saved=summary.get("claims_saved"),
        claims_dropped=summary.get("claims_dropped"),
        parser_version=edi_file.parser_version,
        parse_duration_ms=duration_ms,
        error=None,
    )


# ---------------------------------------------------------------------------
# GET /files
# ---------------------------------------------------------------------------

@router.get(
    "/files",
    response_model=EdiFilesListResponse,
    summary="Paginated list of edi_files with optional filters",
    description=(
        "Returns edi_files (newest first) optionally filtered by parse_status, "
        "service_variant_detected, or a substring match on file_name. Includes "
        "claims_saved / claims_dropped extracted from parse_summary."
    ),
)
async def edi_files_list(
    status: str | None = Query(None, description="parse_status filter"),
    variant: str | None = Query(None, description="service_variant_detected filter"),
    q: str | None = Query(None, description="Substring match on file_name (case-insensitive)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> EdiFilesListResponse:
    where = ["deleted_at IS NULL"]
    params: list = []

    if status:
        params.append(status)
        where.append(f"parse_status::text = ${len(params)}")
    if variant:
        params.append(variant)
        where.append(f"service_variant_detected = ${len(params)}")
    if q:
        params.append(f"%{q}%")
        where.append(f"file_name ILIKE ${len(params)}")

    where_sql = " AND ".join(where)
    c = await _connect()
    try:
        total = await c.fetchval(f"SELECT count(*) FROM edi_files WHERE {where_sql}", *params)
        params_with_paging = [*params, limit, offset]
        rows = await c.fetch(
            f"""
            SELECT
                id, file_name, file_type::text AS file_type,
                service_variant_detected, claim_subtype_detected,
                parse_status::text AS parse_status, parser_version,
                (parse_summary->>'claims_saved')::int  AS claims_saved,
                (parse_summary->>'claims_dropped')::int AS claims_dropped,
                octet_length(raw_text) AS raw_size_bytes,
                to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS') AS uploaded_at,
                to_char(parse_completed_at, 'YYYY-MM-DD"T"HH24:MI:SS') AS parse_completed_at
            FROM edi_files
            WHERE {where_sql}
            ORDER BY id DESC
            LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
            """,
            *params_with_paging,
        )
    finally:
        await c.close()

    items = [
        EdiFileListItem(
            id=int(r["id"]),
            file_name=r["file_name"],
            file_type=r["file_type"],
            service_variant_detected=r["service_variant_detected"],
            claim_subtype_detected=r["claim_subtype_detected"],
            parse_status=r["parse_status"],
            parser_version=r["parser_version"],
            claims_saved=r["claims_saved"],
            claims_dropped=r["claims_dropped"],
            raw_size_bytes=int(r["raw_size_bytes"] or 0),
            uploaded_at=r["uploaded_at"],
            parse_completed_at=r["parse_completed_at"],
        )
        for r in rows
    ]
    return EdiFilesListResponse(items=items, total=int(total or 0), limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# GET /files/{id}
# ---------------------------------------------------------------------------

@router.get(
    "/files/{file_id}",
    response_model=EdiFileDetailResponse,
    summary="Single edi_file detail with live counters",
    description=(
        "Returns full metadata plus live counts of raw_segments, parse_events, "
        "claims, and remittance_claims associated with this file. Counts are "
        "computed in SQL — not derived from parse_summary which can be stale."
    ),
    responses={404: {"description": "edi_file not found or soft-deleted"}},
)
async def edi_file_detail(file_id: int) -> EdiFileDetailResponse:
    c = await _connect()
    try:
        row = await c.fetchrow(
            """
            SELECT
                id, file_name, file_type::text AS file_type,
                implementation_guide, sender_id, receiver_id,
                interchange_control_no, functional_group_control_no,
                service_variant_detected, claim_subtype_detected,
                parse_status::text AS parse_status, parser_version,
                content_hash,
                octet_length(raw_text) AS raw_size_bytes,
                to_char(parse_started_at,   'YYYY-MM-DD"T"HH24:MI:SS') AS parse_started_at,
                to_char(parse_completed_at, 'YYYY-MM-DD"T"HH24:MI:SS') AS parse_completed_at,
                parse_summary,
                to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS') AS uploaded_at
            FROM edi_files
            WHERE id = $1 AND deleted_at IS NULL
            """,
            file_id,
        )
        if row is None:
            raise HTTPException(404, detail=f"edi_file id={file_id} not found")

        # Live counts — separate queries since the partitioned tables don't
        # benefit from a single join here. asyncpg keeps these cheap.
        raw_segments_count = await c.fetchval(
            "SELECT count(*) FROM raw_segments WHERE edi_file_id = $1", file_id,
        )
        parse_events_count = await c.fetchval(
            "SELECT count(*) FROM parse_events WHERE edi_file_id = $1", file_id,
        )
        claims_count = await c.fetchval(
            "SELECT count(*) FROM claims WHERE edi_file_id = $1 AND deleted_at IS NULL",
            file_id,
        )
        remits_count = await c.fetchval(
            "SELECT count(*) FROM remittance_claims WHERE edi_file_id = $1", file_id,
        )
    finally:
        await c.close()

    import json
    summary = row["parse_summary"]
    if isinstance(summary, str):
        summary = json.loads(summary)

    return EdiFileDetailResponse(
        id=int(row["id"]),
        file_name=row["file_name"],
        file_type=row["file_type"],
        implementation_guide=row["implementation_guide"],
        sender_id=row["sender_id"],
        receiver_id=row["receiver_id"],
        interchange_control_no=row["interchange_control_no"],
        functional_group_control_no=row["functional_group_control_no"],
        service_variant_detected=row["service_variant_detected"],
        claim_subtype_detected=row["claim_subtype_detected"],
        parse_status=row["parse_status"],
        parser_version=row["parser_version"],
        content_hash=row["content_hash"],
        raw_size_bytes=int(row["raw_size_bytes"] or 0),
        parse_started_at=row["parse_started_at"],
        parse_completed_at=row["parse_completed_at"],
        parse_summary=summary,
        uploaded_at=row["uploaded_at"],
        raw_segments_count=int(raw_segments_count or 0),
        parse_events_count=int(parse_events_count or 0),
        claims_persisted_count=int(claims_count or 0),
        remittance_claims_persisted_count=int(remits_count or 0),
    )


# ---------------------------------------------------------------------------
# GET /files/{id}/segments
# ---------------------------------------------------------------------------

@router.get(
    "/files/{file_id}/segments",
    response_model=RawSegmentsListResponse,
    summary="Raw segments for one file (parse-trace tab)",
    description=(
        "Returns raw_segments for this file paginated by segment_position. "
        "Filter by handler_status (handled / skipped_unhandled / parse_error) "
        "to home in on what the parser couldn't handle."
    ),
)
async def edi_file_segments(
    file_id: int,
    handler_status: str | None = Query(None, description="Filter on handler_status enum"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> RawSegmentsListResponse:
    where = ["edi_file_id = $1"]
    params: list = [file_id]
    if handler_status:
        params.append(handler_status)
        where.append(f"handler_status::text = ${len(params)}")
    where_sql = " AND ".join(where)

    c = await _connect()
    try:
        total = await c.fetchval(
            f"SELECT count(*) FROM raw_segments WHERE {where_sql}", *params,
        )
        params_with_paging = [*params, limit, offset]
        rows = await c.fetch(
            f"""
            SELECT
                id, segment_position, segment_name,
                handler_status::text AS handler_status,
                raw_segment_text, parse_error,
                claim_id, remittance_claim_id,
                to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS') AS created_at
            FROM raw_segments
            WHERE {where_sql}
            ORDER BY segment_position
            LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
            """,
            *params_with_paging,
        )
    finally:
        await c.close()

    items = [
        RawSegmentItem(
            id=int(r["id"]),
            segment_position=int(r["segment_position"]),
            segment_name=r["segment_name"],
            handler_status=r["handler_status"],
            raw_segment_text=r["raw_segment_text"],
            parse_error=r["parse_error"],
            claim_id=r["claim_id"],
            remittance_claim_id=r["remittance_claim_id"],
            created_at=r["created_at"],
        )
        for r in rows
    ]
    return RawSegmentsListResponse(items=items, total=int(total or 0), limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# GET /files/{id}/events
# ---------------------------------------------------------------------------

@router.get(
    "/files/{file_id}/events",
    response_model=ParseEventsListResponse,
    summary="Parse events for one file",
    description=(
        "Returns parse_events for this file in chronological order. Filter by "
        "event_type (segment_handled / segment_skipped / validator_error / "
        "validator_warning / claim_dropped / parse_error) to focus the trace."
    ),
)
async def edi_file_events(
    file_id: int,
    event_type: str | None = Query(None, description="parse_event_type filter"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> ParseEventsListResponse:
    where = ["edi_file_id = $1"]
    params: list = [file_id]
    if event_type:
        params.append(event_type)
        where.append(f"event_type::text = ${len(params)}")
    where_sql = " AND ".join(where)

    c = await _connect()
    try:
        total = await c.fetchval(
            f"SELECT count(*) FROM parse_events WHERE {where_sql}", *params,
        )
        params_with_paging = [*params, limit, offset]
        rows = await c.fetch(
            f"""
            SELECT
                id, event_type::text AS event_type, segment_name, segment_position,
                claim_number, details,
                to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS') AS created_at
            FROM parse_events
            WHERE {where_sql}
            ORDER BY id
            LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
            """,
            *params_with_paging,
        )
    finally:
        await c.close()

    import json
    items = []
    for r in rows:
        details = r["details"]
        if isinstance(details, str):
            details = json.loads(details)
        items.append(ParseEventItem(
            id=int(r["id"]),
            event_type=r["event_type"],
            segment_name=r["segment_name"],
            segment_position=r["segment_position"],
            claim_number=r["claim_number"],
            details=details,
            created_at=r["created_at"],
        ))
    return ParseEventsListResponse(items=items, total=int(total or 0), limit=limit, offset=offset)
