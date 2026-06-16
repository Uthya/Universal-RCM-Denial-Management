"""CR-078 — prediction parity verification harness.

Runs the FB predict path against a fixed set of `edi_file_id` values and
asserts that every scoring field is byte-identical to a saved baseline.
Only `top_denial_reasons` text is expected to differ.

Usage:
    # 1. Before merging CR-078, on the prior code:
    #    python scripts/cr078_parity_check.py --capture --file-ids 12,15,21,33 \\
    #        --out scripts/cr078_baseline.json
    #
    # 2. After merging CR-078, on the new code:
    #    python scripts/cr078_parity_check.py --verify --file-ids 12,15,21,33 \\
    #        --baseline scripts/cr078_baseline.json

Exits 0 on parity; non-zero with a diff summary otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE_URL = os.environ.get("RCM_API_URL", "http://localhost:8000")

# Fields that MUST be identical pre/post CR-078 — touching any of these
# means the change exceeded its stated scope.
SCORING_FIELDS = (
    "risk_score", "risk_level", "service_variant", "claim_subtype",
    "claim_id", "claim_number",
)


async def fetch_predictions(file_id: int, base_url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=base_url, timeout=60) as c:
        r = await c.post(f"/api/predictions/predict-file/{file_id}")
        r.raise_for_status()
        return r.json()


def project_scoring(resp: dict) -> dict:
    """Strip the response to only the fields parity cares about."""
    out: dict[str, Any] = {
        "edi_file_id": resp.get("edi_file_id"),
        "predicted_claims": resp.get("predicted_claims"),
        "risk_summary": resp.get("risk_summary"),
        "claims": [],
    }
    for c in resp.get("high_risk_claims", []):
        out["claims"].append({f: c.get(f) for f in SCORING_FIELDS})
    return out


def diff_scoring(before: dict, after: dict) -> list[str]:
    """Yield human-readable diffs over the projected scoring fields."""
    diffs: list[str] = []
    for k in ("edi_file_id", "predicted_claims", "risk_summary"):
        if before.get(k) != after.get(k):
            diffs.append(f"  {k}: before={before.get(k)!r} after={after.get(k)!r}")
    b_claims = {c["claim_id"]: c for c in before.get("claims", [])}
    a_claims = {c["claim_id"]: c for c in after.get("claims", [])}
    for cid in sorted(set(b_claims) | set(a_claims)):
        if cid not in a_claims:
            diffs.append(f"  claim {cid} disappeared after CR-078")
            continue
        if cid not in b_claims:
            diffs.append(f"  claim {cid} appeared after CR-078")
            continue
        for f in SCORING_FIELDS:
            if b_claims[cid].get(f) != a_claims[cid].get(f):
                diffs.append(
                    f"  claim {cid} field {f!r}: "
                    f"before={b_claims[cid].get(f)!r} after={a_claims[cid].get(f)!r}"
                )
    return diffs


async def main_async(args: argparse.Namespace) -> int:
    file_ids = [int(x) for x in args.file_ids.split(",") if x.strip()]

    if args.capture:
        baseline = {}
        for fid in file_ids:
            resp = await fetch_predictions(fid, args.base_url)
            baseline[str(fid)] = project_scoring(resp)
        Path(args.out).write_text(json.dumps(baseline, indent=2))
        print(f"[capture] baseline written to {args.out} for {len(file_ids)} files")
        return 0

    if args.verify:
        baseline = json.loads(Path(args.baseline).read_text())
        any_diff = False
        for fid in file_ids:
            resp = await fetch_predictions(fid, args.base_url)
            current = project_scoring(resp)
            before = baseline.get(str(fid))
            if before is None:
                print(f"[file {fid}] no baseline — skipping")
                continue
            diffs = diff_scoring(before, current)
            if diffs:
                any_diff = True
                print(f"[file {fid}] PARITY MISMATCH:")
                for d in diffs:
                    print(d)
            else:
                n = len(current.get("claims", []))
                print(f"[file {fid}] OK — {n} claim(s), every scoring field identical")
        if any_diff:
            print()
            print("FAIL: CR-078 changed scoring output. Review.")
            return 2
        print()
        print("PASS: CR-078 preserved every scoring field across all files.")
        return 0

    print("Either --capture or --verify is required.", file=sys.stderr)
    return 1


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--capture", action="store_true",
                   help="Snapshot the current predictions as the parity baseline.")
    p.add_argument("--verify", action="store_true",
                   help="Compare current predictions to the stored baseline.")
    p.add_argument("--file-ids", required=True,
                   help="Comma-separated edi_file_id values.")
    p.add_argument("--out", default="scripts/cr078_baseline.json",
                   help="Path to write the baseline (capture mode).")
    p.add_argument("--baseline", default="scripts/cr078_baseline.json",
                   help="Path to the stored baseline (verify mode).")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL,
                   help="API base URL (default: $RCM_API_URL or localhost:8000).")
    args = p.parse_args()
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
