"""POST /api/edi/upload  +  GET /api/edi/files

Public-facing wrappers around parse_and_save. The dev console at
/api/dev/edi/* requires `?confirm=true` per CR-038; this surface does not
because it IS the user-facing surface for the action.
"""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, File, HTTPException, UploadFile

from rcm.core.config import settings
from rcm.core.database import async_session
from rcm.parsing.envelope import EnvelopeError
from rcm.parsing.parser import parse_and_save
from rcm.parsing.persistence import DuplicateFileError
from rcm.schemas.public import (
    EdiFileListItem,
    EdiFilesListResponse,
    EdiUploadResponse,
    EdiValidationError,
)

router = APIRouter()


async def _connect() -> asyncpg.Connection:
    try:
        return await asyncpg.connect(dsn=settings.sync_database_url(), timeout=8)
    except Exception as exc:
        raise HTTPException(503, detail=f"Database unreachable: {type(exc).__name__}: {exc}")


def _enum_value(v):
    """Coerce SQLAlchemy enum-typed attributes to their string value.

    FileType / ParseStatus subclass `str`, and on a freshly INSERTed row
    SQLAlchemy leaves the raw string in the attribute instead of wrapping it
    back into the enum. Calling `.value` on a plain str blows up — hence
    this helper.
    """
    if v is None:
        return None
    if hasattr(v, "value"):
        return v.value
    return str(v)


