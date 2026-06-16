"""CR-072: verify mv_claim_labels admits freq=1 originals with no own remit
when a freq=7 descendant carries terminal CLP02.

Semantics tested (in priority order):
  1. own CLP02='4'    → denied=1
  2. own CLP02 paid   → denied=0
  3. descendant denied → denied=1
  4. descendant paid   → denied=0
  5. no signal anywhere → row not in MV

Each test seeds rows under a unique `cr072-mv-` prefix on
`edi_files.file_name`, refreshes `mv_claim_labels`, asserts the expected MV
state, then cleans up via filename-prefix delete (cascades through claims
and remits).

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


async def _refresh_mv(s: AsyncSession) -> None:
    await s.execute(text("REFRESH MATERIALIZED VIEW mv_claim_labels"))
    await s.commit()


async def _cleanup(s: AsyncSession, prefix: str) -> None:
    files = (await s.execute(
        select(EdiFile).where(EdiFile.file_name.like(f"{prefix}%"))
    )).scalars().all()
    for f in files:
        await s.delete(f)
    await s.commit()
    await _refresh_mv(s)


async def _new_edi_file(s: AsyncSession, name: str) -> int:
    row = await s.execute(text("""
        INSERT INTO edi_files (
            file_type, sender_id, receiver_id, interchange_control_no,
            functional_group_control_no, file_name, content_hash, raw_text,
            parser_version, parse_status
        ) VALUES (
            'edi_837', 'TST', 'TST2', '000000001', '000000001',
            :name, :sha, 'cr072-stub', 'cr072-test', 'parsed'
        ) RETURNING id
    """), {"name": name, "sha": "cr072-" + _uuid.uuid4().hex})
    return int(row.scalar_one())


async def _new_payer(s: AsyncSession, name: str) -> int:
    row = await s.execute(text("""
        INSERT INTO payers (canonical_name) VALUES (:n)
        ON CONFLICT (canonical_name) DO UPDATE SET canonical_name=EXCLUDED.canonical_name
        RETURNING id
    """), {"n": name})
    return int(row.scalar_one())


async def _new_claim(s, *, edi_file_id, claim_number, frequency_code,
                    payer_id, variant="837D", subtype="dental"):
    row = await s.execute(text("""
        INSERT INTO claims (
            edi_file_id, service_variant, claim_subtype, claim_number,
            payer_id, frequency_code, claim_status, service_from_date,
            submission_date, total_charge_amount
        ) VALUES (
            :ef, :sv, :st, :cn, :pid, :fc,
            'submitted', :sfd, :sd, 100.00
        ) RETURNING id
    """), {"ef": edi_file_id, "sv": variant, "st": subtype, "cn": claim_number,
           "pid": payer_id, "fc": frequency_code,
           "sfd": date(2026, 1, 1), "sd": date(2026, 1, 2)})
    return int(row.scalar_one())


async def _new_remit(s, *, claim_id, edi_file_id, clp02):
    row = await s.execute(text("""
        INSERT INTO remittance_claims (
            claim_id, edi_file_id, claim_status_code,
            billed_amount, paid_amount
        ) VALUES (:cid, :ef, :clp, 100.00, 0.00)
        RETURNING id
    """), {"cid": claim_id, "ef": edi_file_id, "clp": clp02})
    return int(row.scalar_one())


async def _mv_row_for(s: AsyncSession, claim_id: int):
    return (await s.execute(text(
        "SELECT denied FROM mv_claim_labels WHERE claim_id = :cid"
    ), {"cid": claim_id})).scalar_one_or_none()


class TestCR072MVPropagation:
    @pytest.mark.asyncio
    async def test_own_denied_wins_over_descendant_paid(self, engine_session):
        """Priority 1: own CLP02='4' takes precedence."""
        _, Session = engine_session
        prefix = f"cr072-mv-A-{_uuid.uuid4().hex[:6]}-"
        cn = f"{prefix}CN"
        async with Session() as s:
            await _cleanup(s, prefix)
            payer = await _new_payer(s, f"{prefix}PAYER")
            ef1 = await _new_edi_file(s, f"{prefix}orig.dat")
            ef7 = await _new_edi_file(s, f"{prefix}repl.dat")
            orig = await _new_claim(s, edi_file_id=ef1, claim_number=cn,
                                    frequency_code="1", payer_id=payer)
            await _new_remit(s, claim_id=orig, edi_file_id=ef1, clp02="4")  # own denied
            repl = await _new_claim(s, edi_file_id=ef7, claim_number=cn,
                                    frequency_code="7", payer_id=payer)
            await _new_remit(s, claim_id=repl, edi_file_id=ef7, clp02="1")  # desc paid
            await s.commit()
            await _refresh_mv(s)
            denied = await _mv_row_for(s, orig)
        try:
            assert denied == 1, f"Expected own denial to win, got denied={denied}"
        finally:
            async with Session() as s:
                await _cleanup(s, prefix)

    @pytest.mark.asyncio
    async def test_own_paid_wins_over_descendant_denied(self, engine_session):
        """Priority 2: own paid blocks descendant denial.

        CR-071 CTE in load_training_corpus() still flips this case for FB
        training, but the MV itself returns own-paid here. This test
        confirms the MV's own-status precedence."""
        _, Session = engine_session
        prefix = f"cr072-mv-B-{_uuid.uuid4().hex[:6]}-"
        cn = f"{prefix}CN"
        async with Session() as s:
            await _cleanup(s, prefix)
            payer = await _new_payer(s, f"{prefix}PAYER")
            ef1 = await _new_edi_file(s, f"{prefix}orig.dat")
            ef7 = await _new_edi_file(s, f"{prefix}repl.dat")
            orig = await _new_claim(s, edi_file_id=ef1, claim_number=cn,
                                    frequency_code="1", payer_id=payer)
            await _new_remit(s, claim_id=orig, edi_file_id=ef1, clp02="1")
            repl = await _new_claim(s, edi_file_id=ef7, claim_number=cn,
                                    frequency_code="7", payer_id=payer)
            await _new_remit(s, claim_id=repl, edi_file_id=ef7, clp02="4")
            await s.commit()
            await _refresh_mv(s)
            denied = await _mv_row_for(s, orig)
        try:
            assert denied == 0, f"MV should return own paid, got denied={denied}"
        finally:
            async with Session() as s:
                await _cleanup(s, prefix)

    @pytest.mark.asyncio
    async def test_descendant_denied_when_no_own_remit(self, engine_session):
        """Priority 3: with no own remit, propagated descendant denied wins."""
        _, Session = engine_session
        prefix = f"cr072-mv-C-{_uuid.uuid4().hex[:6]}-"
        cn = f"{prefix}CN"
        async with Session() as s:
            await _cleanup(s, prefix)
            payer = await _new_payer(s, f"{prefix}PAYER")
            ef1 = await _new_edi_file(s, f"{prefix}orig.dat")
            ef7 = await _new_edi_file(s, f"{prefix}repl.dat")
            orig = await _new_claim(s, edi_file_id=ef1, claim_number=cn,
                                    frequency_code="1", payer_id=payer)
            # No remit on the original
            repl = await _new_claim(s, edi_file_id=ef7, claim_number=cn,
                                    frequency_code="7", payer_id=payer)
            await _new_remit(s, claim_id=repl, edi_file_id=ef7, clp02="4")
            await s.commit()
            await _refresh_mv(s)
            denied = await _mv_row_for(s, orig)
        try:
            assert denied == 1, (
                "Original with no own remit should inherit descendant denied, "
                f"got denied={denied}"
            )
        finally:
            async with Session() as s:
                await _cleanup(s, prefix)

    @pytest.mark.asyncio
    async def test_descendant_paid_when_no_own_remit(self, engine_session):
        """Priority 4: with no own remit and only paid descendants, denied=0."""
        _, Session = engine_session
        prefix = f"cr072-mv-D-{_uuid.uuid4().hex[:6]}-"
        cn = f"{prefix}CN"
        async with Session() as s:
            await _cleanup(s, prefix)
            payer = await _new_payer(s, f"{prefix}PAYER")
            ef1 = await _new_edi_file(s, f"{prefix}orig.dat")
            ef7 = await _new_edi_file(s, f"{prefix}repl.dat")
            orig = await _new_claim(s, edi_file_id=ef1, claim_number=cn,
                                    frequency_code="1", payer_id=payer)
            repl = await _new_claim(s, edi_file_id=ef7, claim_number=cn,
                                    frequency_code="7", payer_id=payer)
            await _new_remit(s, claim_id=repl, edi_file_id=ef7, clp02="1")
            await s.commit()
            await _refresh_mv(s)
            denied = await _mv_row_for(s, orig)
        try:
            assert denied == 0, (
                "Original with only paid descendant should be denied=0, "
                f"got denied={denied}"
            )
        finally:
            async with Session() as s:
                await _cleanup(s, prefix)

    @pytest.mark.asyncio
    async def test_no_signal_excluded_from_mv(self, engine_session):
        """Priority 5: no remit anywhere → row absent from MV (NULL excluded
        by the HAVING clause)."""
        _, Session = engine_session
        prefix = f"cr072-mv-E-{_uuid.uuid4().hex[:6]}-"
        cn = f"{prefix}CN"
        async with Session() as s:
            await _cleanup(s, prefix)
            payer = await _new_payer(s, f"{prefix}PAYER")
            ef1 = await _new_edi_file(s, f"{prefix}orig.dat")
            orig = await _new_claim(s, edi_file_id=ef1, claim_number=cn,
                                    frequency_code="1", payer_id=payer)
            # No remit, no replacement at all
            await s.commit()
            await _refresh_mv(s)
            denied = await _mv_row_for(s, orig)
        try:
            assert denied is None, (
                f"Claim with no signal should be absent from MV, got denied={denied}"
            )
        finally:
            async with Session() as s:
                await _cleanup(s, prefix)

    @pytest.mark.asyncio
    async def test_payer_isolation_in_mv(self, engine_session):
        """Cross-payer: D0001 @ payer A (no remit) + D0001 @ payer B
        replacement denied → only payer-B's original inherits the denied
        label; payer-A's original stays absent (it has no own remit and no
        same-payer descendant)."""
        _, Session = engine_session
        prefix = f"cr072-mv-F-{_uuid.uuid4().hex[:6]}-"
        cn = f"{prefix}CN"
        async with Session() as s:
            await _cleanup(s, prefix)
            pa = await _new_payer(s, f"{prefix}PAYER_A")
            pb = await _new_payer(s, f"{prefix}PAYER_B")
            ef_a = await _new_edi_file(s, f"{prefix}origA.dat")
            ef_b = await _new_edi_file(s, f"{prefix}origB.dat")
            ef_b7 = await _new_edi_file(s, f"{prefix}replB.dat")
            orig_a = await _new_claim(s, edi_file_id=ef_a, claim_number=cn,
                                      frequency_code="1", payer_id=pa)
            orig_b = await _new_claim(s, edi_file_id=ef_b, claim_number=cn,
                                      frequency_code="1", payer_id=pb)
            repl_b = await _new_claim(s, edi_file_id=ef_b7, claim_number=cn,
                                      frequency_code="7", payer_id=pb)
            await _new_remit(s, claim_id=repl_b, edi_file_id=ef_b7, clp02="4")
            await s.commit()
            await _refresh_mv(s)
            a_denied = await _mv_row_for(s, orig_a)
            b_denied = await _mv_row_for(s, orig_b)
        try:
            assert a_denied is None, (
                f"Payer-A's original should NOT inherit (cross-payer), got {a_denied}"
            )
            assert b_denied == 1, (
                f"Payer-B's original should inherit descendant denial, got {b_denied}"
            )
        finally:
            async with Session() as s:
                await _cleanup(s, prefix)
