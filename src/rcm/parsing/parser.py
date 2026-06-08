"""Top-level orchestrator.

    parse_edi(raw_text, file_name)              -> ParseContext           (sync, no DB)
    parse_and_save(session, raw_bytes, name)    -> EdiFile                (async, with DB)
    reparse(session, edi_file_id)               -> EdiFile (re-created)   (async)

Flow:
    1. decode bytes
    2. detect ISA + delimiters (raises EnvelopeError on bad envelope)
    3. count ISAs (warn on multiple; only first processed)
    4. tokenize
    5. walk segments, populate ctx.isa_*/gs_*/st_* counters + dispatch
    6. detect variant from GS08 → ctx.service_variant + ctx.file_type
    7. dispatch each segment through registry
    8. derive subtype per-claim
    9. run all 4 validators (Tier 3 only if session passed)
   10. persist via save_parse_context (idempotent dedup on content_hash)
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from rcm.core.config import settings
from rcm.models.ingestion import EdiFile
from rcm.parsing.context import ParseContext, RawSegmentRec
from rcm.parsing.dispatchers import dispatch_segment
from rcm.parsing.envelope import (
    Delimiters,
    EnvelopeError,
    count_isa_blocks,
    decode_edi,
    detect_delimiters,
    tokenize,
)
# Importing handlers triggers registry side-effects
from rcm.parsing import handlers  # noqa: F401
from rcm.parsing.persistence import DuplicateFileError, save_parse_context
from rcm.parsing.routing import detect_variant, finalize_subtypes
from rcm.parsing.safe import safe_element, safe_int
from rcm.parsing.validators import run_all as run_validators

logger = logging.getLogger(__name__)


def parse_edi(
    raw_text: str,
    file_name: str,
    *,
    parser_version: str | None = None,
    submission_date: date | None = None,
) -> ParseContext:
    """Parse raw EDI text into a ParseContext. No DB I/O."""
    parser_version = parser_version or settings.PARSER_VERSION
    submission_date = submission_date or date.today()

    delim = detect_delimiters(raw_text)

    ctx = ParseContext(
        file_name=file_name,
        raw_text=raw_text,
        delimiters=delim,
        parser_version=parser_version,
        parse_started_at=datetime.now(timezone.utc),
    )

    # Surface multi-ISA up front; validators will turn it into a WARNING
    ctx.isa_count = count_isa_blocks(raw_text)

    segments = tokenize(raw_text, delim.segment)
    if not segments:
        ctx.add_error(segment="ISA", field="segments",
                      message="No segments parsed from file body after tokenization")
        ctx.parse_completed_at = datetime.now(timezone.utc)
        return ctx

    # Pre-loop: read GS08 to pick the transaction-set dispatch namespace.
    # ISA + GS + ST come first; we don't dispatch them through the registry
    # (the handler registry treats them as silent skip) — we read them here.
    transaction_set = "837P"  # default if no GS found
    for seg in segments[:10]:
        elems = seg.split(delim.element)
        name = elems[0].strip().upper()
        if name == "ISA":
            ctx.isa_sender_id = safe_element(elems, 6) or None
            ctx.isa_receiver_id = safe_element(elems, 8) or None
            ctx.isa_control_no = safe_element(elems, 13) or None
        elif name == "GS":
            ctx.gs_control_no = safe_element(elems, 6) or None
            gs08 = safe_element(elems, 8)
            ctx.implementation_guide = gs08 or None
            ftype, variant = detect_variant(gs08)
            ctx.file_type = ftype
            ctx.service_variant = variant
            transaction_set = variant or "835" if ftype == "edi_835" else (variant or "837P")
            break

    # Main dispatch loop
    for pos, seg in enumerate(segments):
        ctx.segment_position = pos
        elements = seg.split(delim.element)
        name = elements[0].strip().upper()

        # Trailer counter capture (before/while dispatching)
        if name == "ISA":
            ctx.isa_count = max(ctx.isa_count, 1)
            # Already captured above for first ISA
        elif name == "GS":
            ctx.gs_count += 1
        elif name == "ST":
            ctx.st_count += 1
        elif name == "SE":
            ctx.se_count += 1
            v = safe_int(safe_element(elements, 1))
            if v is not None:
                ctx.se01_values.append(v)
        elif name == "GE":
            ctx.ge_count += 1
            v = safe_int(safe_element(elements, 1))
            if v is not None:
                ctx.ge01_values.append(v)
        elif name == "IEA":
            ctx.iea_count += 1
            v = safe_int(safe_element(elements, 1))
            if v is not None:
                ctx.iea01_values.append(v)

        # Dispatch
        status = dispatch_segment(transaction_set, name, elements, seg, ctx)

        # Snapshot this segment to raw_segments
        # claim_object_index / remittance_object_index let persistence bind the
        # row to its claim/remit after they get DB ids
        rs = RawSegmentRec(
            segment_name=name,
            segment_position=pos,
            raw_segment_text=seg,
            handler_status=status,
            parse_error=(seg if status == "parse_error" else None) and None,
            claim_object_index=(ctx.current_claim.object_index
                                if ctx.current_claim is not None else None),
            remittance_object_index=(ctx.current_remittance.object_index
                                     if ctx.current_remittance is not None else None),
        )
        ctx.raw_segments.append(rs)

    # Stamp every claim with the file submission_date (used by FE for service_to_submission_days)
    for c in ctx.claims:
        c.submission_date = submission_date

    # Derive claim_subtype from parsed content
    finalize_subtypes(ctx)

    ctx.parse_completed_at = datetime.now(timezone.utc)
    return ctx


async def parse_and_save(
    session: AsyncSession,
    raw_bytes: bytes,
    file_name: str,
    *,
    uploaded_by_user_id: int | None = None,
    parser_version: str | None = None,
) -> EdiFile:
    """End-to-end: decode -> parse -> validate (with DB) -> persist.

    Raises:
        EnvelopeError   — bad envelope (HTTP 400 territory)
        DuplicateFileError — content_hash already in DB (HTTP 409 territory)
    """
    text = decode_edi(raw_bytes)
    content_hash = hashlib.sha256(raw_bytes).hexdigest()

    ctx = parse_edi(text, file_name, parser_version=parser_version)
    await run_validators(ctx, session=session)

    edi_file = await save_parse_context(
        session, ctx, content_hash=content_hash, uploaded_by_user_id=uploaded_by_user_id,
    )
    return edi_file


async def reparse(session: AsyncSession, edi_file_id: int) -> EdiFile:
    """Re-run parse from the stored raw_text.

    Strategy: load the original EdiFile row, soft-delete it, then re-run
    `parse_and_save` against the stored raw_text. This generates a fresh
    EdiFile row + all dependents and returns it. The original is preserved
    in `deleted_at` for audit. To hard-delete instead, drop with CASCADE
    (out of scope for this phase).
    """
    from sqlalchemy import select, update

    src = (await session.execute(select(EdiFile).where(EdiFile.id == edi_file_id))).scalar_one()
    raw_bytes = src.raw_text.encode("utf-8")
    file_name = src.file_name + " (reparsed)"
    uploaded_by = src.uploaded_by_user_id

    # Soft-delete the prior row (its content_hash unique-index is partial WHERE deleted_at IS NULL)
    await session.execute(
        update(EdiFile).where(EdiFile.id == edi_file_id).values(deleted_at=datetime.now(timezone.utc))
    )
    await session.flush()

    return await parse_and_save(
        session, raw_bytes, file_name=file_name, uploaded_by_user_id=uploaded_by,
    )


__all__ = ["DuplicateFileError", "EnvelopeError", "parse_and_save", "parse_edi", "reparse"]