@router.post(
    "/upload",
    response_model=EdiUploadResponse,
    summary="Upload and parse a single EDI file (837 or 835)",
)
async def upload_edi(file: UploadFile = File(...)) -> EdiUploadResponse:
    raw = await file.read()
    if len(raw) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
        raise HTTPException(
            413,
            detail=f"File exceeds MAX_UPLOAD_SIZE_MB={settings.MAX_UPLOAD_SIZE_MB}MB",
        )

    try:
        async with async_session() as session:
            edi_file = await parse_and_save(
                session, raw, file_name=file.filename or "uploaded.edi",
            )
            await session.commit()
            edi_file_id = edi_file.id
            file_type = _enum_value(edi_file.file_type)
            parser_version = edi_file.parser_version
            parse_status = _enum_value(edi_file.parse_status)
            summary = edi_file.parse_summary or {}
        # CR-090: schedule a debounced background refresh of mv_claim_labels.
        # No-op if no async loop, never blocks the response, errors are logged
        # but never surfaced. CR-083 train-time refresh remains the backstop.
        from rcm.core.mv_refresh import schedule_mv_refresh
        schedule_mv_refresh()
    except EnvelopeError as exc:
        return EdiUploadResponse(
            success=False, edi_file_id=None, file_type=None,
            claims_count=0, claim_lines_count=0, diagnoses_count=0,
            remittance_claims_count=0, adjustments_count=0,
            remark_codes_count=0, raw_segments_count=0,
            validation_errors=[], parser_version=settings.PARSER_VERSION,
            error=f"EnvelopeError: {exc}",
        )
    except DuplicateFileError as exc:
        # Re-uploading the same file is treated as a no-op — surface the
        # existing file's stored parse summary so the UI's Predict + Recommend
        # panels can run against it. Without this fallback the user can't
        # iterate on a file they've already ingested.
        return await _existing_file_response(int(exc.existing_id))

    # Pull live counts from the DB so the UI's "Parse Successful" panel
    # matches what was actually persisted.
    c = await _connect()
    try:
        claims_count   = await c.fetchval("SELECT count(*) FROM claims          WHERE edi_file_id = $1 AND deleted_at IS NULL", edi_file_id)
        lines_count    = await c.fetchval("SELECT count(*) FROM claim_lines     WHERE claim_id IN (SELECT id FROM claims WHERE edi_file_id = $1)", edi_file_id)
        diag_count     = await c.fetchval("SELECT count(*) FROM diagnoses       WHERE claim_id IN (SELECT id FROM claims WHERE edi_file_id = $1)", edi_file_id)
        remit_count    = await c.fetchval("SELECT count(*) FROM remittance_claims WHERE edi_file_id = $1", edi_file_id)
        adj_count      = await c.fetchval("SELECT count(*) FROM adjustments     WHERE remittance_claim_id IN (SELECT id FROM remittance_claims WHERE edi_file_id = $1)", edi_file_id)
        rem_code_count = await c.fetchval("SELECT count(*) FROM remark_codes    WHERE remittance_claim_id IN (SELECT id FROM remittance_claims WHERE edi_file_id = $1)", edi_file_id)
        seg_count      = await c.fetchval("SELECT count(*) FROM raw_segments    WHERE edi_file_id = $1", edi_file_id)
        pair_status, pair_message = await _check_pair_status(c, edi_file_id, file_type or "")
        n_staled = await _update_pending_pair_registry(c, edi_file_id, file_type or "")
        if n_staled:
            import logging as _logging
            _logging.getLogger(__name__).info(
                "pending_pair_registry: marked %d prior originals stale "
                "after upload of edi_file_id=%d", n_staled, edi_file_id,
            )
    finally:
        await c.close()

    # Surface a small slice of ERROR-severity parse_events so the UI's Errors
    # block has something to show.
    c = await _connect()
    try:
        err_rows = await c.fetch(
            """
            SELECT segment_name, details, claim_number
            FROM parse_events
            WHERE edi_file_id = $1
              AND event_type IN ('validator_error', 'claim_dropped', 'parse_error')
            ORDER BY id
            LIMIT 50
            """,
            edi_file_id,
        )
    finally:
        await c.close()

    import json as _json
    validation_errors: list[EdiValidationError] = []
    for r in err_rows:
        details = r["details"]
        if isinstance(details, str):
            try:
                details = _json.loads(details)
            except Exception:
                details = {}
        details = details or {}
        validation_errors.append(EdiValidationError(
            segment=r["segment_name"] or details.get("segment") or "?",
            field=details.get("field") or "?",
            message=details.get("message") or details.get("reason") or "validator error",
            severity=details.get("severity"),
            claim_identifier=r["claim_number"],
        ))

    return EdiUploadResponse(
        success=parse_status in ("parsed", "partial"),
        edi_file_id=edi_file_id,
        file_type=file_type,
        claims_count=int(claims_count or 0),
        claim_lines_count=int(lines_count or 0),
        diagnoses_count=int(diag_count or 0),
        remittance_claims_count=int(remit_count or 0),
        adjustments_count=int(adj_count or 0),
        remark_codes_count=int(rem_code_count or 0),
        raw_segments_count=int(seg_count or 0),
        validation_errors=validation_errors,
        parser_version=parser_version,
        error=summary.get("error"),
        is_duplicate=False,
        duplicate_of_file_id=None,
        pair_status=pair_status,
        pair_message=pair_message,
    )


