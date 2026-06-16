"""CR-069 pre-implementation verification.

Establishes the BASELINE behavior of master-data upserts under:
  A. Fresh empty DB + serial upload          (reference)
  B. Fresh empty DB + 8-wide concurrent     (the race we expect to see)
  C. Warm DB + serial upload                (reference)
  D. Warm DB + 8-wide concurrent            (the race we expect NOT to see)

Pre-CR-069 hypothesis:
  B should produce IntegrityErrors and missing rows vs. A.
  D should produce identical counts to C (no race after warm-up).

Post-CR-069 expected behavior (re-run after implementation):
  B should match A. D should match C. Both should report 0 errors.

This script is TRANSIENT — creates rcm_cr069_test DB on the local
Docker PG, runs the four scenarios, drops the DB, exits. Does not
touch rcm_denials_dev.

Usage:
  PYTHONPATH=src python scripts/cr069_pre_verification.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Critical: override DATABASE_URL BEFORE importing any rcm module so that
# settings picks up the test DB. We create our own engine for actual work
# but some downstream modules (e.g., validators) may read settings.
TEST_DB = "rcm_cr069_test"
PG_HOST = "localhost"
PG_PORT = "5433"
PG_USER = "rcm"
PG_PASSWORD = "rcm_dev_password"
TEST_DSN_ASYNC = f"postgresql+asyncpg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{TEST_DB}"
TEST_DSN_RAW   = f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{TEST_DB}"
ADMIN_DSN_RAW  = f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/postgres"

os.environ["DATABASE_URL"] = TEST_DSN_ASYNC

# Path setup
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

import asyncpg                                                      # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker                # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine               # noqa: E402

from rcm.parsing.parser import parse_and_save                        # noqa: E402

STAGING = Path(r"C:\Users\Gowdham B\AppData\Local\Temp\claims_staging\claim_pairs_1000_low_risk_PID")

# Test files: 8 files for the MEASURED upload (T_FILES).
# Warmup files: 8 different files to populate master-data first (W_FILES).
# All from LR1K_D so they share the same payer / provider master-data.
T_FILES = [
    STAGING / f"LR1K_D_pair_{i:02d}" / f"LR1K_D_pair_{i:02d}_original_837.dat"
    for i in (1, 2, 3, 4, 5, 6, 7, 8)
]
W_FILES = [
    STAGING / f"LR1K_D_pair_{i:02d}" / f"LR1K_D_pair_{i:02d}_original_837.dat"
    for i in (9, 10, 11, 12, 13, 14, 15, 16)
]


# ---------------------------------------------------------------------------
# Test-DB management
# ---------------------------------------------------------------------------

async def admin_exec(sql: str) -> None:
    c = await asyncpg.connect(dsn=ADMIN_DSN_RAW)
    try:
        await c.execute(sql)
    finally:
        await c.close()


async def drop_test_db() -> None:
    # Terminate any lingering connections so DROP DATABASE doesn't block
    c = await asyncpg.connect(dsn=ADMIN_DSN_RAW)
    try:
        await c.execute(f"""
            SELECT pg_terminate_backend(pid) FROM pg_stat_activity
             WHERE datname='{TEST_DB}' AND pid <> pg_backend_pid()
        """)
        await c.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
    finally:
        await c.close()


async def create_test_db_with_schema() -> None:
    """Create the test DB and copy schema from rcm_denials_dev via pg_dump."""
    await drop_test_db()
    await admin_exec(f"CREATE DATABASE {TEST_DB}")

    # Copy schema (no data) from rcm_denials_dev to test DB via docker exec
    cmd = [
        "docker", "exec", "rcm-postgres", "bash", "-c",
        f"pg_dump -U {PG_USER} --schema-only --no-owner --no-acl rcm_denials_dev "
        f"| psql -U {PG_USER} -d {TEST_DB} -v ON_ERROR_STOP=0 -q"
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    # ON_ERROR_STOP=0 because pg_dump sometimes emits ALTER OWNER lines that
    # fail under the rcm role — those are harmless. Schema copy still succeeds.
    if r.returncode not in (0, 3):
        print("pg_dump|psql stderr:", r.stderr[-1500:])
        raise RuntimeError(f"Schema copy failed (returncode={r.returncode})")


async def reset_data() -> None:
    """TRUNCATE all data tables; preserve schema."""
    c = await asyncpg.connect(dsn=TEST_DSN_RAW)
    try:
        # Discover all base tables in public, then TRUNCATE en masse.
        rows = await c.fetch("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema='public' AND table_type='BASE TABLE'
              AND table_name NOT LIKE 'alembic_%'
              AND table_name NOT LIKE '%_p202%'  -- partition children
              AND table_name NOT LIKE '%_default'
        """)
        names = ", ".join(f'"{r["table_name"]}"' for r in rows)
        if names:
            await c.execute(f"TRUNCATE {names} RESTART IDENTITY CASCADE")
    finally:
        await c.close()


