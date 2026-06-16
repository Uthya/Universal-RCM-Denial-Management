"""CR-071: denial-propagation tests for `load_training_corpus()`.

Strategy A1 ("any denial wins"): a freq=1 original inherits `denied=1` from
any freq=7 sibling claim whose remittance has CLP02='4', matched by
`(claim_number, payer_id)`. Payer isolation prevents cross-payer leakage.

Three tests:
  1. Propagation fires when a denied freq=7 sibling exists.
  2. No propagation when there is no replacement at all.
  3. Payer isolation — a denied freq=7 under payer B does not flip a
     freq=1 paid original under payer A even when they share claim_number.

Each test seeds rows under a unique `cr071-prop-` prefix on `edi_files.file_name`
and cleans up at the end via `_cleanup_test_files(prefix)`. mv_claim_labels
is refreshed inside the test so the seeded freq=1 rows enter the MV.

Skipped unless `RCM_INTEGRATION_DSN` is set.
"""
from __future__ import annotations

import os
import uuid as _uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from rcm.features.dataset import load_training_corpus
from rcm.models.ingestion import EdiFile

pytestmark = pytest.mark.skipif(
    "RCM_INTEGRATION_DSN" not in os.environ,
    reason="RCM_INTEGRATION_DSN not set — integration tests skipped",
)


@pytest_asyncio.fixture
async def engine_session():
    engine = create_async_engine(
        os.environ["RCM_INTEGRATION_DSN"],
        pool_pre_ping=True, pool_recycle=60,
    )
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield engine, Session
    await engine.dispose()


async def _refresh_mv(session: AsyncSession) -> None:
    await session.execute(text("REFRESH MATERIALIZED VIEW mv_claim_labels"))
    await session.commit()


async def _cleanup(session: AsyncSession, prefix: str) -> None:
    files = (await session.execute(
        select(EdiFile).where(EdiFile.file_name.like(f"{prefix}%"))
    )).scalars().all()
    for f in files:
        await session.delete(f)   # cascades to claims, remits, etc.
    await session.commit()
    await _refresh_mv(session)


async def _insert_edi_file(session: AsyncSession, file_name: str) -> int:
    sha = "cr071-" + _uuid.uuid4().hex
    row = await session.execute(text("""
        INSERT INTO edi_files (
            file_type, sender_id, receiver_id, interchange_control_no,
            functional_group_control_no, file_name, content_hash, raw_text,
            parser_version, parse_status
        ) VALUES (
            'edi_837', 'TST', 'TST2', '000000001',
            '000000001', :name, :sha, 'cr071-stub',
            'cr071-test', 'parsed'
        ) RETURNING id
    """), {"name": file_name, "sha": sha})
    return int(row.scalar_one())


async def _insert_payer(session: AsyncSession, canonical: str) -> int:
    """Use the existing pg_insert ON CONFLICT path the parser uses."""
    row = await session.execute(text("""
        INSERT INTO payers (canonical_name)
        VALUES (:n)
        ON CONFLICT (canonical_name) DO UPDATE SET canonical_name=EXCLUDED.canonical_name
        RETURNING id
    """), {"n": canonical})
    return int(row.scalar_one())


async def _insert_claim(
    session: AsyncSession, *, edi_file_id: int, claim_number: str,
    frequency_code: str | None, payer_id: int | None,
    service_variant: str = "837P", claim_subtype: str = "healthcare",
) -> int:
    row = await session.execute(text("""
        INSERT INTO claims (
            edi_file_id, service_variant, claim_subtype, claim_number,
            payer_id, frequency_code, claim_status, service_from_date,
            submission_date, total_charge_amount
        ) VALUES (
            :edi_file_id, :sv, :st, :cn, :payer_id, :fc,
            'submitted', :sfd, :sd, 100.00
        ) RETURNING id
    """), {
        "edi_file_id": edi_file_id, "sv": service_variant, "st": claim_subtype,
        "cn": claim_number, "payer_id": payer_id, "fc": frequency_code,
        "sfd": date(2026, 1, 1), "sd": date(2026, 1, 2),
    })
    return int(row.scalar_one())


async def _insert_remit(
    session: AsyncSession, *, claim_id: int, edi_file_id: int,
    clp02: str, billed: float = 100.00, paid: float = 0.00,
) -> int:
    row = await session.execute(text("""
        INSERT INTO remittance_claims (
            claim_id, edi_file_id, claim_status_code,
            billed_amount, paid_amount
        ) VALUES (
            :cid, :efid, :clp, :b, :p
        ) RETURNING id
    """), {"cid": claim_id, "efid": edi_file_id, "clp": clp02,
           "b": billed, "p": paid})
    return int(row.scalar_one())