async def _existing_file_response(edi_file_id: int) -> EdiUploadResponse:
    """Build an EdiUploadResponse for a file already in the DB. Used when
    a duplicate content_hash hits — the UI gets the same shape it would for a
    fresh upload, pointing at the existing edi_file_id."""
    c = await _connect()
    try:
        meta = await c.fetchrow(
            """
            SELECT id, file_type::text AS file_type,
                   parse_status::text AS parse_status, parser_version,
                   parse_summary
            FROM edi_files
            WHERE id = $1
            """,
            edi_file_id,
        )
        if meta is None:
            raise HTTPException(409, detail=(
                f"Duplicate content_hash but existing file (id={edi_file_id}) "
                "no longer in the DB"
            ))
        claims_count   = await c.fetchval("SELECT count(*) FROM claims          WHERE edi_file_id = $1 AND deleted_at IS NULL", edi_file_id)
        lines_count    = await c.fetchval("SELECT count(*) FROM claim_lines     WHERE claim_id IN (SELECT id FROM claims WHERE edi_file_id = $1)", edi_file_id)
        diag_count     = await c.fetchval("SELECT count(*) FROM diagnoses       WHERE claim_id IN (SELECT id FROM claims WHERE edi_file_id = $1)", edi_file_id)
        remit_count    = await c.fetchval("SELECT count(*) FROM remittance_claims WHERE edi_file_id = $1", edi_file_id)
        adj_count      = await c.fetchval("SELECT count(*) FROM adjustments     WHERE remittance_claim_id IN (SELECT id FROM remittance_claims WHERE edi_file_id = $1)", edi_file_id)
        rem_code_count = await c.fetchval("SELECT count(*) FROM remark_codes    WHERE remittance_claim_id IN (SELECT id FROM remittance_claims WHERE edi_file_id = $1)", edi_file_id)
        seg_count      = await c.fetchval("SELECT count(*) FROM raw_segments    WHERE edi_file_id = $1", edi_file_id)
        err_rows = await c.fetch(
            """
            SELECT segment_name, details, claim_number FROM parse_events
            WHERE edi_file_id = $1
              AND event_type IN ('validator_error', 'claim_dropped', 'parse_error')
            ORDER BY id LIMIT 50
            """,
            edi_file_id,
        )
        pair_status, pair_message = await _check_pair_status(
            c, edi_file_id, meta["file_type"] or "",
        )
    finally:
        await c.close()

    import json as _json
    summary = meta["parse_summary"] or {}
    if isinstance(summary, str):
        try:
            summary = _json.loads(summary)
        except Exception:
            summary = {}

    validation_errors: list[EdiValidationError] = []
    for r in err_rows:
        details = r["details"]
        if isinstance(details, str):
            try:
                details = _json.loads(details)
            except Exception:
                details = {}
        details = details or {}
        validation_errors.append(EdiValidationError(
            segment=r["segment_name"] or details.get("segment") or "?",
            field=details.get("field") or "?",
            message=details.get("message") or details.get("reason") or "validator error",
            severity=details.get("severity"),
            claim_identifier=r["claim_number"],
        ))

    parse_status = meta["parse_status"]
    return EdiUploadResponse(
        success=parse_status in ("parsed", "partial"),
        edi_file_id=edi_file_id,
        file_type=meta["file_type"],
        claims_count=int(claims_count or 0),
        claim_lines_count=int(lines_count or 0),
        diagnoses_count=int(diag_count or 0),
        remittance_claims_count=int(remit_count or 0),
        adjustments_count=int(adj_count or 0),
        remark_codes_count=int(rem_code_count or 0),
        raw_segments_count=int(seg_count or 0),
        validation_errors=validation_errors,
        parser_version=meta["parser_version"],
        error=None,
        is_duplicate=True,
        duplicate_of_file_id=edi_file_id,
        pair_status=pair_status,
        pair_message=pair_message,
    )


# ---------------------------------------------------------------------------
# Pair-completeness check + silent pending-pair registry
# ---------------------------------------------------------------------------
# CLM05[3] frequency code:
#   1   = original claim          (also empty / null)
#   7   = replacement (of a prior claim, same claim_number)
#   8   = void / cancel
#
# Behaviour rules (per user direction):
#   * Uploading an original whose replacement isn't here yet  → NO UI warning.
#     The original is recorded in `pending_pair_registry`; the next original
#     upload sweeps the registry and marks any still-pending entries stale.
#   * Uploading a replacement whose original isn't on file    → UI warning
#     (this is a user-side ordering error).
#   * Uploading an 835 whose 837 isn't on file                 → UI warning
#     (same — order issue).

_FREQ_ORIGINAL = ("1", "")          # empty + '1' both mean "first submission"
_FREQ_REPLACEMENT = ("7",)


