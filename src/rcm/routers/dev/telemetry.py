"""Parsing Telemetry endpoints — Page 4 of the dev console.

Per CR-041:
    * Backend is source of truth — every count/rate computed in SQL against
      live tables (edi_files / parse_events / claims). Frontend never aggregates.
    * Pydantic response schemas.
    * Read-only — no write endpoints on this page.
    * Empty-state safe — when zero rows exist in the window, returns
      `{items: []}` not 500.

Endpoints:
    GET /telemetry/drops                  daily drop count, optionally by variant
    GET /telemetry/drop-reasons           top (segment, field) ERROR-severity reasons
    GET /telemetry/unhandled-segments     top skipped_unhandled segments + sample text
    GET /telemetry/cas-stride-distribution  stride-2 vs stride-3 CAS forms seen
    GET /telemetry/encoding-distribution    decode path distribution (telemetry-gated)
    GET /telemetry/validator-tiers        ERROR/WARNING counts by tier × variant
    GET /telemetry/reparse-log            recent re-parse activity
"""

from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import APIRouter, HTTPException, Query

from rcm.core.config import settings
from rcm.schemas.dev import (
    CasStridePoint,
    CasStrideResponse,
    DropPoint,
    DropRateResponse,
    DropReasonPoint,
    DropReasonsResponse,
    EncodingDistributionPoint,
    EncodingDistributionResponse,
    ReparseLogResponse,
    ReparsePoint,
    UnhandledSegmentPoint,
    UnhandledSegmentsResponse,
    ValidatorTierPoint,
    ValidatorTiersResponse,
)

router = APIRouter()


async def _connect() -> asyncpg.Connection:
    raw_dsn = settings.sync_database_url()
    try:
        return await asyncpg.connect(dsn=raw_dsn, timeout=8)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database unreachable: {type(exc).__name__}: {exc}",
        )


# ---------------------------------------------------------------------------
# /telemetry/drops
# ---------------------------------------------------------------------------

@router.get(
    "/drops",
    response_model=DropRateResponse,
    summary="Daily drop counts (claim_dropped events) grouped by variant",
    description=(
        "Returns a time-series of `parse_events.event_type='claim_dropped'` "
        "counts bucketed by day (or hour), grouped by claim's "
        "`service_variant_detected` from the parent edi_file. Empty when no "
        "drops occurred in the window."
    ),
)
async def telemetry_drops(
    days: int = Query(30, ge=1, le=365, description="Look-back window in days"),
    group_by: str = Query("day", description="day | hour"),
) -> DropRateResponse:
    if group_by not in ("day", "hour"):
        raise HTTPException(400, detail=f"group_by must be 'day' or 'hour'; got {group_by!r}")

    trunc = "day" if group_by == "day" else "hour"
    sql = f"""
        SELECT
            to_char(date_trunc('{trunc}', pe.created_at), 'YYYY-MM-DD"T"HH24:MI:SS') AS bucket,
            ef.service_variant_detected AS service_variant,
            count(*)::bigint AS dropped_count
        FROM parse_events pe
        LEFT JOIN edi_files ef ON ef.id = pe.edi_file_id
        WHERE pe.event_type = 'claim_dropped'
          AND pe.created_at >= now() - ($1 || ' days')::interval
        GROUP BY 1, 2
        ORDER BY 1, 2
    """
    c = await _connect()
    try:
        rows = await c.fetch(sql, str(days))
    finally:
        await c.close()

    items = [
        DropPoint(
            bucket=r["bucket"],
            service_variant=r["service_variant"],
            dropped_count=int(r["dropped_count"]),
        )
        for r in rows
    ]
    return DropRateResponse(days=days, group_by=group_by, items=items)


# ---------------------------------------------------------------------------
# /telemetry/drop-reasons
# ---------------------------------------------------------------------------

@router.get(
    "/drop-reasons",
    response_model=DropReasonsResponse,
    summary="Top drop reasons by (segment, field)",
    description=(
        "Aggregates ERROR-severity validator entries from "
        "`parse_events.event_type IN ('validator_error', 'claim_dropped')` "
        "with details.segment + details.field. Returns the top N (segment, "
        "field) pairs by count over the window."
    ),
)
async def telemetry_drop_reasons(
    days: int = Query(7, ge=1, le=365),
    top: int = Query(20, ge=1, le=100),
) -> DropReasonsResponse:
    sql = """
        SELECT
            COALESCE(details->>'segment', segment_name, '?') AS segment,
            COALESCE(details->>'field', '?') AS field,
            count(*)::bigint AS count
        FROM parse_events
        WHERE event_type IN ('validator_error', 'claim_dropped')
          AND created_at >= now() - ($1 || ' days')::interval
        GROUP BY 1, 2
        ORDER BY count DESC
        LIMIT $2
    """
    c = await _connect()
    try:
        rows = await c.fetch(sql, str(days), top)
        total = await c.fetchval("""
            SELECT count(*) FROM parse_events
            WHERE event_type IN ('validator_error', 'claim_dropped')
              AND created_at >= now() - ($1 || ' days')::interval
        """, str(days))
    finally:
        await c.close()

    items = [
        DropReasonPoint(segment=r["segment"], field=r["field"], count=int(r["count"]))
        for r in rows
    ]
    return DropReasonsResponse(days=days, total_errors=int(total or 0), items=items)


