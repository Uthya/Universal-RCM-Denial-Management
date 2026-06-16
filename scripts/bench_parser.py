"""PARSER-STRESS-001 — synthetic 837P benchmark at 100/1k/10k claims.

Measures wall-clock time for parse / validate / persist, plus tracemalloc
peak per phase. Runs each scale twice to detect leaks. Reports scaling
exponent so an O(n^2) regression shows up as k>1.3.

Usage:
    PYTHONPATH=src python scripts/bench_parser.py
"""

from __future__ import annotations

import asyncio
import gc
import math
import os
import time
import tracemalloc
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

# Test settings (must satisfy Settings validation)
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET_KEY", "bench-secret-at-least-16-characters-long")

import asyncpg
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from rcm.parsing import parse_edi
from rcm.parsing.persistence import save_parse_context
from rcm.parsing.validators import run_all as run_validators


SCALES = (100, 1_000, 10_000)
RUNS_PER_SCALE = 2
PARSE_TARGET_CLAIMS_PER_SEC = 5_000

# CR-082A — DSN repointed from the deleted native-PG `rcm_v2_verify` (5432)
# to the docker dev DB `rcm_denials_dev` (5433). PARSER-STRESS-001 benchmark
# now exercises the same DB the live backend uses.
LOCAL_DSN = "postgresql+asyncpg://rcm:rcm_dev_password@localhost:5433/rcm_denials_dev"
LOCAL_RAW_DSN = "postgresql://rcm:rcm_dev_password@localhost:5433/rcm_denials_dev"


# ---------------------------------------------------------------------------
# Synthetic 837P generator
# ---------------------------------------------------------------------------

_PROCEDURE_POOL = ("99213", "99214", "99203", "99204", "99381", "99396", "97110", "97140")
_DX_POOL = ("I10", "E119", "M5450", "J449", "K219", "F329", "N390", "R51")


def build_edi(n_claims: int) -> str:
    """Generate a syntactically valid 837P with `n_claims` distinct CLM segments."""
    segs: list[str] = []
    # Envelope
    segs.append(
        "ISA*00*          *00*          *ZZ*BENCH_SUB      "
        "*ZZ*BENCH_RCV      *260601*1200*^*00501*000000999*0*P*:"
    )
    segs.append("GS*HC*BENCH_SUB*BENCH_RCV*20260601*1200*999*X*005010X222A1")
    segs.append("ST*837*0999*005010X222A1")
    segs.append("BHT*0019*00*BENCH0001*20260601*1200*CH")

    # Header (billing provider, subscriber loop, payer)
    segs.append("HL*1**20*1")
    segs.append("NM1*85*2*BENCH CLINIC*****XX*1234567890")
    segs.append("REF*EI*123456789")

    for i in range(n_claims):
        seq = f"{i + 1:07d}"
        segs.append(f"HL*{i + 2}*1*22*0")
        segs.append("SBR*P*18*GRPBENCH*******CI")
        segs.append(f"NM1*IL*1*PATIENT*BENCH{seq}****MI*MEM{seq}")
        segs.append("NM1*PR*2*BENCH PAYER*****PI*BPAY1")
        segs.append(f"CLM*BENCH-{seq}*150***11:B:1*Y*A*Y*Y")
        # Two diagnoses (deterministic per i so the test is reproducible)
        seg_dx_principal = _DX_POOL[i % len(_DX_POOL)]
        seg_dx_other = _DX_POOL[(i + 1) % len(_DX_POOL)]
        segs.append(f"HI*ABK:{seg_dx_principal}")
        segs.append(f"HI*ABF:{seg_dx_other}")
        segs.append("DTP*472*D8*20260601")
        proc = _PROCEDURE_POOL[i % len(_PROCEDURE_POOL)]
        segs.append(f"SV1*HC:{proc}*150*UN*1*11**1:2")

    # Trailer
    segs.append(f"SE*{len(segs) - 2}*0999")
    segs.append("GE*1*999")
    segs.append("IEA*1*000000999")
    return "~".join(segs) + "~"


# ---------------------------------------------------------------------------
# Measurement plumbing
# ---------------------------------------------------------------------------

@dataclass
class PhaseResult:
    phase: str
    seconds: float
    peak_mb: float
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScaleResult:
    scale: int
    run: int
    edi_size_bytes: int
    phases: list[PhaseResult] = field(default_factory=list)