async def _check_pair_status(
    c: asyncpg.Connection, edi_file_id: int, file_type: str,
) -> tuple[str | None, str | None]:
    """Returns (pair_status, pair_message) suitable for EdiUploadResponse.

    Only surfaces warnings for user-side ordering errors (replacement before
    original, 835 before 837). The 'original waiting for replacement' case is
    silent — handled by `_update_pending_pair_registry()` instead."""
    if file_type == "edi_837":
        rows = await c.fetch(
            """
            SELECT id, claim_number, COALESCE(frequency_code, '') AS freq
            FROM claims
            WHERE edi_file_id = $1 AND deleted_at IS NULL
            """,
            edi_file_id,
        )
        if not rows:
            return None, None

        # CR-052: batched lookup replaces the prior per-claim SELECT loop.
        # Build the set of replacement claim_numbers + the ids to exclude
        # (the file's own rows), then ask the DB which of those numbers
        # already have a matching original. Anything not in the result is
        # missing its pair.
        replacement_rows = [r for r in rows if r["freq"] in _FREQ_REPLACEMENT]
        missing_originals: list[str] = []
        if replacement_rows:
            cnums = [r["claim_number"] for r in replacement_rows]
            this_file_ids = [int(r["id"]) for r in replacement_rows]
            matched = await c.fetch(
                """
                SELECT DISTINCT claim_number
                FROM claims
                WHERE claim_number = ANY($1::varchar[])
                  AND id <> ALL($2::bigint[])
                  AND deleted_at IS NULL
                  AND COALESCE(frequency_code, '') = ANY($3::text[])
                """,
                cnums, this_file_ids, list(_FREQ_ORIGINAL),
            )
            matched_set = {r["claim_number"] for r in matched}
            missing_originals = [cn for cn in cnums if cn not in matched_set]

        if missing_originals:
            n = len(missing_originals)
            sample = ", ".join(missing_originals[:3])
            extra = "" if n <= 3 else f" (+{n - 3} more)"
            return (
                "replacement_no_original",
                f"This file contains {n} replacement claim{'s' if n != 1 else ''} "
                f"({sample}{extra}) with no matching original on file. "
                "Upload the original 837 to complete the pair.",
            )
        # Original-without-replacement is intentionally NOT warned.
        return "paired", None

    if file_type == "edi_835":
        rows = await c.fetch(
            """
            SELECT rc.id, cl.claim_number,
                   (cl.id IS NOT NULL) AS has_837
            FROM remittance_claims rc
            LEFT JOIN claims cl ON cl.id = rc.claim_id
            WHERE rc.edi_file_id = $1
            """,
            edi_file_id,
        )
        if not rows:
            return None, None

        orphans = [r for r in rows if not r["has_837"]]
        if orphans:
            n = len(orphans)
            return (
                "remit_no_837",
                f"{n} remittance{'s' if n != 1 else ''} in this 835 "
                f"{'has' if n == 1 else 'have'} no matching 837 claim "
                "in the database. Upload the original 837 first so the "
                "remits can be linked.",
            )
        return "paired", None

    return None, None


async def _update_pending_pair_registry(
    c: asyncpg.Connection, edi_file_id: int, file_type: str,
) -> int:
    """Silent registry maintenance — never returns text for the UI.

    Rules:
      * For every ORIGINAL 837 claim on this upload:
          - INSERT a row into `pending_pair_registry` with
            replacement_edi_file_id=NULL.
          - Then, scan the registry for ANY OTHER originals (uploaded earlier,
            i.e. row.original_edi_file_id != this upload) whose replacement
            is still NULL and that haven't been flagged stale yet — flag them
            with marked_stale_at = now().
      * For every REPLACEMENT 837 claim on this upload:
          - UPDATE the matching registry rows (same claim_number, still
            pending) to set replacement_edi_file_id + replacement_uploaded_at.

    Returns the number of registry rows just flagged stale (so the caller can
    log it). Never affects the response shape.
    """
    if file_type != "edi_837":
        return 0

    claims = await c.fetch(
        """
        SELECT claim_number, COALESCE(frequency_code, '') AS freq
        FROM claims
        WHERE edi_file_id = $1 AND deleted_at IS NULL
        """,
        edi_file_id,
    )
    if not claims:
        return 0

    # Bucket by role
    originals = [r["claim_number"] for r in claims if r["freq"] in _FREQ_ORIGINAL]
    replacements = [r["claim_number"] for r in claims if r["freq"] in _FREQ_REPLACEMENT]

    # Match replacements first so a same-file original→replacement pair
    # closes immediately without going stale.
    if replacements:
        await c.execute(
            """
            UPDATE pending_pair_registry
            SET replacement_edi_file_id = $1,
                replacement_uploaded_at = now()
            WHERE claim_number = ANY($2::varchar[])
              AND replacement_edi_file_id IS NULL
            """,
            edi_file_id, replacements,
        )

    # Insert new originals
    if originals:
        await c.executemany(
            """
            INSERT INTO pending_pair_registry
                (claim_number, original_edi_file_id, original_uploaded_at)
            VALUES ($1, $2, now())
            """,
            [(cn, edi_file_id) for cn in originals],
        )

        # On a new original upload, sweep the registry for prior originals
        # still pending and mark them stale.
        n_staled = await c.fetchval(
            """
            WITH staled AS (
                UPDATE pending_pair_registry
                SET marked_stale_at = now()
                WHERE replacement_edi_file_id IS NULL
                  AND marked_stale_at IS NULL
                  AND original_edi_file_id <> $1
                RETURNING id
            )
            SELECT count(*) FROM staled
            """,
            edi_file_id,
        )
        return int(n_staled or 0)

    return 0