# ---------------------------------------------------------------------------
# /telemetry/unhandled-segments
# ---------------------------------------------------------------------------

@router.get(
    "/unhandled-segments",
    response_model=UnhandledSegmentsResponse,
    summary="Top unhandled (skipped) segment types with sample text",
    description=(
        "Joins parse_events.event_type='segment_skipped' with raw_segments to "
        "surface the most-frequent segment types the parser DOESN'T have a "
        "handler for. For each segment, includes a sample raw text from one "
        "actual occurrence — useful for deciding which handler to build next."
    ),
)
async def telemetry_unhandled_segments(
    days: int = Query(7, ge=1, le=365),
    top: int = Query(20, ge=1, le=100),
) -> UnhandledSegmentsResponse:
    sql = """
        WITH counts AS (
            SELECT
                segment_name,
                count(*)::bigint AS n
            FROM parse_events
            WHERE event_type = 'segment_skipped'
              AND created_at >= now() - ($1 || ' days')::interval
              AND segment_name IS NOT NULL
            GROUP BY segment_name
            ORDER BY n DESC
            LIMIT $2
        ),
        samples AS (
            SELECT DISTINCT ON (rs.segment_name)
                rs.segment_name,
                rs.created_at,
                rs.edi_file_id,
                rs.raw_segment_text
            FROM raw_segments rs
            JOIN counts c ON c.segment_name = rs.segment_name
            WHERE rs.handler_status = 'skipped_unhandled'
              AND rs.created_at >= now() - ($1 || ' days')::interval
            ORDER BY rs.segment_name, rs.created_at DESC
        )
        SELECT
            counts.segment_name,
            counts.n AS count,
            to_char(samples.created_at, 'YYYY-MM-DD"T"HH24:MI:SS') AS last_seen_at,
            samples.edi_file_id AS last_seen_in_file_id,
            samples.raw_segment_text AS last_seen_text
        FROM counts
        LEFT JOIN samples ON samples.segment_name = counts.segment_name
        ORDER BY counts.n DESC
    """
    c = await _connect()
    try:
        rows = await c.fetch(sql, str(days), top)
    finally:
        await c.close()

    items = [
        UnhandledSegmentPoint(
            segment_name=r["segment_name"],
            count=int(r["count"]),
            last_seen_at=r["last_seen_at"],
            last_seen_in_file_id=r["last_seen_in_file_id"],
            last_seen_text=r["last_seen_text"],
        )
        for r in rows
    ]
    return UnhandledSegmentsResponse(days=days, items=items)


# ---------------------------------------------------------------------------
# /telemetry/cas-stride-distribution
# ---------------------------------------------------------------------------

@router.get(
    "/cas-stride-distribution",
    response_model=CasStrideResponse,
    summary="CAS triplet stride-2 (compact) vs stride-3 (spec) distribution",
    description=(
        "Counts `validator_warning` parse_events emitted by the CAS handler "
        "where `details->>'stride'` is set. Stride-2 = compact non-spec form; "
        "stride-3 = spec form. Heavily-stride-2 payers indicate a clearinghouse "
        "that emits the compact form."
    ),
)
async def telemetry_cas_stride(
    days: int = Query(7, ge=1, le=365),
) -> CasStrideResponse:
    sql = """
        SELECT
            (details->>'stride')::int AS stride,
            count(*)::bigint AS count
        FROM parse_events
        WHERE event_type = 'validator_warning'
          AND segment_name = 'CAS'
          AND details ? 'stride'
          AND created_at >= now() - ($1 || ' days')::interval
        GROUP BY 1
        ORDER BY 1
    """
    c = await _connect()
    try:
        rows = await c.fetch(sql, str(days))
        total = await c.fetchval("""
            SELECT count(*) FROM parse_events
            WHERE event_type = 'validator_warning'
              AND segment_name = 'CAS'
              AND created_at >= now() - ($1 || ' days')::interval
        """, str(days))
    finally:
        await c.close()

    items = [CasStridePoint(stride=int(r["stride"]), count=int(r["count"])) for r in rows]
    return CasStrideResponse(days=days, total_warnings=int(total or 0), items=items)


# ---------------------------------------------------------------------------
# /telemetry/encoding-distribution
# ---------------------------------------------------------------------------

