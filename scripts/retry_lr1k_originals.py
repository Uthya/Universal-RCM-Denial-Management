"""CR-068A — retry LR1K originals that failed during the bulk upload's
cold-start race window (now resolved by CR-069).

Strategy:
  1. Enumerate every LR1K_*_original_837.dat in the staging directory.
  2. Ask the local DB which of those are already persisted.
  3. Diff -> missing files.
  4. POST each missing file to /api/edi/upload (serially — the failed-set
     is small, easier to audit, and CR-069 makes any concurrency safe).
  5. Capture full response per file.
  6. Write scripts/retry_lr1k_originals_post.json with the summary.

Constraints honoured:
  - No schema change, no migration, no new tables/MVs/indexes/registries
  - Writes only to existing tables via the normal upload pathway
  - Does NOT populate claim_lifecycles or modify mv_claim_labels
  - Does NOT retrain models or run benchmarks
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import asyncpg
import httpx

STAGING = Path(r"C:\Users\Gowdham B\AppData\Local\Temp\claims_staging\claim_pairs_1000_low_risk_PID")
BACKEND = "http://127.0.0.1:8000"
UPLOAD  = f"{BACKEND}/api/edi/upload"
LOCAL_DSN = "postgresql://rcm:rcm_dev_password@localhost:5433/rcm_denials_dev"
TIMEOUT_SECS = 60


async def fetch_already_loaded() -> set[str]:
    c = await asyncpg.connect(dsn=LOCAL_DSN)
    try:
        rows = await c.fetch(
            "SELECT file_name FROM edi_files WHERE file_name LIKE 'LR1K_%_original_837.dat'"
        )
        return {r["file_name"] for r in rows}
    finally:
        await c.close()


def enumerate_inventory() -> list[Path]:
    paths = sorted(STAGING.glob("LR1K_*_pair_*/LR1K_*_original_837.dat"))
    return paths


async def post_one(client: httpx.AsyncClient, path: Path) -> dict:
    t0 = time.monotonic()
    try:
        with path.open("rb") as f:
            resp = await client.post(
                UPLOAD,
                files={"file": (path.name, f, "application/octet-stream")},
                timeout=TIMEOUT_SECS,
            )
        elapsed = round(time.monotonic() - t0, 3)
        if resp.status_code != 200:
            return {"file": path.name, "status_code": resp.status_code,
                    "ok": False, "elapsed_s": elapsed,
                    "body_sample": resp.text[:300]}
        body = resp.json()
        return {"file": path.name, "status_code": 200,
                "ok": bool(body.get("success")) or bool(body.get("is_duplicate")),
                "is_duplicate": bool(body.get("is_duplicate")),
                "elapsed_s": elapsed,
                "edi_file_id": body.get("edi_file_id"),
                "claims_count": body.get("claims_count"),
                "claim_lines_count": body.get("claim_lines_count"),
                "diagnoses_count": body.get("diagnoses_count"),
                "raw_segments_count": body.get("raw_segments_count"),
                "validation_errors": body.get("validation_errors") or [],
                "pair_status": body.get("pair_status"),
                "error": body.get("error")}
    except Exception as e:
        return {"file": path.name, "status_code": -1, "ok": False,
                "elapsed_s": round(time.monotonic() - t0, 3),
                "error": f"{type(e).__name__}: {str(e)[:200]}"}


async def main() -> None:
    # 0. Health check
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{BACKEND}/openapi.json", timeout=10)
        if r.status_code != 200:
            raise SystemExit(f"backend unhealthy: HTTP {r.status_code}")

    # 1. Inventory
    inventory = enumerate_inventory()
    print(f"LR1K originals in staging: {len(inventory)}")

    # 2. Already-loaded
    loaded = await fetch_already_loaded()
    print(f"LR1K originals already in DB: {len(loaded)}")

    # 3. Diff
    missing = [p for p in inventory if p.name not in loaded]
    print(f"Missing -> to retry: {len(missing)}")
    print()
    for p in missing:
        print(f"  {p.name}")
    print()

    if not missing:
        print("Nothing to retry. Exiting.")
        return

    # 4. Retry serially (CR-069 makes concurrent safe too, but serial is
    #    simpler to audit for a 21-file batch)
    print("=" * 72)
    print(f"Retrying {len(missing)} files serially…")
    print("=" * 72)
    results: list[dict] = []
    t_start = time.monotonic()
    async with httpx.AsyncClient() as client:
        for i, path in enumerate(missing, 1):
            res = await post_one(client, path)
            results.append(res)
            tag = ("OK" if res.get("ok") and not res.get("is_duplicate")
                   else "DUP" if res.get("is_duplicate")
                   else "ERR")
            print(f"  [{i:>2}/{len(missing)}] {tag:<3}  {res['file']:<46}  "
                  f"claims={res.get('claims_count', '-'):>3}  "
                  f"lines={res.get('claim_lines_count', '-'):>3}  "
                  f"pair={res.get('pair_status', '-')}  "
                  f"{res['elapsed_s']}s")
    total_elapsed = round(time.monotonic() - t_start, 2)

    # 5. Summary
    n_ok    = sum(1 for r in results if r.get("ok") and not r.get("is_duplicate"))
    n_dup   = sum(1 for r in results if r.get("is_duplicate"))
    n_err   = sum(1 for r in results if not r.get("ok"))
    new_claims = sum(int(r.get("claims_count") or 0) for r in results
                     if r.get("ok") and not r.get("is_duplicate"))
    new_lines  = sum(int(r.get("claim_lines_count") or 0) for r in results
                     if r.get("ok") and not r.get("is_duplicate"))
    new_diags  = sum(int(r.get("diagnoses_count") or 0) for r in results
                     if r.get("ok") and not r.get("is_duplicate"))

    print()
    print("=" * 72)
    print("RETRY SUMMARY")
    print("=" * 72)
    print(f"  files attempted   : {len(missing)}")
    print(f"  succeeded (new)   : {n_ok}")
    print(f"  succeeded (dup)   : {n_dup}")
    print(f"  failed            : {n_err}")
    print(f"  new claims        : {new_claims}")
    print(f"  new claim_lines   : {new_lines}")
    print(f"  new diagnoses     : {new_diags}")
    print(f"  total wall-clock  : {total_elapsed}s ({len(missing)/max(total_elapsed,0.001):.1f}/s)")

    out_path = Path("scripts/retry_lr1k_originals_post.json")
    out_path.write_text(json.dumps({
        "n_attempted": len(missing),
        "n_ok": n_ok, "n_dup": n_dup, "n_err": n_err,
        "new_claims": new_claims, "new_claim_lines": new_lines,
        "new_diagnoses": new_diags,
        "total_elapsed_s": total_elapsed,
        "missing_inventory": [p.name for p in missing],
        "results": results,
    }, indent=2, default=str), encoding="utf-8")
    print(f"\n  wrote {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
