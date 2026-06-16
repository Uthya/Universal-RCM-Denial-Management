"""CR-079 — candidate-vs-baseline shadow parity check.

Loads both bundles (production + candidate) for each variant, scores the
same set of fresh EDI files through each, and reports per-claim score drift.

This is the operational gate immediately before promotion: it answers
"would the candidate produce different risk levels for the same claims?"
and surfaces the magnitude of the change.

Usage:
    PYTHONPATH=src python scripts/cr079_parity_check.py --shadow \\
        --file-ids 12,15,21,33

Output: scripts/cr079_parity_shadow.json
Exit codes:
    0 = report written successfully (does NOT mean candidate passed; the
        promote script decides based on the report).
    2 = either bundle missing or file_id not found.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg
import numpy as np
import pandas as pd
from fastapi import HTTPException

from rcm.core.config import settings
from rcm.core.database import async_session
from rcm.ml.predictor import HealthcarePredictor

logging.basicConfig(level=logging.WARNING)

BASE_ROOT = Path("artifacts/featurebuilder")
CAND_ROOT = Path("artifacts/featurebuilder_cr079_candidate")
OUT_PATH  = Path("scripts/cr079_parity_shadow.json")

VARIANT_KEYS: dict[tuple[str, str], str] = {
    ("837P", "healthcare"): "837P_healthcare",
    ("837D", "dental"):     "837D_dental",
    ("837I", "home_care"):  "837I_home_care",
}


async def _connect() -> asyncpg.Connection:
    return await asyncpg.connect(dsn=settings.sync_database_url(), timeout=10)


async def _load_file_corpus(session, edi_file_id: int) -> pd.DataFrame:
    """Use the same DB → pandas pipeline the predict endpoint uses."""
    from rcm.ml.shadow import _load_predict_corpus
    c = await _connect()
    try:
        rows = await c.fetch(
            "SELECT id FROM claims WHERE edi_file_id = $1 AND deleted_at IS NULL",
            edi_file_id,
        )
        ids = [int(r["id"]) for r in rows]
    finally:
        await c.close()
    if not ids:
        return pd.DataFrame()
    return await _load_predict_corpus(session, ids)


async def _bucket_by_variant(df: pd.DataFrame) -> dict[tuple[str, str], pd.DataFrame]:
    out: dict[tuple[str, str], pd.DataFrame] = {}
    if df.empty:
        return out
    for (v, st), sub in df.groupby(["service_variant", "claim_subtype"], dropna=False):
        out[(str(v), str(st))] = sub.reset_index(drop=True)
    return out


async def _shadow_one_file(file_id: int) -> dict[str, Any]:
    print(f"[shadow file_id={file_id}] loading corpus...", flush=True)
    async with async_session() as session:
        df = await _load_file_corpus(session, file_id)
        if df.empty:
            return {"file_id": file_id, "status": "empty", "claims": []}

        buckets = await _bucket_by_variant(df)
        per_claim_rows: list[dict[str, Any]] = []
        per_variant_summary: dict[str, dict[str, Any]] = {}

        for (variant, subtype), sub_df in buckets.items():
            key = VARIANT_KEYS.get((variant, subtype))
            if key is None:
                continue  # Option C fallback — outside CR-079
            base_dir = BASE_ROOT / key
            cand_dir = CAND_ROOT / key
            if not (cand_dir / "model.json").exists():
                continue
            if not (base_dir / "model.json").exists():
                continue
            base = HealthcarePredictor.load(base_dir)
            cand = HealthcarePredictor.load(cand_dir)
            base_results = await base.predict(session, sub_df)
            cand_results = await cand.predict(session, sub_df)
            assert len(base_results) == len(cand_results)
            deltas = []
            level_changes = 0
            for b, c in zip(base_results, cand_results):
                d = float(c.risk_score - b.risk_score)
                deltas.append(d)
                if b.risk_level != c.risk_level:
                    level_changes += 1
                per_claim_rows.append({
                    "file_id":     file_id,
                    "claim_id":    int(c.claim_id) if c.claim_id is not None else None,
                    "claim_number": c.claim_number,
                    "variant":     variant,
                    "subtype":     subtype,
                    "base_risk_score":  float(b.risk_score),
                    "cand_risk_score":  float(c.risk_score),
                    "base_risk_level":  b.risk_level,
                    "cand_risk_level":  c.risk_level,
                    "delta_score":      d,
                    "level_changed":    b.risk_level != c.risk_level,
                })
            arr = np.array(deltas)
            per_variant_summary[key] = {
                "n_claims":            int(len(arr)),
                "mean_abs_delta":      float(np.mean(np.abs(arr))) if len(arr) else 0.0,
                "p95_abs_delta":       float(np.percentile(np.abs(arr), 95)) if len(arr) else 0.0,
                "max_abs_delta":       float(np.max(np.abs(arr))) if len(arr) else 0.0,
                "n_risk_level_changes": int(level_changes),
            }
    return {
        "file_id": file_id,
        "status":  "ok",
        "per_variant": per_variant_summary,
        "claims":  per_claim_rows,
    }


async def main_async(args: argparse.Namespace) -> int:
    file_ids = [int(x) for x in args.file_ids.split(",") if x.strip()]
    print(f"CR-079 shadow parity — {len(file_ids)} file(s)", flush=True)

    all_rows: list[dict[str, Any]] = []
    per_file: list[dict[str, Any]] = []
    for fid in file_ids:
        try:
            res = await _shadow_one_file(fid)
        except Exception as exc:
            res = {"file_id": fid, "status": "error", "error": f"{type(exc).__name__}: {exc}"}
            print(f"[shadow file_id={fid}] FAILED: {res['error']}", flush=True)
        if res.get("status") == "ok":
            all_rows.extend(res["claims"])
        per_file.append({
            "file_id":     res["file_id"],
            "status":      res["status"],
            "per_variant": res.get("per_variant", {}),
            "n_claims":    len(res.get("claims", [])),
        })

    # Aggregate by variant across all files
    by_variant: dict[str, list[float]] = {}
    level_counts: dict[str, int] = {}
    for r in all_rows:
        key = VARIANT_KEYS.get((r["variant"], r["subtype"]))
        if key is None:
            continue
        by_variant.setdefault(key, []).append(r["delta_score"])
        if r["level_changed"]:
            level_counts[key] = level_counts.get(key, 0) + 1
    aggregate = {}
    for k, deltas in by_variant.items():
        arr = np.array(deltas)
        aggregate[k] = {
            "n_claims_total":   int(len(arr)),
            "mean_abs_delta":   float(np.mean(np.abs(arr))),
            "p95_abs_delta":    float(np.percentile(np.abs(arr), 95)),
            "max_abs_delta":    float(np.max(np.abs(arr))),
            "n_risk_level_changes": int(level_counts.get(k, 0)),
            "pct_risk_level_changes": round(
                100.0 * level_counts.get(k, 0) / len(arr), 2),
        }

    payload = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "file_ids":    file_ids,
        "per_file":    per_file,
        "aggregate":   aggregate,
        "claim_rows_count": len(all_rows),
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print()
    print(f"Wrote {OUT_PATH}")
    if aggregate:
        print()
        print("Per-variant aggregate:")
        for k, agg in aggregate.items():
            print(f"  {k:24s}  n={agg['n_claims_total']:>5d}  "
                  f"mean|Δ|={agg['mean_abs_delta']:.4f}  "
                  f"p95|Δ|={agg['p95_abs_delta']:.4f}  "
                  f"max|Δ|={agg['max_abs_delta']:.4f}  "
                  f"level_chg={agg['pct_risk_level_changes']:.1f}%")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--shadow", action="store_true",
                   help="Run candidate alongside production on the same claims.")
    p.add_argument("--file-ids", required=True,
                   help="Comma-separated edi_file_id list to score through both bundles.")
    args = p.parse_args()
    if not args.shadow:
        print("--shadow is the only supported mode in CR-079", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