@router.get(
    "/encoding-distribution",
    response_model=EncodingDistributionResponse,
    summary="Decode-path distribution across uploads",
    description=(
        "Counts which encoding (utf-8-sig / utf-8 / cp1252 / latin-1) the "
        "decode chain used per upload over the window. Surfaces clearinghouses "
        "sending non-UTF-8 EDI. **Note**: this metric is not yet recorded by "
        "the parser — endpoint returns `note: 'instrumentation pending'` and an "
        "empty items list. Will activate when CR-045 telemetry is added."
    ),
)
async def telemetry_encoding(
    days: int = Query(7, ge=1, le=365),
) -> EncodingDistributionResponse:
    # The parser doesn't currently emit a parse_event tagged with the decode
    # path. Returning empty + a structured note rather than 0-everywhere data
    # so the UI doesn't pretend it has signal.
    return EncodingDistributionResponse(
        days=days,
        items=[],
        note=(
            "Encoding telemetry not yet collected. The decode_edi() function "
            "in src/rcm/parsing/envelope.py does not currently emit a "
            "parse_event tagged with the resolved encoding. Add a "
            "parse_event(event_type='segment_handled', segment_name='_decode', "
            "details={encoding: ...}) emission to enable this metric."
        ),
    )


# ---------------------------------------------------------------------------
# /telemetry/validator-tiers
# ---------------------------------------------------------------------------

@router.get(
    "/validator-tiers",
    response_model=ValidatorTiersResponse,
    summary="Counts of ERROR/WARNING events per tier × variant",
    description=(
        "Aggregates parse_events of type 'validator_error' / 'validator_warning' "
        "by their details.validator (tier1_structural / tier2_ig / tier3_payer / "
        "tier4_business / parser) and details.severity. Joined to edi_files for "
        "service_variant breakdown. Empty when no validator events in window."
    ),
)
async def telemetry_validator_tiers(
    days: int = Query(7, ge=1, le=365),
) -> ValidatorTiersResponse:
    sql = """
        SELECT
            ef.service_variant_detected AS service_variant,
            COALESCE(pe.details->>'validator', 'parser') AS validator,
            CASE pe.event_type
                WHEN 'validator_error'   THEN 'ERROR'
                WHEN 'validator_warning' THEN 'WARNING'
                ELSE 'INFO'
            END AS severity,
            count(*)::bigint AS count
        FROM parse_events pe
        LEFT JOIN edi_files ef ON ef.id = pe.edi_file_id
        WHERE pe.event_type IN ('validator_error', 'validator_warning')
          AND pe.created_at >= now() - ($1 || ' days')::interval
        GROUP BY 1, 2, 3
        ORDER BY count DESC
    """
    c = await _connect()
    try:
        rows = await c.fetch(sql, str(days))
    finally:
        await c.close()

    items = [
        ValidatorTierPoint(
            service_variant=r["service_variant"],
            validator=r["validator"],
            severity=r["severity"],
            count=int(r["count"]),
        )
        for r in rows
    ]
    return ValidatorTiersResponse(days=days, items=items)


# ---------------------------------------------------------------------------
# /telemetry/reparse-log
# ---------------------------------------------------------------------------

@router.get(
    "/reparse-log",
    response_model=ReparseLogResponse,
    summary="Recent re-parse activity (file_name contains 'reparsed')",
    description=(
        "Surfaces edi_files rows that were created via the reparse path "
        "(src/rcm/parsing/parser.py:reparse stamps ' (reparsed)' on the "
        "filename). Includes parser_version, parse_status, claims_saved, "
        "claims_dropped from the parse_summary so you can compare runs."
    ),
)
async def telemetry_reparse_log(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(50, ge=1, le=200),
) -> ReparseLogResponse:
    sql = """
        SELECT
            id,
            file_name,
            parser_version,
            parse_status::text AS parse_status,
            to_char(parse_completed_at, 'YYYY-MM-DD"T"HH24:MI:SS') AS parse_completed_at,
            (parse_summary->>'claims_saved')::int AS claims_saved,
            (parse_summary->>'claims_dropped')::int AS claims_dropped
        FROM edi_files
        WHERE file_name LIKE '%reparsed%'
          AND created_at >= now() - ($1 || ' days')::interval
        ORDER BY id DESC
        LIMIT $2
    """
    c = await _connect()
    try:
        rows = await c.fetch(sql, str(days), limit)
    finally:
        await c.close()

    items = [
        ReparsePoint(
            edi_file_id=int(r["id"]),
            file_name=r["file_name"],
            parser_version=r["parser_version"],
            parse_status=r["parse_status"],
            parse_completed_at=r["parse_completed_at"],
            claims_saved=r["claims_saved"],
            claims_dropped=r["claims_dropped"],
        )
        for r in rows
    ]
    return ReparseLogResponse(days=days, items=items)