async def measure_counts() -> dict[str, int]:
    c = await asyncpg.connect(dsn=TEST_DSN_RAW)
    try:
        return {
            "edi_files":         await c.fetchval("SELECT count(*) FROM edi_files"),
            "claims":            await c.fetchval("SELECT count(*) FROM claims"),
            "claim_lines":       await c.fetchval("SELECT count(*) FROM claim_lines"),
            "diagnoses":         await c.fetchval("SELECT count(*) FROM diagnoses"),
            "payers":            await c.fetchval("SELECT count(*) FROM payers"),
            "patients":          await c.fetchval("SELECT count(*) FROM patients"),
            "providers":         await c.fetchval("SELECT count(*) FROM providers"),
            "subscribers":       await c.fetchval("SELECT count(*) FROM subscribers"),
        }
    finally:
        await c.close()


# ---------------------------------------------------------------------------
# Upload via direct parse_and_save (bypasses HTTP)
# ---------------------------------------------------------------------------

async def upload_one(session_factory, path: Path) -> dict:
    raw = path.read_bytes()
    try:
        async with session_factory() as session:
            edi_file = await parse_and_save(session, raw, file_name=path.name)
            await session.commit()
            return {"file": path.name, "status": "ok",
                    "edi_file_id": int(edi_file.id), "error": None}
    except Exception as e:
        return {"file": path.name, "status": "err",
                "edi_file_id": None,
                "error": f"{type(e).__name__}: {str(e)[:240]}"}


async def run_batch(files: list[Path], *, concurrent: bool) -> tuple[list[dict], float]:
    engine = create_async_engine(TEST_DSN_ASYNC, echo=False)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    t0 = time.monotonic()
    if concurrent:
        sem = asyncio.Semaphore(8)
        async def _gated(p):
            async with sem:
                return await upload_one(SessionLocal, p)
        results = await asyncio.gather(*(_gated(p) for p in files))
    else:
        results = []
        for p in files:
            results.append(await upload_one(SessionLocal, p))
    elapsed = time.monotonic() - t0

    await engine.dispose()
    return results, elapsed


def summarize_results(results: list[dict]) -> dict:
    ok = sum(1 for r in results if r["status"] == "ok")
    err = sum(1 for r in results if r["status"] == "err")
    err_details = [r for r in results if r["status"] == "err"]
    return {"ok": ok, "err": err, "err_samples": err_details[:3]}


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

async def scenario_A_fresh_serial() -> dict:
    await reset_data()
    results, secs = await run_batch(T_FILES, concurrent=False)
    counts = await measure_counts()
    return {"label": "A_fresh_serial", "elapsed_sec": secs,
            "results_summary": summarize_results(results), "counts": counts}


async def scenario_B_fresh_concurrent() -> dict:
    await reset_data()
    results, secs = await run_batch(T_FILES, concurrent=True)
    counts = await measure_counts()
    return {"label": "B_fresh_concurrent", "elapsed_sec": secs,
            "results_summary": summarize_results(results), "counts": counts}


async def scenario_C_warm_serial() -> dict:
    await reset_data()
    # Warm-up: load W_FILES serially so master-data exists
    w_results, _ = await run_batch(W_FILES, concurrent=False)
    if any(r["status"] == "err" for r in w_results):
        raise RuntimeError(f"Warmup failed: {w_results}")
    before = await measure_counts()
    results, secs = await run_batch(T_FILES, concurrent=False)
    after = await measure_counts()
    delta = {k: after[k] - before[k] for k in after}
    return {"label": "C_warm_serial", "elapsed_sec": secs,
            "results_summary": summarize_results(results),
            "counts_delta": delta, "before": before, "after": after}


