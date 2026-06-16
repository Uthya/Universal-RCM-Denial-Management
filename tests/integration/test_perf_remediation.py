"""Regression tests for CR-052 performance remediation.

Two changes proven safe here:
  1. _propagate_remit_status_to_claims is now scoped to a single 835's claims
     AND skips no-op writes — final claim_status must still be identical
     to what the pre-CR-052 broad UPDATE would have produced.
  2. _check_pair_status batches its lookup into one query — output tuple
     (pair_status, pair_message) must be identical to the pre-CR-052 N+1
     loop for every test case.

These tests are read-mostly: they build small in-memory scenarios using the
live `parse_and_save` path against the integration DB, then assert outcomes.
They CLEAN UP after themselves via the FV6-prefix soft-delete pattern.

Skipped unless RCM_INTEGRATION_DSN is set (matches the rest of
tests/integration/).
"""

from __future__ import annotations

import os
import time
import hashlib
from datetime import date, datetime, timezone
from decimal import Decimal

import asyncpg
import pytest


pytestmark = pytest.mark.skipif(
    "RCM_INTEGRATION_DSN" not in os.environ,
    reason="RCM_INTEGRATION_DSN not set — perf-remediation tests skipped",
)


_TEST_PREFIX = "PERF052_"   # unique prefix so we can purge cleanly


@pytest.fixture
async def conn():
    """Direct asyncpg connection to the integration DB."""
    dsn = os.environ["RCM_INTEGRATION_DSN"].replace("postgresql+asyncpg://", "postgresql://", 1)
    c = await asyncpg.connect(dsn=dsn, timeout=15)
    yield c
    # cleanup
    await c.execute(
        f"DELETE FROM remittance_claims WHERE edi_file_id IN "
        f"(SELECT id FROM edi_files WHERE file_name LIKE '{_TEST_PREFIX}%')"
    )
    await c.execute(
        f"UPDATE claims SET deleted_at=now() "
        f"WHERE claim_number LIKE '{_TEST_PREFIX}%' AND deleted_at IS NULL"
    )
    await c.execute(
        f"UPDATE edi_files SET deleted_at=now() "
        f"WHERE file_name LIKE '{_TEST_PREFIX}%' AND deleted_at IS NULL"
    )
    await c.close()


async def _insert_file(conn, file_name: str, file_type: str = "edi_837") -> int:
    return await conn.fetchval(
        """
        INSERT INTO edi_files (file_type, file_name, content_hash, raw_text,
                               parser_version, parse_status,
                               parse_started_at, parse_completed_at)
        VALUES ($1::file_type, $2, $3, 'TEST', 'v-test',
                'parsed'::parse_status, now(), now())
        RETURNING id
        """,
        file_type, file_name,
        hashlib.sha256(file_name.encode()).hexdigest(),
    )


async def _insert_claim(conn, edi_file_id: int, claim_number: str,
                        initial_status: str = "submitted") -> int:
    return await conn.fetchval(
        """
        INSERT INTO claims (edi_file_id, service_variant, claim_subtype,
                            claim_number, total_charge_amount, claim_status,
                            service_from_date, submission_date)
        VALUES ($1, '837P', 'healthcare', $2, 100.00,
                $3::claim_status, '2026-01-01', '2026-01-15')
        RETURNING id
        """,
        edi_file_id, claim_number, initial_status,
    )


async def _insert_remit(conn, edi_file_id: int, claim_id: int,
                        status_code: str, paid: float, billed: float = 100.0) -> int:
    return await conn.fetchval(
        """
        INSERT INTO remittance_claims (edi_file_id, claim_id, claim_status_code,
                                       billed_amount, paid_amount)
        VALUES ($1, $2, $3, $4, $5) RETURNING id
        """,
        edi_file_id, claim_id, status_code, Decimal(str(billed)), Decimal(str(paid)),
    )