def _format_table(rows: list[list[str]], headers: list[str]) -> str:
    cols = list(zip(*([headers, *rows])))
    widths = [max(len(str(c)) for c in col) for col in cols]
    out_lines = [" │ ".join(h.ljust(w) for h, w in zip(headers, widths))]
    out_lines.append("─┼─".join("─" * w for w in widths))
    for r in rows:
        out_lines.append(" │ ".join(str(v).ljust(w) for v, w in zip(r, widths)))
    return "\n".join(out_lines)


# ---------------------------------------------------------------------------
# Per-scale benchmark
# ---------------------------------------------------------------------------

async def bench_one(scale: int, run: int, session_factory) -> ScaleResult:
    edi_text = build_edi(scale)
    edi_bytes = edi_text.encode("utf-8")
    result = ScaleResult(scale=scale, run=run, edi_size_bytes=len(edi_bytes))

    # ---- Parse ----
    gc.collect()
    tracemalloc.start()
    t0 = time.perf_counter()
    ctx = parse_edi(edi_text, f"bench-{scale}-r{run}.edi")
    t1 = time.perf_counter()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    result.phases.append(PhaseResult(
        phase="parse",
        seconds=t1 - t0,
        peak_mb=peak / (1024 * 1024),
        extras={
            "claims_parsed": len(ctx.claims),
            "raw_segments": len(ctx.raw_segments),
            "parse_events": len(ctx.parse_events),
        },
    ))

    # ---- Validate (no DB → Tier 3 skipped) ----
    gc.collect()
    tracemalloc.start()
    t0 = time.perf_counter()
    await run_validators(ctx, session=None)
    t1 = time.perf_counter()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    n_errors = sum(1 for e in ctx.parse_errors if e.severity == "ERROR")
    n_warnings = sum(1 for e in ctx.parse_errors if e.severity == "WARNING")
    result.phases.append(PhaseResult(
        phase="validate",
        seconds=t1 - t0,
        peak_mb=peak / (1024 * 1024),
        extras={"errors": n_errors, "warnings": n_warnings},
    ))

    # ---- Persist ----
    # Hard-reset the verify DB so each scale gets a clean slate (FK CASCADE
    # from edi_files takes care of dependents)
    await _reset_db_state()
    gc.collect()
    tracemalloc.start()
    t0 = time.perf_counter()
    async with session_factory() as session:
        import hashlib
        content_hash = hashlib.sha256(edi_bytes + f"r{run}".encode()).hexdigest()
        ef = await save_parse_context(session, ctx, content_hash=content_hash)
    t1 = time.perf_counter()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    result.phases.append(PhaseResult(
        phase="persist",
        seconds=t1 - t0,
        peak_mb=peak / (1024 * 1024),
        extras={"edi_file_id": ef.id, "claims_saved": ef.parse_summary["claims_saved"]},
    ))

    return result


async def _reset_db_state() -> None:
    """Truncate everything that parse_and_save writes to. Keeps schema."""
    c = await asyncpg.connect(LOCAL_RAW_DSN)
    try:
        # CASCADE truncate from edi_files cascades to claims → lines/dx/remits/etc.
        # But raw_segments / parse_events are partitioned; truncate each parent.
        await c.execute(
            "TRUNCATE edi_files, payers, patients, providers, subscribers, "
            "raw_segments, parse_events RESTART IDENTITY CASCADE"
        )
    finally:
        await c.close()


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

