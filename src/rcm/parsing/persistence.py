"""Bulk Core inserts that turn a ParseContext into rows.

Decoupled from parsing so:
    * Unit tests can parse without touching the DB.
    * Re-parse can re-run from stored raw_text without losing the previous rows.
    * A single transaction wraps the whole save (atomic-or-nothing).

Pattern:
    1. Insert / upsert master data (payers, patients, providers, subscribers)
    2. Insert edi_files row (insert-then-catch on content_hash for dedup)
    3. Bulk-insert claims, retrieve ids via INSERT...RETURNING
    4. Build resolved (claim_id-bound) lists for claim_lines, diagnoses,
       certifications, amounts, attachments, home_care_episodes,
       transport_certifications. Bulk insert each.
    5. Bulk-insert remittances with RETURNING, then bulk-insert adjustments
       and remark_codes against the resolved remit ids.
    6. Bulk-insert raw_segments (always — even for dropped claims)
    7. Bulk-insert parse_events

Public surface unchanged from the prior implementation:
    save_parse_context(session, ctx, *, content_hash, uploaded_by_user_id=None) -> EdiFile
    DuplicateFileError

PARSER-STRESS-001 followup: this rewrite replaces the per-claim
session.add()+flush() pattern that did one DB round-trip per claim, plus the
per-line/per-dx ORM adds that scaled at ~158 claims/sec. With bulk
INSERT+RETURNING the round-trip count is now O(table count), not O(row count).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from rcm.models.claims import Claim, ClaimLine, Diagnosis, Patient, Provider, Subscriber
from rcm.models.ingestion import EdiFile, ParseEvent, RawSegment
from rcm.models.reference import Payer
from rcm.models.remittance import Adjustment, RemarkCode, RemittanceClaim
from rcm.models.variant_extensions import (
    ClaimAmount,
    ClaimAttachment,
    ClaimCertification,
    HomeCareEpisode,
    TransportCertification,
)
from rcm.parsing.context import ParseContext

logger = logging.getLogger(__name__)

_BULK_CHUNK = 1000


class DuplicateFileError(Exception):
    """Raised when an EDI file with the same content_hash has already been ingested."""

    def __init__(self, content_hash: str, existing_id: int):
        super().__init__(
            f"EDI file with content_hash {content_hash} already exists (id={existing_id})"
        )
        self.content_hash = content_hash
        self.existing_id = existing_id


async def save_parse_context(
    session: AsyncSession,
    ctx: ParseContext,
    *,
    content_hash: str,
    uploaded_by_user_id: int | None = None,
) -> EdiFile:
    """Persist the full ParseContext in one transaction.

    Raises `DuplicateFileError` on content_hash collision with a live (not
    soft-deleted) edi_files row.
    """
    # ---- 1. master-data upserts (now bulk) -------------------------------
    payer_id_by_name = await _bulk_upsert_payers(session, ctx)
    patient_id_by_member = await _bulk_upsert_patients(session, ctx)
    provider_id_by_npi = await _bulk_upsert_providers(session, ctx)
    subscriber_id_by_key = await _bulk_insert_subscribers(
        session, ctx, patient_id_by_member, payer_id_by_name,
    )

    # ---- 2. edi_files (insert-then-catch on dup) -------------------------
    edi_file = EdiFile(
        file_type=ctx.file_type,
        implementation_guide=ctx.implementation_guide,
        sender_id=ctx.isa_sender_id,
        receiver_id=ctx.isa_receiver_id,
        interchange_control_no=ctx.isa_control_no,
        functional_group_control_no=ctx.gs_control_no,
        file_name=ctx.file_name,
        content_hash=content_hash,
        raw_text=ctx.raw_text,
        parser_version=ctx.parser_version,
        service_variant_detected=ctx.service_variant,
        claim_subtype_detected=ctx.claim_subtype,
        parse_status="parsed",
        parse_started_at=ctx.parse_started_at,
        parse_completed_at=ctx.parse_completed_at or datetime.now(timezone.utc),
        parse_summary=_build_summary(ctx),
        uploaded_by_user_id=uploaded_by_user_id,
    )
    session.add(edi_file)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        # Prefer a LIVE row (deleted_at IS NULL) — the partial unique index
        # only protects live rows, and a soft-deleted row may still hold the
        # same content_hash from a prior aborted/replaced ingest. Returning
        # the live row keeps the UI pointing at the file with actual data.
        existing = (await session.execute(
            select(EdiFile.id).where(
                EdiFile.content_hash == content_hash,
                EdiFile.deleted_at.is_(None),
            ).order_by(EdiFile.id.desc()).limit(1)
        )).scalar()
        if existing is None:
            # No live row — fall back to whatever exists (caller will get the
            # soft-deleted row's data, which is still better than crashing).
            existing = (await session.execute(
                select(EdiFile.id).where(EdiFile.content_hash == content_hash)
                .order_by(EdiFile.id.desc()).limit(1)
            )).scalar()
        raise DuplicateFileError(content_hash, existing or 0) from None

    # ---- 3. claims: bulk INSERT...RETURNING ------------------------------
    claim_id_by_index = await _bulk_insert_claims(
        session, ctx, edi_file.id,
        payer_id_by_name, patient_id_by_member, subscriber_id_by_key, provider_id_by_npi,
    )

    # ---- 4. claim children — bulk inserts with resolved claim_ids --------
    await _bulk_insert_claim_lines(session, ctx, claim_id_by_index)
    await _bulk_insert_diagnoses(session, ctx, claim_id_by_index)
    await _bulk_insert_certifications(session, ctx, claim_id_by_index)
    await _bulk_insert_claim_amounts(session, ctx, claim_id_by_index)
    await _bulk_insert_claim_attachments(session, ctx, claim_id_by_index)
    await _bulk_insert_home_care_episodes(session, ctx, claim_id_by_index)
    await _bulk_insert_transport_certs(session, ctx, claim_id_by_index)

    # ---- 5. remittances + adjustments + remarks --------------------------
    remit_id_by_index = await _bulk_insert_remittances(
        session, ctx, edi_file.id, claim_id_by_index,
    )
    await _bulk_insert_adjustments(session, ctx, remit_id_by_index)
    await _bulk_insert_remarks(session, ctx, remit_id_by_index)

    # ---- 5.5. propagate CLP02 → claims.claim_status ----------------------
    # Scoped to the claims this 835 actually references (CR-052). Skipped on
    # 837-only uploads (no remittances).
    if ctx.remittances:
        await _propagate_remit_status_to_claims(session, edi_file.id)

    # ---- 6. raw_segments (always — incl. validator_dropped rows) ---------
    await _bulk_insert_raw_segments(
        session, ctx, edi_file.id, claim_id_by_index, remit_id_by_index,
    )

    # ---- 7. parse_events ------------------------------------------------
    await _bulk_insert_parse_events(session, ctx, edi_file.id)

    await session.commit()
    return edi_file


# =============================================================================
# Master-data upserts (bulk)
# =============================================================================

async def _bulk_upsert_payers(session: AsyncSession, ctx: ParseContext) -> dict[str, int]:
    # CR-069: concurrency-safe upsert. INSERT ... ON CONFLICT DO NOTHING on
    # the existing payers.canonical_name UNIQUE constraint, then SELECT to
    # retrieve every requested id (both newly-inserted and pre-existing). The
    # prior check-then-insert pattern raised IntegrityError when two
    # concurrent uploads of files sharing the same payer raced on cold-start.
    by_name: dict[str, Any] = {}
    for p in ctx.payers:
        if p.canonical_name and p.canonical_name not in by_name:
            by_name[p.canonical_name] = p
    if not by_name:
        return {}

    dicts = [
        {
            "canonical_name": n,
            "sender_id": r.sender_id,
            "receiver_id": r.receiver_id,
            "payer_taxonomy": r.payer_taxonomy,
        }
        for n, r in by_name.items()
    ]
    stmt = pg_insert(Payer).values(dicts).on_conflict_do_nothing(
        index_elements=["canonical_name"],
    )
    await session.execute(stmt)

    rows = (await session.execute(
        select(Payer.id, Payer.canonical_name).where(Payer.canonical_name.in_(by_name))
    )).all()
    return {n: i for i, n in rows}


async def _bulk_upsert_patients(session: AsyncSession, ctx: ParseContext) -> dict[str, int]:
    # CR-069: concurrency-safe upsert against patients.member_id UNIQUE.
    # Merge multiple PatientRec instances for the same member_id (e.g., one
    # from NM1*IL self-claim + one from NM1*QC), keeping the last seen.
    by_member: dict[str, Any] = {}
    for p in ctx.patients:
        if not p.member_id:
            continue
        by_member[p.member_id] = p

    if not by_member:
        return {}

    dicts = [
        {
            "member_id": m,
            "first_name": r.first_name,
            "last_name": r.last_name,
            "date_of_birth": r.date_of_birth,
            "gender": r.gender,
        }
        for m, r in by_member.items()
    ]
    stmt = pg_insert(Patient).values(dicts).on_conflict_do_nothing(
        index_elements=["member_id"],
    )
    await session.execute(stmt)

    rows = (await session.execute(
        select(Patient.id, Patient.member_id).where(Patient.member_id.in_(by_member))
    )).all()
    return {m: i for i, m in rows}


async def _bulk_upsert_providers(session: AsyncSession, ctx: ParseContext) -> dict[str, int]:
    # CR-069: concurrency-safe upsert against providers.npi UNIQUE. This was
    # the highest-contention path in the pre-verification: every LR1K_D file
    # shared the same billing NPI 1326242504, producing the observed
    # UniqueViolation cascade under 8-wide concurrency.
    by_npi: dict[str, Any] = {}
    for p in ctx.providers:
        if not p.npi:
            continue
        # Last-write-wins on duplicates within a file
        by_npi[p.npi] = p

    if not by_npi:
        return {}

    dicts = [
        {
            "npi": n,
            "provider_type": r.provider_type,
            "organization_name": r.organization_name,
            "last_name": r.last_name,
            "first_name": r.first_name,
            "taxonomy_code": r.taxonomy_code,
            "state": r.state,
        }
        for n, r in by_npi.items()
    ]
    stmt = pg_insert(Provider).values(dicts).on_conflict_do_nothing(
        index_elements=["npi"],
    )
    await session.execute(stmt)

    rows = (await session.execute(
        select(Provider.id, Provider.npi).where(Provider.npi.in_(by_npi))
    )).all()
    return {n: i for i, n in rows}


async def _bulk_insert_subscribers(
    session: AsyncSession,
    ctx: ParseContext,
    patient_ids: dict[str, int],
    payer_ids: dict[str, int],
) -> dict[tuple[str, str], int]:
    """No DB-level uniqueness on subscribers (intentional — same member_id
    can subscribe to multiple payers). Dedup in-memory within a file by
    (member_id, payer_name)."""
    by_key: dict[tuple[str, str], Any] = {}
    for s in ctx.subscribers:
        if not s.member_id:
            continue
        key = _sub_key(s.member_id, s.payer_canonical_name)
        if key not in by_key:
            by_key[key] = s

    if not by_key:
        return {}

    keys_in_order = list(by_key.keys())
    dicts = [
        {
            "member_id": s.member_id,
            "patient_id": patient_ids.get(s.patient_member_id or "")
                          if s.patient_member_id else None,
            "payer_id": payer_ids.get(s.payer_canonical_name or "")
                        if s.payer_canonical_name else None,
            "relationship_code": s.relationship_code,
            "group_number": s.group_number,
            "policy_number": s.policy_number,
            "coordination_of_benefits": s.coordination_of_benefits,
        }
        for s in (by_key[k] for k in keys_in_order)
    ]
    rows = await _bulk_insert_returning(session, Subscriber, dicts, [Subscriber.id])
    return {k: cid for k, (cid,) in zip(keys_in_order, rows)}


# =============================================================================
# Claims + children (bulk)
# =============================================================================

async def _bulk_insert_claims(
    session: AsyncSession,
    ctx: ParseContext,
    edi_file_id: int,
    payer_ids: dict[str, int],
    patient_ids: dict[str, int],
    subscriber_ids: dict[tuple[str, str], int],
    provider_ids: dict[str, int],
) -> dict[int, int]:
    object_indices: list[int] = []
    dicts: list[dict[str, Any]] = []

    for i, c in enumerate(ctx.claims):
        if c.dropped:
            continue
        object_indices.append(i)
        dicts.append({
            "edi_file_id": edi_file_id,
            "service_variant": c.service_variant,
            "claim_subtype": c.claim_subtype,
            "claim_number": c.claim_number,
            "payer_id": payer_ids.get(c.payer_canonical_name or ""),
            "patient_id": patient_ids.get(c.patient_member_id or ""),
            "subscriber_id": subscriber_ids.get(
                _sub_key(c.subscriber_member_id, c.payer_canonical_name)
            ),
            "billing_provider_id": provider_ids.get(c.billing_provider_npi or ""),
            "rendering_provider_id": provider_ids.get(c.rendering_provider_npi or ""),
            "referring_provider_id": provider_ids.get(c.referring_provider_npi or ""),
            "total_charge_amount": c.total_charge_amount,
            "facility_type_code": c.facility_type_code,
            "frequency_code": c.frequency_code,
            "claim_status": c.claim_status,
            "service_from_date": c.service_from_date,
            "service_to_date": c.service_to_date,
            "submission_date": c.submission_date or date.today(),
            "authorization_number": c.authorization_number,
            "referral_number": c.referral_number,
            "previous_payer_claim_control_no": c.previous_payer_claim_control_no,
            "variant_data": c.variant_data or None,
            "raw_claim_segment": c.raw_claim_segment,
        })

    if not dicts:
        return {}

    rows = await _bulk_insert_returning(session, Claim, dicts, [Claim.id])
    return {idx: cid for idx, (cid,) in zip(object_indices, rows)}


async def _bulk_insert_claim_lines(
    session: AsyncSession,
    ctx: ParseContext,
    claim_ids: dict[int, int],
) -> None:
    rows: list[dict[str, Any]] = []
    for i, c in enumerate(ctx.claims):
        cid = claim_ids.get(i)
        if cid is None:
            continue
        for line in c.lines:
            rows.append({
                "claim_id": cid,
                "line_number": line.line_number,
                "procedure_code": line.procedure_code,
                "procedure_code_qualifier": line.procedure_code_qualifier,
                "modifier1": line.modifier1,
                "modifier2": line.modifier2,
                "modifier3": line.modifier3,
                "modifier4": line.modifier4,
                "billed_amount": line.billed_amount,
                "units": line.units,
                "units_basis": line.units_basis,
                "place_of_service": line.place_of_service,
                "service_date": line.service_date,
                "diagnosis_pointers": line.diagnosis_pointers or None,
                "revenue_code": line.revenue_code,
                "hipps_code": line.hipps_code,
                "tooth_number": line.tooth_number,
                "tooth_surfaces": line.tooth_surfaces,
                "ndc_drug_code": line.ndc_drug_code,
                "line_data": line.line_data or None,
                "raw_sv_segment": line.raw_sv_segment,
            })
    await _bulk_insert(session, ClaimLine, rows)


async def _bulk_insert_diagnoses(
    session: AsyncSession,
    ctx: ParseContext,
    claim_ids: dict[int, int],
) -> None:
    rows: list[dict[str, Any]] = []
    for i, c in enumerate(ctx.claims):
        cid = claim_ids.get(i)
        if cid is None:
            continue
        for dx in c.diagnoses:
            rows.append({
                "claim_id": cid,
                "sequence_number": dx.sequence_number,
                "diagnosis_code": dx.diagnosis_code,
                "diagnosis_type": dx.diagnosis_type,
                "diagnosis_qualifier": dx.diagnosis_qualifier,
                "present_on_admission": dx.present_on_admission,
            })
    await _bulk_insert(session, Diagnosis, rows)


async def _bulk_insert_certifications(
    session: AsyncSession,
    ctx: ParseContext,
    claim_ids: dict[int, int],
) -> None:
    rows: list[dict[str, Any]] = []
    for i, c in enumerate(ctx.claims):
        cid = claim_ids.get(i)
        if cid is None:
            continue
        for cert in c.certifications:
            rows.append({
                "claim_id": cid,
                "certification_type": cert.certification_type,
                "raw_segment": cert.raw_segment,
                "structured_data": cert.structured_data or None,
            })
    await _bulk_insert(session, ClaimCertification, rows)


async def _bulk_insert_claim_amounts(
    session: AsyncSession,
    ctx: ParseContext,
    claim_ids: dict[int, int],
) -> None:
    rows: list[dict[str, Any]] = []
    for i, c in enumerate(ctx.claims):
        cid = claim_ids.get(i)
        if cid is None:
            continue
        for amt in c.amounts:
            rows.append({
                "claim_id": cid,
                "amount_qualifier": amt.amount_qualifier,
                "amount": amt.amount,
            })
    await _bulk_insert(session, ClaimAmount, rows)


async def _bulk_insert_claim_attachments(
    session: AsyncSession,
    ctx: ParseContext,
    claim_ids: dict[int, int],
) -> None:
    rows: list[dict[str, Any]] = []
    for i, c in enumerate(ctx.claims):
        cid = claim_ids.get(i)
        if cid is None:
            continue
        for att in c.attachments:
            rows.append({
                "claim_id": cid,
                "report_type_code": att.report_type_code,
                "transmission_code": att.transmission_code,
                "attachment_control_no": att.attachment_control_no,
            })
    await _bulk_insert(session, ClaimAttachment, rows)


async def _bulk_insert_home_care_episodes(
    session: AsyncSession,
    ctx: ParseContext,
    claim_ids: dict[int, int],
) -> None:
    rows: list[dict[str, Any]] = []
    for i, c in enumerate(ctx.claims):
        cid = claim_ids.get(i)
        if cid is None or c.home_care_episode is None:
            continue
        h = c.home_care_episode
        rows.append({
            "claim_id": cid,
            "episode_start_date": h.episode_start_date,
            "episode_end_date": h.episode_end_date,
            "hipps_code": h.hipps_code,
            "oasis_assessment_date": h.oasis_assessment_date,
            "visit_count": h.visit_count,
            "discipline_mix": h.discipline_mix or None,
            "is_lupa": h.is_lupa,
            "homebound_certified": h.homebound_certified,
            "plan_of_care_signed_date": h.plan_of_care_signed_date,
        })
    await _bulk_insert(session, HomeCareEpisode, rows)


async def _bulk_insert_transport_certs(
    session: AsyncSession,
    ctx: ParseContext,
    claim_ids: dict[int, int],
) -> None:
    rows: list[dict[str, Any]] = []
    for i, c in enumerate(ctx.claims):
        cid = claim_ids.get(i)
        if cid is None or c.transport_cert is None:
            continue
        t = c.transport_cert
        rows.append({
            "claim_id": cid,
            "transport_miles": t.transport_miles,
            "patient_weight_lbs": t.patient_weight_lbs,
            "transport_reason_code": t.transport_reason_code,
            "round_trip": t.round_trip,
            "emergent": t.emergent,
            "origin_address": t.origin_address,
            "destination_address": t.destination_address,
            "level_of_service": t.level_of_service,
        })
    await _bulk_insert(session, TransportCertification, rows)


# =============================================================================
# Remittances + adjustments + remarks (bulk)
# =============================================================================

async def _bulk_insert_remittances(
    session: AsyncSession,
    ctx: ParseContext,
    edi_file_id: int,
    claim_ids_in_file: dict[int, int],
) -> dict[int, int]:
    """Bulk-insert non-dropped remittances. Orphan CLPs (no matching claim
    in this file AND none in DB) are skipped with a parse_event log."""
    if not ctx.remittances:
        return {}

    # Build claim_id_by_number from this file's just-inserted claims
    claim_id_by_number: dict[str, int] = {}
    for i, c in enumerate(ctx.claims):
        cid = claim_ids_in_file.get(i)
        if cid is not None:
            claim_id_by_number[c.claim_number] = cid

    # Resolve any remit claim_numbers not in this file by hitting the DB
    needed = {
        r.claim_number
        for r in ctx.remittances
        if not r.dropped and r.claim_number not in claim_id_by_number
    }
    if needed:
        existing = (await session.execute(
            select(Claim.id, Claim.claim_number).where(Claim.claim_number.in_(needed))
        )).all()
        for cid, cn in existing:
            claim_id_by_number[cn] = cid

    object_indices: list[int] = []
    dicts: list[dict[str, Any]] = []

    for i, r in enumerate(ctx.remittances):
        if r.dropped:
            continue
        target_cid = claim_id_by_number.get(r.claim_number)
        if target_cid is None:
            ctx.add_event(
                "validator_warning",
                segment_name="CLP",
                details={"reason": "orphan_clp_unmatched", "claim_number": r.claim_number},
            )
            continue
        object_indices.append(i)
        dicts.append({
            "claim_id": target_cid,
            "edi_file_id": edi_file_id,
            "claim_status_code": r.claim_status_code,
            "billed_amount": r.billed_amount,
            "paid_amount": r.paid_amount,
            "patient_responsibility_amount": r.patient_responsibility_amount,
            "payer_claim_control_number": r.payer_claim_control_number,
            "remittance_date": r.remittance_date,
            "payer_paid_date": r.payer_paid_date,
            "raw_clp_segment": r.raw_clp_segment,
        })

    if not dicts:
        return {}

    rows = await _bulk_insert_returning(session, RemittanceClaim, dicts, [RemittanceClaim.id])
    return {idx: rcid for idx, (rcid,) in zip(object_indices, rows)}


async def _bulk_insert_adjustments(
    session: AsyncSession,
    ctx: ParseContext,
    remit_ids: dict[int, int],
) -> None:
    rows: list[dict[str, Any]] = []
    for i, r in enumerate(ctx.remittances):
        rcid = remit_ids.get(i)
        if rcid is None:
            continue
        for adj in r.adjustments:
            rows.append({
                "remittance_claim_id": rcid,
                "adjustment_group_code": adj.adjustment_group_code,
                "adjustment_reason_code": adj.adjustment_reason_code,
                "adjustment_amount": adj.adjustment_amount,
                "quantity": adj.quantity,
                "raw_cas_segment": adj.raw_cas_segment,
            })
    await _bulk_insert(session, Adjustment, rows)


async def _bulk_insert_remarks(
    session: AsyncSession,
    ctx: ParseContext,
    remit_ids: dict[int, int],
) -> None:
    rows: list[dict[str, Any]] = []
    for i, r in enumerate(ctx.remittances):
        rcid = remit_ids.get(i)
        if rcid is None:
            continue
        for rem in r.remark_codes:
            rows.append({
                "remittance_claim_id": rcid,
                "remark_code": rem.remark_code,
                "raw_lq_segment": rem.raw_lq_segment,
            })
    await _bulk_insert(session, RemarkCode, rows)


# =============================================================================
# raw_segments + parse_events (bulk)
# =============================================================================

async def _bulk_insert_raw_segments(
    session: AsyncSession,
    ctx: ParseContext,
    edi_file_id: int,
    claim_ids: dict[int, int],
    remit_ids: dict[int, int],
) -> None:
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for rs in ctx.raw_segments:
        claim_id = None
        remit_claim_id = None
        if rs.claim_object_index is not None:
            claim_id = claim_ids.get(rs.claim_object_index)
            if claim_id is None:
                # Was dropped — preserve audit trail with validator_dropped status
                rs.handler_status = "validator_dropped"
        if rs.remittance_object_index is not None:
            remit_claim_id = remit_ids.get(rs.remittance_object_index)

        rows.append({
            "created_at": now,
            "edi_file_id": edi_file_id,
            "claim_id": claim_id,
            "remittance_claim_id": remit_claim_id,
            "segment_name": rs.segment_name,
            "segment_position": rs.segment_position,
            "raw_segment_text": rs.raw_segment_text,
            "parse_error": rs.parse_error,
            "handler_status": rs.handler_status,
        })
    await _bulk_insert(session, RawSegment, rows)


async def _bulk_insert_parse_events(
    session: AsyncSession, ctx: ParseContext, edi_file_id: int,
) -> None:
    now = datetime.now(timezone.utc)
    rows = [
        {
            "created_at": now,
            "edi_file_id": edi_file_id,
            "event_type": e.event_type,
            "segment_name": e.segment_name,
            "segment_position": e.segment_position,
            "claim_number": e.claim_number,
            "details": e.details or None,
        }
        for e in ctx.parse_events
    ]
    await _bulk_insert(session, ParseEvent, rows)


# =============================================================================
# Helpers
# =============================================================================

async def _bulk_insert(
    session: AsyncSession,
    model: Any,
    dicts: list[dict[str, Any]],
    chunk_size: int = _BULK_CHUNK,
) -> None:
    """Bulk Core insert. No RETURNING, no per-row work."""
    if not dicts:
        return
    stmt = insert(model)
    for chunk in _chunked(dicts, chunk_size):
        await session.execute(stmt, chunk)


async def _bulk_insert_returning(
    session: AsyncSession,
    model: Any,
    dicts: list[dict[str, Any]],
    return_cols: list[Any],
    chunk_size: int = _BULK_CHUNK,
) -> list[tuple]:
    """Bulk Core insert with INSERT...RETURNING.

    Uses `sort_by_parameter_order=True` so the returned rows correspond
    exactly to the input dict order (SQLAlchemy 2.0.10+ feature backed by
    PG's insertmanyvalues mode).
    """
    if not dicts:
        return []

    out: list[tuple] = []
    for chunk in _chunked(dicts, chunk_size):
        stmt = insert(model).returning(*return_cols, sort_by_parameter_order=True)
        result = await session.execute(stmt, chunk)
        out.extend(result.tuples().all())
    return out


def _sub_key(member_id: str | None, payer_name: str | None) -> tuple[str, str]:
    return ((member_id or "").strip(), (payer_name or "").strip())


def _chunked(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _build_summary(ctx: ParseContext) -> dict[str, Any]:
    return {
        "claims_saved": ctx.saved_claim_count(),
        "claims_dropped": ctx.dropped_claim_count(),
        "remittances_seen": len(ctx.remittances),
        "remittances_dropped": sum(1 for r in ctx.remittances if r.dropped),
        "drop_reasons_by_field": ctx.dropped_reasons_by_field(),
        "unhandled_segments": dict(ctx.unhandled_segment_counts),
        "error_count": sum(1 for e in ctx.parse_errors if e.severity == "ERROR"),
        "warning_count": sum(1 for e in ctx.parse_errors if e.severity == "WARNING"),
        "service_variant": ctx.service_variant,
        "claim_subtype": ctx.claim_subtype,
        "raw_segment_count": len(ctx.raw_segments),
        "parse_event_count": len(ctx.parse_events),
    }


async def _propagate_remit_status_to_claims(
    session: AsyncSession, edi_file_id: int,
) -> None:
    """After 835 ingest, recompute `claims.claim_status` for ONLY the claims
    this 835 file actually references, and only write rows whose status
    actually changes.

    Business rules unchanged from v1:
      * any remit with claim_status_code = '4'                  → denied
      * else any remit with paid_amount > 0 AND < billed_amount → partially_paid
      * else any remit with paid_amount >= billed_amount > 0    → paid
      * otherwise (no informative remit)                        → leave as-is

    CR-052 performance fix: previously the UPDATE filtered only on
    "claim is adjudicated" and "claim is alive" — which meant *every* 835
    upload rewrote *every* adjudicated claim in the entire database (12k
    rows per file × thousands of files = tens of millions of wasted row
    touches, dead-tuple churn, and (pre-CR-051) audit_log fanout).
    The new query scopes to the current file's claims via the
    `remittance_claims.edi_file_id = :file_id` filter, and the
    `IS DISTINCT FROM` guard skips no-op writes entirely.
    Final `claim_status` values are unchanged.
    """
    from sqlalchemy import text

    await session.execute(text("""
        UPDATE claims c
        SET claim_status = computed.new_status
        FROM (
            SELECT rc.claim_id AS id,
                   CASE
                       WHEN bool_or(rc.claim_status_code = '4')
                            THEN 'denied'::claim_status
                       WHEN bool_or(rc.paid_amount > 0
                                    AND rc.paid_amount < rc.billed_amount)
                            THEN 'partially_paid'::claim_status
                       WHEN bool_or(rc.paid_amount > 0
                                    AND rc.paid_amount >= rc.billed_amount)
                            THEN 'paid'::claim_status
                       ELSE NULL::claim_status
                   END AS new_status
            FROM remittance_claims rc
            WHERE rc.claim_id IN (
                SELECT DISTINCT claim_id
                FROM remittance_claims
                WHERE edi_file_id = :file_id
                  AND claim_id IS NOT NULL
            )
            GROUP BY rc.claim_id
        ) AS computed
        WHERE c.id = computed.id
          AND c.deleted_at IS NULL
          AND computed.new_status IS NOT NULL
          AND c.claim_status IS DISTINCT FROM computed.new_status
    """), {"file_id": edi_file_id})
