"""Bulk-upload .dat files from the extracted Claims staging area to the
running local backend at http://127.0.0.1:8000.

Strategy:
  - Phase 1: all 837 originals     (creates claims; FK targets for 835s + replacements)
  - Phase 2: all 837 replacements   (frequency_code=7; references original claim_numbers)
  - Phase 3: all 835 originals      (creates remits + links to claim_id)
  - Phase 4: all 835 replacements

Within each phase: N concurrent workers hit POST /api/edi/upload.

Duplicate uploads (same content_hash) return 200 OK with is_duplicate=true —
treated as success, not an error.

Progress reported every 100 files per phase.
"""
from __future__ import annotations

import asyncio
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

STAGING = Path(r"C:\Users\Gowdham B\AppData\Local\Temp\claims_staging")
BACKEND = "http://127.0.0.1:8000"
UPLOAD = f"{BACKEND}/api/edi/upload"
CONCURRENCY = 8
TIMEOUT_SECS = 60


def _categorize(p: Path) -> str:
    """Returns one of: '837_original', '837_replacement', '835_original',
    '835_replacement', or 'unknown'."""
    n = p.name.lower()
    if "837" in n:
        kind = "837"
    elif "835" in n:
        kind = "835"
    else:
        return "unknown"
    if "replacement" in n:
        sub = "replacement"
    elif "original" in n:
        sub = "original"
    else:
        return "unknown"
    return f"{kind}_{sub}"


async def upload_one(client: httpx.AsyncClient, path: Path) -> tuple[Path, dict | None, str | None]:
    try:
        with path.open("rb") as f:
            files = {"file": (path.name, f, "application/octet-stream")}
            resp = await client.post(UPLOAD, files=files, timeout=TIMEOUT_SECS)
        if resp.status_code != 200:
            return (path, None, f"HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        return (path, body, None)
    except Exception as e:
        return (path, None, f"{type(e).__name__}: {e}")


async def upload_phase(label: str, paths: list[Path], concurrency: int) -> dict:
    sem = asyncio.Semaphore(concurrency)
    results: dict[str, int] = Counter()
    failures: list[tuple[str, str]] = []
    t_start = time.monotonic()
    done = 0
    total = len(paths)

    async def _runner(client, p):
        nonlocal done
        async with sem:
            path, body, err = await upload_one(client, p)
            done += 1
            if err is not None:
                results["error"] += 1
                failures.append((path.name, err))
                if len(failures) <= 5:
                    print(f"    [err] {path.name}: {err[:120]}")
            elif body and body.get("is_duplicate"):
                results["duplicate"] += 1
            elif body and body.get("success"):
                results["success"] += 1
            else:
                results["other"] += 1
                if len(failures) <= 5:
                    print(f"    [other] {path.name}: success={body.get('success')} err={body.get('error')}")
                failures.append((path.name, str(body)[:150]))
            if done % 100 == 0 or done == total:
                elapsed = time.monotonic() - t_start
                rate = done / max(elapsed, 0.001)
                eta_secs = (total - done) / max(rate, 0.001)
                print(f"    [{label}] {done}/{total}  "
                      f"success={results['success']} dup={results['duplicate']} err={results['error']}  "
                      f"{rate:.1f}/s  eta={eta_secs:.0f}s")

    limits = httpx.Limits(max_connections=concurrency + 4, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(limits=limits) as client:
        tasks = [_runner(client, p) for p in paths]
        await asyncio.gather(*tasks)

    elapsed = time.monotonic() - t_start
    return {
        "label": label,
        "total": total,
        "success": results["success"],
        "duplicate": results["duplicate"],
        "error": results["error"],
        "other": results["other"],
        "elapsed_seconds": round(elapsed, 1),
        "rate_per_second": round(total / max(elapsed, 0.001), 2),
        "failure_samples": failures[:10],
    }


async def main() -> None:
    # Health check before doing anything
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(f"{BACKEND}/openapi.json", timeout=10)
            if r.status_code != 200:
                print(f"ERR: backend not healthy (HTTP {r.status_code})")
                sys.exit(1)
        except Exception as e:
            print(f"ERR: backend unreachable: {e}")
            sys.exit(1)

    all_dats = sorted(p for p in STAGING.rglob("*.dat"))
    print(f"discovered {len(all_dats)} .dat files under {STAGING}")

    by_cat: dict[str, list[Path]] = {}
    for p in all_dats:
        by_cat.setdefault(_categorize(p), []).append(p)

    print()
    print("=== census ===")
    for k in sorted(by_cat):
        print(f"  {k:25s}  {len(by_cat[k])}")
    print()

    phases = [
        ("837_original",    by_cat.get("837_original", [])),
        ("837_replacement", by_cat.get("837_replacement", [])),
        ("835_original",    by_cat.get("835_original", [])),
        ("835_replacement", by_cat.get("835_replacement", [])),
    ]

    summary: list[dict] = []
    overall_start = time.monotonic()
    for label, paths in phases:
        if not paths:
            print(f"\n  [{label}] (none)")
            continue
        print(f"\n  --- phase {label} ({len(paths)} files; {CONCURRENCY}-wide concurrency) ---")
        out = await upload_phase(label, paths, CONCURRENCY)
        summary.append(out)
        print(f"  [{label}] done: success={out['success']} dup={out['duplicate']} err={out['error']} "
              f"in {out['elapsed_seconds']}s ({out['rate_per_second']}/s)")

    total_elapsed = time.monotonic() - overall_start
    print()
    print("=" * 78)
    print("BULK UPLOAD SUMMARY")
    print("=" * 78)
    grand_total = 0
    grand_success = 0
    grand_dup = 0
    grand_err = 0
    for s in summary:
        print(f"  {s['label']:25s}  total={s['total']:>5}  success={s['success']:>5}  "
              f"dup={s['duplicate']:>5}  err={s['error']:>4}  "
              f"{s['elapsed_seconds']}s  {s['rate_per_second']}/s")
        grand_total += s["total"]
        grand_success += s["success"]
        grand_dup += s["duplicate"]
        grand_err += s["error"]
    print(f"  {'TOTAL':25s}  total={grand_total:>5}  success={grand_success:>5}  "
          f"dup={grand_dup:>5}  err={grand_err:>4}  "
          f"{total_elapsed:.0f}s")

    import json
    Path("scripts/bulk_upload_post.json").write_text(
        json.dumps({"summary": summary, "grand_total_seconds": total_elapsed}, indent=2, default=str),
        encoding="utf-8",
    )
    print("\n  wrote scripts/bulk_upload_post.json")


if __name__ == "__main__":
    asyncio.run(main())