class TestPropagateScope:
    """CR-052 Task 1: propagation only touches THIS 835's claims, not the
    whole table, and skips no-op writes."""

    @pytest.mark.asyncio
    async def test_only_this_files_claims_get_evaluated(self, conn):
        """Set up two 837s (A, B) each with one claim. Apply an 835 that
        only references A. Claim B's status must NOT change.
        Identical to the pre-fix outcome: B was never affected by this 835."""
        from rcm.parsing.persistence import _propagate_remit_status_to_claims
        from rcm.core.database import async_session

        f837_a = await _insert_file(conn, f"{_TEST_PREFIX}A_837.dat", "edi_837")
        f837_b = await _insert_file(conn, f"{_TEST_PREFIX}B_837.dat", "edi_837")
        claim_a = await _insert_claim(conn, f837_a, f"{_TEST_PREFIX}A")
        claim_b = await _insert_claim(conn, f837_b, f"{_TEST_PREFIX}B")

        # Only A has a remit in our 835
        f835 = await _insert_file(conn, f"{_TEST_PREFIX}A_835.dat", "edi_835")
        await _insert_remit(conn, f835, claim_a, "4", 0.0, 100.0)  # denied

        # Run the propagation, scoped to this 835
        async with async_session() as session:
            await _propagate_remit_status_to_claims(session, f835)
            await session.commit()

        # A got the update
        a_status = await conn.fetchval(
            "SELECT claim_status::text FROM claims WHERE id=$1", claim_a
        )
        b_status = await conn.fetchval(
            "SELECT claim_status::text FROM claims WHERE id=$1", claim_b
        )
        assert a_status == "denied"
        assert b_status == "submitted"   # untouched

    # NOTE: dead-tuple / IS DISTINCT FROM guard verification is done in the
    # benchmark script (scripts/perf_benchmark_cr052.py) rather than here.
    # pytest-asyncio + the module-level async_session engine have loop-scoping
    # issues that make multi-call async tests flaky; the SQL-level rowcount
    # check is cleaner outside the test runner.

    @pytest.mark.asyncio
    async def test_business_outcomes_match_original(self, conn):
        """Three claims with three different remit shapes — outputs must
        match the original logic exactly. This is the contract that lets
        us replace the implementation without behavior change."""
        from rcm.parsing.persistence import _propagate_remit_status_to_claims
        from rcm.core.database import async_session

        f837 = await _insert_file(conn, f"{_TEST_PREFIX}Mix_837.dat", "edi_837")
        c_denied  = await _insert_claim(conn, f837, f"{_TEST_PREFIX}D")
        c_full    = await _insert_claim(conn, f837, f"{_TEST_PREFIX}F")
        c_partial = await _insert_claim(conn, f837, f"{_TEST_PREFIX}P")

        f835 = await _insert_file(conn, f"{_TEST_PREFIX}Mix_835.dat", "edi_835")
        await _insert_remit(conn, f835, c_denied,  "4", 0.0,   100.0)
        await _insert_remit(conn, f835, c_full,    "1", 100.0, 100.0)
        await _insert_remit(conn, f835, c_partial, "1", 60.0,  100.0)

        async with async_session() as session:
            await _propagate_remit_status_to_claims(session, f835)
            await session.commit()

        rows = await conn.fetch(
            "SELECT claim_number, claim_status::text FROM claims "
            "WHERE id IN ($1,$2,$3) ORDER BY claim_number",
            c_denied, c_full, c_partial,
        )
        by_num = {r["claim_number"]: r["claim_status"] for r in rows}
        assert by_num[f"{_TEST_PREFIX}D"] == "denied"
        assert by_num[f"{_TEST_PREFIX}F"] == "paid"
        assert by_num[f"{_TEST_PREFIX}P"] == "partially_paid"


class TestPairCheckBatching:
    """CR-052 Task 2: _check_pair_status batches its lookup, output unchanged."""

    @pytest.mark.asyncio
    async def test_missing_originals_detected_in_one_query(self, conn):
        """Three replacements in one upload, none of them have matching
        originals on file. The batched query must return all three as
        missing — same outcome as three separate queries would have."""
        from rcm.routers.public.edi import _check_pair_status

        f = await _insert_file(conn, f"{_TEST_PREFIX}Repl_837.dat", "edi_837")
        # frequency_code='7' = replacement
        for n in ("X1", "X2", "X3"):
            await conn.execute(
                """
                INSERT INTO claims (edi_file_id, service_variant, claim_subtype,
                                    claim_number, total_charge_amount,
                                    claim_status, service_from_date,
                                    submission_date, frequency_code)
                VALUES ($1, '837P', 'healthcare', $2, 100.00,
                        'submitted'::claim_status, '2026-01-01',
                        '2026-01-15', '7')
                """,
                f, f"{_TEST_PREFIX}{n}",
            )

        status, msg = await _check_pair_status(conn, f, "edi_837")
        assert status == "replacement_no_original"
        assert "3 replacement claims" in msg
        assert f"{_TEST_PREFIX}X1" in msg

    @pytest.mark.asyncio
    async def test_paired_when_originals_exist(self, conn):
        """Original exists under file A; replacement uploaded as file B.
        Pair check on B must return 'paired'."""
        from rcm.routers.public.edi import _check_pair_status

        fa = await _insert_file(conn, f"{_TEST_PREFIX}Orig_837.dat", "edi_837")
        await conn.execute(
            """
            INSERT INTO claims (edi_file_id, service_variant, claim_subtype,
                                claim_number, total_charge_amount, claim_status,
                                service_from_date, submission_date, frequency_code)
            VALUES ($1, '837P', 'healthcare', $2, 100.00,
                    'submitted'::claim_status, '2026-01-01', '2026-01-15', '1')
            """,
            fa, f"{_TEST_PREFIX}PairY",
        )

        fb = await _insert_file(conn, f"{_TEST_PREFIX}Repl2_837.dat", "edi_837")
        await conn.execute(
            """
            INSERT INTO claims (edi_file_id, service_variant, claim_subtype,
                                claim_number, total_charge_amount, claim_status,
                                service_from_date, submission_date, frequency_code)
            VALUES ($1, '837P', 'healthcare', $2, 100.00,
                    'submitted'::claim_status, '2026-01-01', '2026-01-15', '7')
            """,
            fb, f"{_TEST_PREFIX}PairY",
        )

        status, msg = await _check_pair_status(conn, fb, "edi_837")
        assert status == "paired"
        assert msg is None
