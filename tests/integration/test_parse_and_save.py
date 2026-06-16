"""End-to-end integration test against the remote dev DB.

Skipped unless ``RCM_INTEGRATION_DSN`` env var is set, e.g.:
    RCM_INTEGRATION_DSN='postgresql+asyncpg://postgres:...@host:5432/rcm_denials' pytest
The remote dev DSN is the same as the one in `.env.remote`; CI can set this
inline. We never read passwords from .env in tests.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from rcm.models.claims import Claim, ClaimLine, Diagnosis, Patient, Provider
from rcm.models.ingestion import EdiFile, ParseEvent, RawSegment
from rcm.models.reference import Payer
from rcm.models.remittance import Adjustment, RemarkCode, RemittanceClaim
from rcm.parsing import parse_and_save
from rcm.parsing.persistence import DuplicateFileError


pytestmark = pytest.mark.skipif(
    "RCM_INTEGRATION_DSN" not in os.environ,
    reason="RCM_INTEGRATION_DSN not set — integration tests skipped",
)


FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures"


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(
        os.environ["RCM_INTEGRATION_DSN"],
        pool_pre_ping=True,
        pool_recycle=60,
    )
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as s:
        yield s
    await engine.dispose()


async def _cleanup_test_files(session: AsyncSession, name_prefix: str) -> None:
    """Hard-delete any EdiFile rows created by previous test runs."""
    rows = (await session.execute(
        select(EdiFile).where(EdiFile.file_name.like(f"{name_prefix}%"))
    )).scalars().all()
    for r in rows:
        await session.delete(r)
    await session.commit()


class TestParseAndSave837P:
    @pytest.mark.asyncio
    async def test_healthy_837p_persists(self, session: AsyncSession):
        await _cleanup_test_files(session, "int-837p-healthy")
        raw = (FIXTURE_DIR / "837p_healthcare_healthy.edi").read_bytes()
        ef = await parse_and_save(session, raw, "int-837p-healthy.edi")

        assert ef.id is not None
        assert ef.service_variant_detected == "837P"
        assert ef.claim_subtype_detected == "healthcare"
        assert ef.parse_summary["claims_saved"] == 1
        assert ef.parse_summary["claims_dropped"] == 0

        claim = (await session.execute(
            select(Claim).where(Claim.edi_file_id == ef.id)
        )).scalar_one()
        assert claim.claim_number == "HC-001"
        assert claim.billing_provider_id is not None

        # Confirm raw_segments persisted (partitioned table)
        n_raw = (await session.execute(
            select(RawSegment).where(RawSegment.edi_file_id == ef.id)
        )).scalars().all()
        assert len(n_raw) >= 15

        # Confirm parse_events table is queryable (may be empty when the file
        # contains no skipped/error segments — segment_handled events are
        # intentionally not emitted per PARSER-STRESS-001 optimization).
        events = (await session.execute(
            select(ParseEvent).where(ParseEvent.edi_file_id == ef.id)
        )).scalars().all()
        assert isinstance(events, list)

        # Cleanup
        await session.delete(ef)
        await session.commit()

    @pytest.mark.asyncio
    async def test_dedup_via_content_hash(self, session: AsyncSession):
        await _cleanup_test_files(session, "int-837p-dedup")
        raw = (FIXTURE_DIR / "837p_healthcare_healthy.edi").read_bytes()
        ef = await parse_and_save(session, raw, "int-837p-dedup.edi")

        try:
            with pytest.raises(DuplicateFileError):
                await parse_and_save(session, raw, "int-837p-dedup-2.edi")
        finally:
            await session.rollback()
            ef_again = (await session.execute(
                select(EdiFile).where(EdiFile.file_name == "int-837p-dedup.edi")
            )).scalar_one()
            await session.delete(ef_again)
            await session.commit()


class TestConcurrentMasterDataUpserts:
    """CR-069: 8-wide concurrent uploads sharing the same master-data
    (payers / patients / providers) must all succeed.

    Pre-CR-069 this test fails because `_bulk_upsert_*` uses a racy
    check-then-insert pattern: multiple workers see "row missing", all
    INSERT, all but one fail with UniqueViolation on the existing
    `payers.canonical_name`, `patients.member_id`, or `providers.npi`
    UNIQUE constraints.

    Post-CR-069 every worker uses INSERT...ON CONFLICT DO NOTHING followed
    by a SELECT, so all workers succeed and return the same id for the
    shared canonical key.
    """

    @pytest.mark.asyncio
    async def test_eight_wide_concurrent_uploads(self):
        engine = create_async_engine(
            os.environ["RCM_INTEGRATION_DSN"],
            pool_pre_ping=True, pool_recycle=60, pool_size=16, max_overflow=4,
        )
        Session = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False,
        )

        # 8 payloads that share master-data but have unique content_hash.
        # Trailing whitespace is stripped by the segment parser but changes
        # the SHA-256, so each upload bypasses the content_hash dedup.
        base = (FIXTURE_DIR / "837d_dental_simple.edi").read_bytes()
        payloads = [
            (f"int-cr069-race-{i:02d}.edi", base + (b"\n" * (i + 1)))
            for i in range(8)
        ]

        async with Session() as s:
            await _cleanup_test_files(s, "int-cr069-race-")

        async def _upload(name: str, raw: bytes):
            try:
                async with Session() as s:
                    ef = await parse_and_save(s, raw, name)
                    await s.commit()
                    return ("ok", int(ef.id), None)
            except Exception as e:
                return ("err", None, f"{type(e).__name__}: {str(e)[:200]}")

        try:
            results = await asyncio.gather(
                *(_upload(name, raw) for name, raw in payloads)
            )

            errors = [r for r in results if r[0] != "ok"]
            assert not errors, (
                f"Concurrent uploads produced {len(errors)} error(s). "
                f"Pre-CR-069 this was the expected race; post-CR-069 it must "
                f"be zero. First failure: {errors[0] if errors else None}"
            )

            edi_ids = [r[1] for r in results]
            assert len(set(edi_ids)) == 8, "8 distinct edi_files expected"

            # Verify master-data is shared (not duplicated). For the dental
            # fixture, every claim points at the same billing-provider NPI;
            # post-CR-069 there is exactly ONE Provider row for it.
            async with Session() as s:
                provider_ids = (await s.execute(
                    select(Claim.billing_provider_id)
                    .where(Claim.edi_file_id.in_(edi_ids))
                    .where(Claim.billing_provider_id.isnot(None))
                )).scalars().all()
                assert len(set(provider_ids)) == 1, (
                    f"Expected all 8 uploads to share one billing provider; "
                    f"found {len(set(provider_ids))} distinct provider_ids"
                )
                # And there is exactly one row in providers for that NPI
                shared_pid = next(iter(set(provider_ids)))
                npi = (await s.execute(
                    select(Provider.npi).where(Provider.id == shared_pid)
                )).scalar_one()
                count = (await s.execute(
                    select(func.count(Provider.id)).where(Provider.npi == npi)
                )).scalar_one()
                assert count == 1, f"Provider NPI {npi} duplicated: {count} rows"
        finally:
            async with Session() as s:
                await _cleanup_test_files(s, "int-cr069-race-")
            await engine.dispose()


class TestParseAndSave837INo:
    @pytest.mark.asyncio
    async def test_no_dtp472_marks_claim_dropped(self, session: AsyncSession):
        await _cleanup_test_files(session, "int-837p-no-dtp")
        raw = (FIXTURE_DIR / "837p_healthcare_no_dtp472.edi").read_bytes()
        ef = await parse_and_save(session, raw, "int-837p-no-dtp.edi")
        assert ef.parse_summary["claims_dropped"] == 1
        assert ef.parse_summary["claims_saved"] == 0
        # Dropped claim still has raw_segments (handler_status='validator_dropped')
        rs = (await session.execute(
            select(RawSegment).where(RawSegment.edi_file_id == ef.id)
        )).scalars().all()
        assert len(rs) >= 5
        await session.delete(ef)
        await session.commit()


class TestParseAndSave835:
    @pytest.mark.asyncio
    async def test_remittance_links_to_existing_claim(self, session: AsyncSession):
        await _cleanup_test_files(session, "int-")

        # First load the 837P that creates HC-001
        raw_837 = (FIXTURE_DIR / "837p_healthcare_healthy.edi").read_bytes()
        ef837 = await parse_and_save(session, raw_837, "int-link-837.edi")

        # Now load an 835 that references HC-001
        raw_835 = (FIXTURE_DIR / "835_healthy_full.edi").read_bytes()
        ef835 = await parse_and_save(session, raw_835, "int-link-835.edi")
        assert ef835.file_type == "edi_835"
        assert ef835.parse_summary["remittances_seen"] == 2

        # HC-001 remittance should be linked
        rc = (await session.execute(
            select(RemittanceClaim).join(Claim, RemittanceClaim.claim_id == Claim.id)
            .where(Claim.claim_number == "HC-001")
        )).scalars().all()
        assert len(rc) == 1
        assert rc[0].claim_status_code == "1"

        # HC-NO-DTP doesn't exist as a claim — that remittance is skipped (orphan)
        rc2 = (await session.execute(
            select(RemittanceClaim).join(Claim, RemittanceClaim.claim_id == Claim.id)
            .where(Claim.claim_number == "HC-NO-DTP")
        )).scalars().all()
        assert rc2 == []

        # Cleanup
        await session.delete(ef835)
        await session.delete(ef837)
        await session.commit()


class TestParseAndSave837I:
    @pytest.mark.asyncio
    async def test_home_care_episode_persists(self, session: AsyncSession):
        await _cleanup_test_files(session, "int-837i-hha")
        raw = (FIXTURE_DIR / "837i_home_care.edi").read_bytes()
        ef = await parse_and_save(session, raw, "int-837i-hha.edi")
        from rcm.models.variant_extensions import HomeCareEpisode
        ep = (await session.execute(
            select(HomeCareEpisode).join(Claim, HomeCareEpisode.claim_id == Claim.id)
            .where(Claim.edi_file_id == ef.id)
        )).scalar_one()
        assert ep.homebound_certified is True
        await session.delete(ef)
        await session.commit()


class TestParseAndSave837D:
    @pytest.mark.asyncio
    async def test_dental_tooth_persists(self, session: AsyncSession):
        await _cleanup_test_files(session, "int-837d-dds")
        raw = (FIXTURE_DIR / "837d_dental_simple.edi").read_bytes()
        ef = await parse_and_save(session, raw, "int-837d-dds.edi")
        line = (await session.execute(
            select(ClaimLine).join(Claim, ClaimLine.claim_id == Claim.id)
            .where(Claim.edi_file_id == ef.id)
        )).scalar_one()
        assert line.tooth_number == "14"
        assert line.tooth_surfaces == "MOD"
        await session.delete(ef)
        await session.commit()