@router.get("/pending-pairs")
async def pending_pairs(
    only_stale: bool = False,
    only_unresolved: bool = True,
    limit: int = 200,
) -> dict:
    """Inspect the silent pending-pair registry.

    Defaults: returns originals still waiting for their replacement
    (`only_unresolved=True`). Pass `only_stale=true` to filter further to
    those flagged stale by a subsequent original upload — these are the
    "noticed but never replaced" cases the team should chase down.

    This endpoint is the only surface for the registry; it is NOT shown in
    the upload card UI per design.
    """
    where = []
    if only_unresolved:
        where.append("replacement_edi_file_id IS NULL")
    if only_stale:
        where.append("marked_stale_at IS NOT NULL")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    c = await _connect()
    try:
        rows = await c.fetch(f"""
            SELECT id, claim_number,
                   original_edi_file_id,
                   to_char(original_uploaded_at,    'YYYY-MM-DD"T"HH24:MI:SS') AS original_uploaded_at,
                   replacement_edi_file_id,
                   to_char(replacement_uploaded_at, 'YYYY-MM-DD"T"HH24:MI:SS') AS replacement_uploaded_at,
                   to_char(marked_stale_at,         'YYYY-MM-DD"T"HH24:MI:SS') AS marked_stale_at
            FROM pending_pair_registry
            {where_sql}
            ORDER BY id DESC
            LIMIT $1
        """, limit)
        total_pending = await c.fetchval(
            "SELECT count(*) FROM pending_pair_registry WHERE replacement_edi_file_id IS NULL",
        )
        total_stale = await c.fetchval(
            "SELECT count(*) FROM pending_pair_registry WHERE marked_stale_at IS NOT NULL",
        )
        total_paired = await c.fetchval(
            "SELECT count(*) FROM pending_pair_registry WHERE replacement_edi_file_id IS NOT NULL",
        )
    finally:
        await c.close()

    return {
        "items": [dict(r) for r in rows],
        "totals": {
            "pending": int(total_pending or 0),
            "stale":   int(total_stale or 0),
            "paired":  int(total_paired or 0),
        },
    }


@router.get("/files", response_model=EdiFilesListResponse)
async def edi_files() -> EdiFilesListResponse:
    c = await _connect()
    try:
        rows = await c.fetch(
            """
            SELECT
                ef.id, ef.file_name,
                ef.file_type::text AS file_type,
                ef.parse_status::text AS parse_status,
                to_char(ef.created_at, 'YYYY-MM-DD"T"HH24:MI:SS') AS uploaded_at,
                (SELECT count(*) FROM claims c
                 WHERE c.edi_file_id = ef.id AND c.deleted_at IS NULL) AS claims_count
            FROM edi_files ef
            WHERE ef.deleted_at IS NULL
            ORDER BY ef.id DESC
            LIMIT 200
            """,
        )
    finally:
        await c.close()
    items = [EdiFileListItem(
        id=int(r["id"]), file_name=r["file_name"], file_type=r["file_type"],
        parse_status=r["parse_status"], claims_count=int(r["claims_count"] or 0),
        uploaded_at=r["uploaded_at"],
    ) for r in rows]
    return EdiFilesListResponse(items=items, total=len(items))