async def main() -> int:
    engine = create_async_engine(LOCAL_DSN, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Sanity: confirm verify DB reachable
    try:
        c = await asyncpg.connect(LOCAL_RAW_DSN, timeout=5)
        await c.close()
    except Exception as exc:
        print(f"FATAL: cannot reach local PG at {LOCAL_RAW_DSN}: {exc}")
        return 2

    all_results: list[ScaleResult] = []
    for scale in SCALES:
        for run in range(1, RUNS_PER_SCALE + 1):
            print(f"  → scale={scale:>5}  run={run}  generating + benchmarking...", flush=True)
            res = await bench_one(scale, run, session_factory)
            all_results.append(res)
            for ph in res.phases:
                rate = res.scale / ph.seconds if ph.seconds > 0 else float("inf")
                print(f"      {ph.phase:>8}  {ph.seconds*1000:>10.2f} ms  "
                      f"peak {ph.peak_mb:>7.2f} MB  rate {rate:>10.1f} claims/s",
                      flush=True)

    await engine.dispose()
    _print_report(all_results)
    return 0


def _print_report(results: list[ScaleResult]) -> None:
    print()
    print("=" * 78)
    print("PARSER-STRESS-001 — REPORT")
    print("=" * 78)

    # --- Headline table: per scale per run, per phase ---
    rows: list[list[str]] = []
    for r in results:
        for ph in r.phases:
            rate = r.scale / ph.seconds if ph.seconds > 0 else float("inf")
            rows.append([
                f"{r.scale}", f"{r.run}", ph.phase,
                f"{ph.seconds * 1000:.2f}",
                f"{ph.peak_mb:.2f}",
                f"{rate:.1f}",
                f"{ph.extras}",
            ])
    print()
    print(_format_table(
        rows,
        ["scale", "run", "phase", "ms", "peak_MB", "claims/sec", "extras"],
    ))

    # --- Scaling exponent per phase ---
    print()
    print("─── Scaling exponent (time = a · n^k; k≈1 linear, k≈2 quadratic) ───")
    for phase in ("parse", "validate", "persist"):
        # Use run-1 measurements at each scale
        pts: list[tuple[int, float]] = []
        for r in results:
            if r.run != 1:
                continue
            for ph in r.phases:
                if ph.phase == phase:
                    pts.append((r.scale, ph.seconds))
        if len(pts) < 2:
            continue
        # Fit log(time) = log(a) + k * log(n) via simple regression on log-log
        logs_x = [math.log(s) for s, _ in pts]
        logs_y = [math.log(t) for _, t in pts]
        mean_x = sum(logs_x) / len(logs_x)
        mean_y = sum(logs_y) / len(logs_y)
        num = sum((x - mean_x) * (y - mean_y) for x, y in zip(logs_x, logs_y))
        den = sum((x - mean_x) ** 2 for x in logs_x)
        k = num / den if den else float("nan")
        verdict = (
            "sub-linear" if k < 0.9 else
            "linear" if k <= 1.15 else
            "near-linear" if k <= 1.3 else
            "SUPERLINEAR" if k <= 1.6 else
            "QUADRATIC"
        )
        print(f"  {phase:>8}: k = {k:.3f}   [{verdict}]")

    # --- Leak check: peak memory comparison across runs at same scale ---
    print()
    print("─── Memory-leak check (peak run2 / peak run1 at same scale) ───")
    for scale in sorted({r.scale for r in results}):
        for phase in ("parse", "validate", "persist"):
            r1 = next((r for r in results if r.scale == scale and r.run == 1), None)
            r2 = next((r for r in results if r.scale == scale and r.run == 2), None)
            if not r1 or not r2:
                continue
            p1 = next((p for p in r1.phases if p.phase == phase), None)
            p2 = next((p for p in r2.phases if p.phase == phase), None)
            if not p1 or not p2 or p1.peak_mb == 0:
                continue
            ratio = p2.peak_mb / p1.peak_mb
            tag = "OK" if 0.7 <= ratio <= 1.3 else "LEAK?"
            print(f"  scale={scale:>5} {phase:>8}: run1={p1.peak_mb:6.2f} MB  "
                  f"run2={p2.peak_mb:6.2f} MB  ratio={ratio:.2f}  [{tag}]")

    # --- Throughput target check ---
    print()
    print(f"─── Throughput target: ≥{PARSE_TARGET_CLAIMS_PER_SEC} claims/sec (parse phase) ───")
    for scale in sorted({r.scale for r in results}):
        r1 = next((r for r in results if r.scale == scale and r.run == 1), None)
        if not r1:
            continue
        parse = next((p for p in r1.phases if p.phase == "parse"), None)
        if not parse:
            continue
        rate = scale / parse.seconds if parse.seconds > 0 else float("inf")
        verdict = "PASS" if rate >= PARSE_TARGET_CLAIMS_PER_SEC else "MISS"
        print(f"  scale={scale:>5}: {rate:>10.1f} claims/sec  [{verdict}]")

    print()


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main()))
