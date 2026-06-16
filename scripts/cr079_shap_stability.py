"""CR-079 — SHAP top-20 stability gate.

For each variant, compute SHAP feature importance against a fixed held-out
sample using both the production bundle and the candidate bundle, then report
the top-20 overlap percentage. Auto-promote requires ≥50 %; lower values
trigger manual review.

The "sample" is the held-out 15 % slice that the trainer already isolates,
so the SHAP attributions describe behaviour on data neither model has seen.

Usage:
    PYTHONPATH=src python scripts/cr079_shap_stability.py
    # writes scripts/cr079_shap_stability.json

Exit code is 0 even when a variant fails the overlap gate — the caller
(promotion script) is responsible for blocking. This script only reports.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.model_selection import train_test_split

from rcm.core.database import async_session
from rcm.features.builder import FeatureBuilder
from rcm.features.constants import XGBOOST_RANDOM_STATE
from rcm.features.dataset import load_training_corpus
from rcm.ml.artifacts import ModelArtifactBundle

logging.basicConfig(level=logging.WARNING)

VARIANTS: list[tuple[str, str, str]] = [
    ("837P", "healthcare", "837P_healthcare"),
    ("837D", "dental",     "837D_dental"),
    ("837I", "home_care",  "837I_home_care"),
]

BASE_ROOT = Path("artifacts/featurebuilder")
CAND_ROOT = Path("artifacts/featurebuilder_cr079_candidate")
OUT_PATH  = Path("scripts/cr079_shap_stability.json")

OVERLAP_GATE_PCT = 50.0
SAMPLE_CAP       = 1500   # cap SHAP samples per variant — fast + statistically stable


def _top_k_features(booster: xgb.Booster, X: pd.DataFrame, k: int = 20) -> list[str]:
    """Return the top-k feature names by mean |SHAP| across the sample."""
    dmat = xgb.DMatrix(X.to_numpy(), feature_names=list(X.columns))
    contribs = np.asarray(booster.predict(dmat, pred_contribs=True))
    # last column is the bias; drop
    contribs = contribs[:, :-1]
    importance = np.abs(contribs).mean(axis=0)
    order = np.argsort(importance)[::-1][:k]
    return [str(X.columns[j]) for j in order]


def _build_held_out_via_bundle_builder(
    bundle: ModelArtifactBundle, df_held: pd.DataFrame, session,
) -> pd.DataFrame:
    """Re-create an FB transform with the saved encoder + rarity_state. The
    bundle is enough to score new claims at predict time; we use the same
    pieces to render the held-out feature matrix."""
    builder = FeatureBuilder(
        service_variant=bundle.service_variant,
        claim_subtype=bundle.claim_subtype,
        encoder=bundle.encoder,
        rarity_state=bundle.rarity_state,
    )
    return asyncio.get_event_loop().run_until_complete(
        _maybe(builder.transform(session, df_held))
    )


async def _transform(builder: FeatureBuilder, session, df: pd.DataFrame) -> pd.DataFrame:
    return (await builder.transform(session, df)).astype("float32")


async def _eval_variant(variant: str, subtype: str, key: str) -> dict[str, Any]:
    base_dir = BASE_ROOT / key
    cand_dir = CAND_ROOT / key
    if not (base_dir / "model.json").exists():
        return {"status": "missing_baseline"}
    if not (cand_dir / "model.json").exists():
        return {"status": "missing_candidate"}

    print(f"[shap {key}] loading bundles...", flush=True)
    base = ModelArtifactBundle.load(base_dir)
    cand = ModelArtifactBundle.load(cand_dir)

    print(f"[shap {key}] loading + splitting corpus...", flush=True)
    async with async_session() as session:
        df = await load_training_corpus(
            session, service_variant=variant, claim_subtype=subtype,
        )
        y_all = df["denied"].astype(int).to_numpy()
        idx_all = np.arange(len(df))
        _, idx_temp = train_test_split(
            idx_all, test_size=0.30, random_state=XGBOOST_RANDOM_STATE, stratify=y_all,
        )
        _, idx_held = train_test_split(
            idx_temp, test_size=0.50, random_state=XGBOOST_RANDOM_STATE,
            stratify=y_all[idx_temp],
        )
        df_held = df.iloc[idx_held].copy()
        if len(df_held) > SAMPLE_CAP:
            df_held = df_held.sample(SAMPLE_CAP, random_state=XGBOOST_RANDOM_STATE)

        base_builder = FeatureBuilder(
            service_variant=variant, claim_subtype=subtype,
            encoder=base.encoder, rarity_state=base.rarity_state,
        )
        cand_builder = FeatureBuilder(
            service_variant=variant, claim_subtype=subtype,
            encoder=cand.encoder, rarity_state=cand.rarity_state,
        )
        X_base = await _transform(base_builder, session, df_held)
        X_cand = await _transform(cand_builder, session, df_held)

    print(f"[shap {key}] computing SHAP top-20 (n={len(X_base)})...", flush=True)
    base_top = _top_k_features(base.booster, X_base, k=20)
    cand_top = _top_k_features(cand.booster, X_cand, k=20)

    overlap = sorted(set(base_top) & set(cand_top))
    only_base = sorted(set(base_top) - set(cand_top))
    only_cand = sorted(set(cand_top) - set(base_top))
    overlap_pct = 100.0 * len(overlap) / 20.0

    verdict = "AUTO_PROMOTE" if overlap_pct >= OVERLAP_GATE_PCT else "MANUAL_REVIEW"
    print(f"[shap {key}] overlap={overlap_pct:.0f}% — {verdict}", flush=True)

    return {
        "status":          "ok",
        "service_variant": variant,
        "claim_subtype":   subtype,
        "sample_n":        int(len(X_base)),
        "baseline_model_version":  base.model_version,
        "candidate_model_version": cand.model_version,
        "top20_baseline":  base_top,
        "top20_candidate": cand_top,
        "overlap_count":   len(overlap),
        "overlap_pct":     round(overlap_pct, 1),
        "overlap_features":  overlap,
        "only_in_baseline":  only_base,
        "only_in_candidate": only_cand,
        "gate":            OVERLAP_GATE_PCT,
        "verdict":         verdict,
    }


async def main_async() -> int:
    out: dict[str, Any] = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "gate_pct":    OVERLAP_GATE_PCT,
        "variants":    {},
    }
    print("CR-079 SHAP top-20 stability")
    print("=" * 64)
    any_review = False
    for variant, subtype, key in VARIANTS:
        try:
            row = await _eval_variant(variant, subtype, key)
        except Exception as exc:
            row = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
            print(f"[shap {key}] FAILED: {row['error']}", flush=True)
        out["variants"][key] = row
        if row.get("verdict") == "MANUAL_REVIEW":
            any_review = True

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print()
    print(f"Wrote {OUT_PATH}")
    if any_review:
        print("At least one variant fell below the 50% gate — manual review required.")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