async def scenario_D_warm_concurrent() -> dict:
    await reset_data()
    w_results, _ = await run_batch(W_FILES, concurrent=False)
    if any(r["status"] == "err" for r in w_results):
        raise RuntimeError(f"Warmup failed: {w_results}")
    before = await measure_counts()
    results, secs = await run_batch(T_FILES, concurrent=True)
    after = await measure_counts()
    delta = {k: after[k] - before[k] for k in after}
    return {"label": "D_warm_concurrent", "elapsed_sec": secs,
            "results_summary": summarize_results(results),
            "counts_delta": delta, "before": before, "after": after}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print(f"Test DSN: {TEST_DSN_ASYNC}")
    missing = [p for p in T_FILES + W_FILES if not p.exists()]
    if missing:
        print("MISSING FILES:")
        for m in missing: print(f"  {m}")
        sys.exit(1)
    print(f"T_FILES ({len(T_FILES)}): {[p.name for p in T_FILES]}")
    print(f"W_FILES ({len(W_FILES)}): {[p.name for p in W_FILES]}")
    print()

    print("=" * 72)
    print("Step 0: Create test DB and copy schema from rcm_denials_dev")
    print("=" * 72)
    await create_test_db_with_schema()
    initial = await measure_counts()
    print(f"  initial counts (should all be 0): {initial}")
    print()

    results = {}
    try:
        print("=" * 72)
        print("Scenario A — fresh DB, serial upload (baseline for race detection)")
        print("=" * 72)
        results["A"] = await scenario_A_fresh_serial()
        print(json.dumps(results["A"], indent=2, default=str))
        print()

        print("=" * 72)
        print("Scenario B — fresh DB, 8-wide concurrent upload (race expected)")
        print("=" * 72)
        results["B"] = await scenario_B_fresh_concurrent()
        print(json.dumps(results["B"], indent=2, default=str))
        print()

        print("=" * 72)
        print("Scenario C — warm DB, serial upload (warm baseline)")
        print("=" * 72)
        results["C"] = await scenario_C_warm_serial()
        print(json.dumps(results["C"], indent=2, default=str))
        print()

        print("=" * 72)
        print("Scenario D — warm DB, 8-wide concurrent upload (race not expected)")
        print("=" * 72)
        results["D"] = await scenario_D_warm_concurrent()
        print(json.dumps(results["D"], indent=2, default=str))
        print()

    finally:
        print("=" * 72)
        print("Cleanup: drop test DB")
        print("=" * 72)
        await drop_test_db()

    # Comparison table
    print()
    print("=" * 72)
    print("RESULT — fresh-DB race check (B should match A; pre-CR-069 it won't)")
    print("=" * 72)
    a, b = results["A"]["counts"], results["B"]["counts"]
    fresh_metrics_match = (a == b)
    fresh_errors_zero = results["B"]["results_summary"]["err"] == 0
    for k in a:
        marker = "MATCH" if a[k] == b[k] else f"DIFF (Δ={b[k] - a[k]:+})"
        print(f"  {k:14s} A={a[k]:>5}  B={b[k]:>5}   [{marker}]")
    print(f"  errors in B: {results['B']['results_summary']['err']}")

    print()
    print("=" * 72)
    print("RESULT — warm-DB race check (D should match C; race vanished)")
    print("=" * 72)
    c, d = results["C"]["counts_delta"], results["D"]["counts_delta"]
    warm_metrics_match = (c == d)
    warm_errors_zero = results["D"]["results_summary"]["err"] == 0
    for k in c:
        marker = "MATCH" if c[k] == d[k] else f"DIFF (Δ={d[k] - c[k]:+})"
        print(f"  {k:14s} C={c[k]:>5}  D={d[k]:>5}   [{marker}]")
    print(f"  errors in D: {results['D']['results_summary']['err']}")

    print()
    print("=" * 72)
    print("PRE-CR-069 EXPECTATIONS:")
    print("  Fresh:  metrics_match=False, errors>=1   (race produces IntegrityError)")
    print("  Warm:   metrics_match=True,  errors=0    (master-data populated)")
    print()
    print("OBSERVED:")
    print(f"  Fresh:  metrics_match={fresh_metrics_match}, errors_zero={fresh_errors_zero}")
    print(f"  Warm:   metrics_match={warm_metrics_match},  errors_zero={warm_errors_zero}")
    print("=" * 72)

    Path("scripts/cr069_pre_verification_post.json").write_text(
        json.dumps(results, indent=2, default=str), encoding="utf-8"
    )
    print("Wrote scripts/cr069_pre_verification_post.json")


if __name__ == "__main__":
    asyncio.run(main())