class TestCR071Propagation:
    @pytest.mark.asyncio
    async def test_propagates_denial_from_replacement(self, engine_session):
        _, Session = engine_session
        prefix = f"cr071-prop-{_uuid.uuid4().hex[:6]}-"
        cn = f"{prefix}CN"
        async with Session() as s:
            await _cleanup(s, prefix)

            payer_id = await _insert_payer(s, f"{prefix}PAYER")
            ef = await _insert_edi_file(s, f"{prefix}orig.dat")
            ef7 = await _insert_edi_file(s, f"{prefix}repl.dat")

            # freq=1 original with a PAID remit → enters mv with denied=0
            orig_id = await _insert_claim(
                s, edi_file_id=ef, claim_number=cn, frequency_code="1",
                payer_id=payer_id,
            )
            await _insert_remit(s, claim_id=orig_id, edi_file_id=ef,
                                clp02="1", paid=100.00)

            # freq=7 replacement with a DENIED remit (not in mv directly)
            repl_id = await _insert_claim(
                s, edi_file_id=ef7, claim_number=cn, frequency_code="7",
                payer_id=payer_id,
            )
            await _insert_remit(s, claim_id=repl_id, edi_file_id=ef7,
                                clp02="4", paid=0.00)

            await s.commit()
            await _refresh_mv(s)

            df = await load_training_corpus(s, service_variant="837P",
                                            claim_subtype="healthcare")
        try:
            mask = df["claim_number"] == cn
            assert mask.sum() == 1, f"Expected 1 row for {cn}, got {mask.sum()}"
            assert int(df.loc[mask, "denied"].iloc[0]) == 1, (
                "Expected denied=1 (propagated from freq=7 sibling), got 0"
            )
        finally:
            async with Session() as s:
                await _cleanup(s, prefix)

    @pytest.mark.asyncio
    async def test_no_propagation_without_replacement(self, engine_session):
        _, Session = engine_session
        prefix = f"cr071-noprop-{_uuid.uuid4().hex[:6]}-"
        cn = f"{prefix}CN"
        async with Session() as s:
            await _cleanup(s, prefix)
            payer_id = await _insert_payer(s, f"{prefix}PAYER")
            ef = await _insert_edi_file(s, f"{prefix}orig.dat")

            orig_id = await _insert_claim(
                s, edi_file_id=ef, claim_number=cn, frequency_code="1",
                payer_id=payer_id,
            )
            await _insert_remit(s, claim_id=orig_id, edi_file_id=ef,
                                clp02="1", paid=100.00)
            await s.commit()
            await _refresh_mv(s)

            df = await load_training_corpus(s, service_variant="837P",
                                            claim_subtype="healthcare")
        try:
            mask = df["claim_number"] == cn
            assert mask.sum() == 1
            assert int(df.loc[mask, "denied"].iloc[0]) == 0, (
                "Expected denied=0 (no descendant exists), got 1"
            )
        finally:
            async with Session() as s:
                await _cleanup(s, prefix)

    @pytest.mark.asyncio
    async def test_payer_isolation(self, engine_session):
        """Two freq=1 originals share claim_number but differ in payer.
        A denied freq=7 under payer B must NOT flip the freq=1 under payer A."""
        _, Session = engine_session
        prefix = f"cr071-payer-{_uuid.uuid4().hex[:6]}-"
        cn = f"{prefix}CN"
        async with Session() as s:
            await _cleanup(s, prefix)
            payer_a = await _insert_payer(s, f"{prefix}PAYER_A")
            payer_b = await _insert_payer(s, f"{prefix}PAYER_B")
            ef_a   = await _insert_edi_file(s, f"{prefix}orig_a.dat")
            ef_b   = await _insert_edi_file(s, f"{prefix}orig_b.dat")
            ef_b7  = await _insert_edi_file(s, f"{prefix}repl_b.dat")

            # Original payer A — paid
            orig_a = await _insert_claim(
                s, edi_file_id=ef_a, claim_number=cn, frequency_code="1",
                payer_id=payer_a,
            )
            await _insert_remit(s, claim_id=orig_a, edi_file_id=ef_a,
                                clp02="1", paid=100.00)

            # Original payer B — paid
            orig_b = await _insert_claim(
                s, edi_file_id=ef_b, claim_number=cn, frequency_code="1",
                payer_id=payer_b,
            )
            await _insert_remit(s, claim_id=orig_b, edi_file_id=ef_b,
                                clp02="1", paid=100.00)

            # Replacement payer B — denied
            repl_b = await _insert_claim(
                s, edi_file_id=ef_b7, claim_number=cn, frequency_code="7",
                payer_id=payer_b,
            )
            await _insert_remit(s, claim_id=repl_b, edi_file_id=ef_b7,
                                clp02="4", paid=0.00)

            await s.commit()
            await _refresh_mv(s)

            df = await load_training_corpus(s, service_variant="837P",
                                            claim_subtype="healthcare")
        try:
            # Pull just the two rows for this test's claim_number, identify by
            # payer_canonical_name from the loader's projection.
            mask = df["claim_number"] == cn
            assert mask.sum() == 2, f"Expected 2 rows for {cn}, got {mask.sum()}"
            sub = df.loc[mask].set_index("payer_canonical_name")
            a_label = int(sub.loc[f"{prefix}PAYER_A", "denied"])
            b_label = int(sub.loc[f"{prefix}PAYER_B", "denied"])
            assert a_label == 0, (
                f"Payer A should NOT inherit denial (cross-payer leakage), got {a_label}"
            )
            assert b_label == 1, (
                f"Payer B should inherit denial from its own freq=7 sibling, got {b_label}"
            )
        finally:
            async with Session() as s:
                await _cleanup(s, prefix)
