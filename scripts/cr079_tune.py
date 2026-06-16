"""CR-079 — Optuna hyperparameter tuning for FeatureBuilder variants.

Per-variant studies. Shared search space (per Phase 4 AIR Part C). TPE sampler
+ MedianPruner. Re-uses the production training pipeline pieces — corpus loader,
FeatureBuilder, isotonic calibrator, precision-floor threshold selection — so a
tuned candidate bundle is byte-format-identical to a production bundle.

Pipeline:

    1. Load corpus via load_training_corpus(variant, subtype)
    2. Stratified 70/15/15 split (seed=XGBOOST_RANDOM_STATE) — same as trainer.py
    3. FB.fit_transform on TRAIN; FB.transform on val + held-out — once per variant
    4. Optuna study: each trial samples params, runs StratifiedKFold inside the
       train slice, reports mean validation-slice PR-AUC. MedianPruner kills
       under-performing trials early.
    5. Best trial → re-fit on full TRAIN, isotonic on VAL, threshold via
       precision-floor on VAL, evaluate on HELD-OUT, save bundle to
       artifacts/featurebuilder_cr079_candidate/<variant>/
    6. Write per-variant + aggregate summaries to scripts/cr079_tune_summary.json

CLI flags:
    --smoke      (3 trials per variant)
    --balanced   (60 trials per variant, ~5-8h on 837P)
    --thorough   (150 trials per variant)
    --variant    optionally restrict to one variant key (837P_healthcare, etc.)
    --resume     reuse the SQLite study DB if present (Optuna handles this)

Run:
    PYTHONPATH=src python scripts/cr079_tune.py --smoke
    PYTHONPATH=src python scripts/cr079_tune.py --balanced
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    brier_score_loss,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedKFold, train_test_split
from xgboost import XGBClassifier

import optuna
from optuna.pruners import MedianPruner
from optuna.samplers import TPESampler

from rcm.core.database import async_session
from rcm.features.builder import FeatureBuilder
from rcm.features.constants import (
    FEATURE_ENGINEERING_VERSION,
    PRECISION_FLOOR,
    XGBOOST_RANDOM_STATE,
)
from rcm.features.dataset import load_training_corpus
from rcm.ml.artifacts import ModelArtifactBundle

warnings.filterwarnings("ignore", category=UserWarning)
optuna.logging.set_verbosity(optuna.logging.WARNING)
logging.basicConfig(level=logging.WARNING)

VARIANTS: list[tuple[str, str, str]] = [
    ("837P", "healthcare", "837P_healthcare"),
    ("837D", "dental",     "837D_dental"),
    ("837I", "home_care",  "837I_home_care"),
]

ARTIFACT_CANDIDATE_ROOT = Path("artifacts/featurebuilder_cr079_candidate")
STUDY_ROOT              = Path("artifacts/optuna")
SUMMARY_OUT             = Path("scripts/cr079_tune_summary.json")


# ---------------------------------------------------------------------------
# Search space — Phase 4 AIR Part C. Locked; do not widen without amending AIR.
# ---------------------------------------------------------------------------

def suggest_params(trial: optuna.Trial, auto_spw: float) -> dict[str, Any]:
    """Sample one trial's hyperparameter dict from the AIR-approved space."""
    return {
        "n_estimators":     trial.suggest_int   ("n_estimators", 100, 800, step=50),
        "max_depth":        trial.suggest_int   ("max_depth", 3, 9),
        "learning_rate":    trial.suggest_float ("learning_rate", 0.01, 0.30, log=True),
        "min_child_weight": trial.suggest_int   ("min_child_weight", 1, 20, log=True),
        "gamma":            trial.suggest_float ("gamma", 0.0, 5.0),
        "subsample":        trial.suggest_float ("subsample", 0.5, 1.0),
        "colsample_bytree": trial.suggest_float ("colsample_bytree", 0.4, 1.0),
        "colsample_bylevel":trial.suggest_float ("colsample_bylevel", 0.4, 1.0),
        "reg_alpha":        trial.suggest_float ("reg_alpha", 1e-8, 10.0, log=True),
        "reg_lambda":       trial.suggest_float ("reg_lambda", 1e-8, 10.0, log=True),
        "scale_pos_weight": trial.suggest_float ("scale_pos_weight",
                                                 max(0.05, 0.5 * auto_spw),
                                                 max(0.10, 2.0 * auto_spw)),
        "max_delta_step":   trial.suggest_int   ("max_delta_step", 0, 10),
    }


def build_xgb(params: dict[str, Any]) -> XGBClassifier:
    """Trainer-style XGBoost — frozen tree_method / eval_metric / random_state."""
    return XGBClassifier(
        tree_method="hist",
        eval_metric="logloss",
        random_state=XGBOOST_RANDOM_STATE,
        n_jobs=1,
        verbosity=0,
        **params,
    )


# ---------------------------------------------------------------------------
# Per-variant data preparation — runs ONCE; trials reuse the cached matrices
# ---------------------------------------------------------------------------

class VariantData:
    """Cached, immutable per-variant data used by every trial in a study."""

    def __init__(self, variant: str, subtype: str,
                 df_train: pd.DataFrame, df_val: pd.DataFrame, df_held: pd.DataFrame,
                 X_train: pd.DataFrame, X_val: pd.DataFrame, X_held: pd.DataFrame,
                 y_train: np.ndarray, y_val: np.ndarray, y_held: np.ndarray,
                 builder: FeatureBuilder):
        self.variant, self.subtype = variant, subtype
        self.df_train, self.df_val, self.df_held = df_train, df_val, df_held
        self.X_train, self.X_val, self.X_held = X_train, X_val, X_held
        self.y_train, self.y_val, self.y_held = y_train, y_val, y_held
        self.builder = builder
        n_pos = int(y_train.sum())
        n_neg = int((1 - y_train).sum())
        self.auto_spw = n_neg / max(1, n_pos)
        self.cv_splits = min(5, max(2, n_pos // 5), max(2, n_neg // 5))


async def prepare_variant(variant: str, subtype: str) -> VariantData:
    """Pull the corpus, split, fit FB once, transform val + held."""
    print(f"[prep {variant}/{subtype}] loading corpus...", flush=True)
    t0 = time.perf_counter()
    async with async_session() as session:
        df = await load_training_corpus(
            session, service_variant=variant, claim_subtype=subtype,
        )
        if df.empty:
            raise RuntimeError(f"empty corpus for {variant}/{subtype}")

        y_all = df["denied"].astype(int).to_numpy()
        idx_all = np.arange(len(df))
        idx_train, idx_temp = train_test_split(
            idx_all, test_size=0.30, random_state=XGBOOST_RANDOM_STATE, stratify=y_all,
        )
        idx_val, idx_held = train_test_split(
            idx_temp, test_size=0.50, random_state=XGBOOST_RANDOM_STATE,
            stratify=y_all[idx_temp],
        )
        df_train = df.iloc[idx_train].copy()
        df_val   = df.iloc[idx_val].copy()
        df_held  = df.iloc[idx_held].copy()
        y_train, y_val, y_held = y_all[idx_train], y_all[idx_val], y_all[idx_held]

        print(f"[prep {variant}/{subtype}] fit_transform on TRAIN ({len(df_train)} rows)...", flush=True)
        builder = FeatureBuilder(service_variant=variant, claim_subtype=subtype)
        artifacts = await builder.fit_transform(
            session, df_train, pd.Series(y_train, index=df_train.index),
        )
        X_train = artifacts.features.astype("float32")
        X_val   = (await builder.transform(session, df_val)).astype("float32")
        X_held  = (await builder.transform(session, df_held)).astype("float32")

    print(f"[prep {variant}/{subtype}] done in {time.perf_counter()-t0:.1f}s "
          f"(rows train/val/held = {len(df_train)}/{len(df_val)}/{len(df_held)})",
          flush=True)

    return VariantData(
        variant=variant, subtype=subtype,
        df_train=df_train, df_val=df_val, df_held=df_held,
        X_train=X_train, X_val=X_val, X_held=X_held,
        y_train=y_train, y_val=y_val, y_held=y_held,
        builder=builder,
    )


# ---------------------------------------------------------------------------
# Optuna objective — cross-validated PR-AUC on the TRAIN slice
# ---------------------------------------------------------------------------

def make_objective(data: VariantData):
    Xtr_np = data.X_train.to_numpy()
    Xv_np  = data.X_val.to_numpy()
    cv = StratifiedKFold(
        n_splits=data.cv_splits, shuffle=True, random_state=XGBOOST_RANDOM_STATE,
    )

    def objective(trial: optuna.Trial) -> float:
        params = suggest_params(trial, data.auto_spw)
        # Cross-validated PR-AUC inside the TRAIN slice. We never look at
        # held-out during tuning; that's evaluated once for the best trial.
        fold_scores: list[float] = []
        for i, (tr_idx, va_idx) in enumerate(cv.split(Xtr_np, data.y_train)):
            model = build_xgb(params)
            model.fit(Xtr_np[tr_idx], data.y_train[tr_idx])
            p = model.predict_proba(Xtr_np[va_idx])[:, 1]
            score = float(average_precision_score(data.y_train[va_idx], p))
            fold_scores.append(score)
            trial.report(score, step=i)
            if trial.should_prune():
                raise optuna.TrialPruned()
        # Composite: 0.7 * train-CV PR-AUC + 0.3 * single-fit val PR-AUC.
        # Val component grounds the objective in the same slice the trainer
        # uses for calibration + threshold selection.
        val_model = build_xgb(params)
        val_model.fit(Xtr_np, data.y_train)
        val_score = float(average_precision_score(
            data.y_val, val_model.predict_proba(Xv_np)[:, 1],
        ))
        return 0.7 * float(np.mean(fold_scores)) + 0.3 * val_score

    return objective


# ---------------------------------------------------------------------------
# Final-fit + bundle persistence (mirrors trainer.py exactly)
# ---------------------------------------------------------------------------

def fit_and_save_candidate(
    data: VariantData, params: dict[str, Any], out_dir: Path,
) -> dict[str, Any]:
    Xtr = data.X_train
    Xv  = data.X_val
    Xh  = data.X_held

    model = build_xgb(params)
    model.fit(Xtr, data.y_train)
    booster = model.get_booster()
    assert booster.feature_names == list(Xtr.columns), \
        "booster.feature_names drift — refuse to save candidate"

    val_raw  = model.predict_proba(Xv)[:, 1]
    held_raw = model.predict_proba(Xh)[:, 1]

    # Calibration on validation (trainer.py mirror)
    calibrator: IsotonicRegression | None = IsotonicRegression(
        out_of_bounds="clip", y_min=0.001, y_max=0.999,
    )
    calibrator.fit(val_raw, data.y_val)
    pts = np.linspace(0, 1, 20)
    if not bool(np.all(np.diff(calibrator.transform(pts)) >= -1e-9)):
        calibrator = None
        val_cal = val_raw
        held_cal = held_raw
    else:
        val_cal = calibrator.transform(val_raw)
        held_cal = calibrator.transform(held_raw)

    threshold = _select_threshold(val_cal, data.y_val, PRECISION_FLOOR)

    def block(y: np.ndarray, p_cal: np.ndarray, p_raw: np.ndarray) -> dict:
        if len(set(y.tolist())) < 2:
            return {"degenerate": True, "n": int(len(y))}
        yp = (p_cal >= threshold).astype(int)
        return {
            "n": int(len(y)), "prevalence": float(y.mean()),
            "accuracy_at_threshold": float(accuracy_score(y, yp)),
            "precision_at_threshold": float(precision_score(y, yp, zero_division=0)),
            "recall_at_threshold":   float(recall_score(y, yp, zero_division=0)),
            "f1_at_threshold":       float(f1_score(y, yp, zero_division=0)),
            "roc_auc":               float(roc_auc_score(y, p_cal)),
            "pr_auc":                float(average_precision_score(y, p_cal)),
            "brier_uncalibrated":    float(brier_score_loss(y, p_raw)),
            "brier_calibrated":      float(brier_score_loss(y, p_cal)),
            "positive_rate":         float(yp.mean()),
        }

    val_block  = block(data.y_val,  val_cal, val_raw)
    held_block = block(data.y_held, held_cal, held_raw)

    metrics = {
        "n_training_rows":   int(len(data.y_train)),
        "n_validation_rows": int(len(data.y_val)),
        "n_held_out_rows":   int(len(data.y_held)),
        "prevalence": float(np.concatenate([data.y_train, data.y_val, data.y_held]).mean()),
        "accuracy_at_threshold": held_block.get("accuracy_at_threshold"),
        "precision_at_threshold": held_block.get("precision_at_threshold"),
        "recall_at_threshold":    held_block.get("recall_at_threshold"),
        "f1_at_threshold":        held_block.get("f1_at_threshold"),
        "roc_auc":                held_block.get("roc_auc"),
        "pr_auc":                 held_block.get("pr_auc"),
        "brier_uncalibrated":     held_block.get("brier_uncalibrated"),
        "brier_calibrated":       held_block.get("brier_calibrated"),
        "positive_rate":          held_block.get("positive_rate"),
        "validation":             val_block,
        "held_out":               held_block,
        "hyperparameters":        params,
    }

    fb_version = (
        "v1.fb.tuned." + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        + f".{data.variant}_{data.subtype}"
    )

    bundle = ModelArtifactBundle(
        booster=booster, calibrator=calibrator,
        encoder=data.builder.encoder, rarity_state=data.builder.rarity_state,
        feature_columns=list(Xtr.columns),
        service_variant=data.variant, claim_subtype=data.subtype,
        decision_threshold=float(threshold),
        feature_engineering_version=FEATURE_ENGINEERING_VERSION,
        model_version=fb_version,
        calibrator_version=("isotonic_v1" if calibrator is not None else None),
        metrics=metrics,
        training_size=int(len(data.y_train)),
        training_prevalence=float(data.y_train.mean()),
    )
    bundle.save(out_dir)
    return metrics


def _select_threshold(scores: np.ndarray, y: np.ndarray, precision_floor: float) -> float:
    """Same precision-floor rule as trainer._select_threshold (frozen by AIR)."""
    candidates = np.arange(0.01, 0.99, 0.01)
    best_threshold: float | None = None
    best_recall = -1.0
    max_precision = -1.0
    max_p_threshold = 0.5
    for t in candidates:
        yp = (scores >= t).astype(int)
        tp = int(((yp == 1) & (y == 1)).sum())
        fp = int(((yp == 1) & (y == 0)).sum())
        fn = int(((yp == 0) & (y == 1)).sum())
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        if precision > max_precision:
            max_precision = precision
            max_p_threshold = float(t)
        if precision >= precision_floor and recall > best_recall:
            best_recall = recall
            best_threshold = float(t)
    return best_threshold if best_threshold is not None else max_p_threshold


# ---------------------------------------------------------------------------
# Per-variant study orchestration
# ---------------------------------------------------------------------------

async def run_variant_study(
    variant: str, subtype: str, key: str,
    n_trials: int, warm_up: int,
) -> dict[str, Any]:
    print()
    print(f"[study {key}] starting — {n_trials} trials (+{warm_up} warm-up)", flush=True)
    data = await prepare_variant(variant, subtype)

    STUDY_ROOT.mkdir(parents=True, exist_ok=True)
    storage_url = f"sqlite:///{(STUDY_ROOT / f'cr079_{key}.db').as_posix()}"
    sampler = TPESampler(seed=XGBOOST_RANDOM_STATE, n_startup_trials=warm_up)
    pruner = MedianPruner(n_startup_trials=warm_up, n_warmup_steps=1)

    study = optuna.create_study(
        study_name=f"cr079_{key}",
        storage=storage_url,
        direction="maximize",
        sampler=sampler,
        pruner=pruner,
        load_if_exists=True,
    )

    t0 = time.perf_counter()
    study.optimize(
        make_objective(data),
        n_trials=n_trials,
        gc_after_trial=True,
        show_progress_bar=False,
    )
    study_seconds = round(time.perf_counter() - t0, 1)

    best = study.best_trial
    print(f"[study {key}] best trial #{best.number}: value={best.value:.4f} "
          f"params={ {k: (round(v, 4) if isinstance(v, float) else v) for k, v in best.params.items()} }",
          flush=True)

    out_dir = ARTIFACT_CANDIDATE_ROOT / key
    print(f"[study {key}] writing candidate to {out_dir}", flush=True)
    metrics = fit_and_save_candidate(data, best.params, out_dir)

    n_pruned   = sum(1 for t in study.trials if t.state == optuna.trial.TrialState.PRUNED)
    n_complete = sum(1 for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE)
    n_fail     = sum(1 for t in study.trials if t.state == optuna.trial.TrialState.FAIL)

    return {
        "service_variant": variant,
        "claim_subtype":   subtype,
        "best_trial_number": int(best.number),
        "best_objective":  float(best.value),
        "best_params":     best.params,
        "trial_counts": {
            "total":    len(study.trials),
            "complete": n_complete,
            "pruned":   n_pruned,
            "failed":   n_fail,
        },
        "study_seconds":   study_seconds,
        "candidate_dir":   str(out_dir),
        "candidate_metrics": metrics,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

PLAN_TRIALS = {"smoke": 3, "balanced": 60, "thorough": 150}
PLAN_WARMUP = {"smoke": 1, "balanced": 10, "thorough": 20}


async def main_async(args: argparse.Namespace) -> int:
    if args.plan not in PLAN_TRIALS:
        print(f"unknown plan: {args.plan}", flush=True); return 2
    n_trials = PLAN_TRIALS[args.plan]
    warm_up  = PLAN_WARMUP[args.plan]

    variants = VARIANTS
    if args.variant:
        variants = [v for v in VARIANTS if v[2] == args.variant]
        if not variants:
            print(f"unknown variant: {args.variant}", flush=True); return 2

    print(f"CR-079 tuning — plan={args.plan} trials/variant={n_trials} "
          f"warmup={warm_up} variants={[v[2] for v in variants]}", flush=True)

    ARTIFACT_CANDIDATE_ROOT.mkdir(parents=True, exist_ok=True)

    summaries: dict[str, Any] = {}
    t0 = time.perf_counter()
    for variant, subtype, key in variants:
        try:
            summaries[key] = await run_variant_study(
                variant, subtype, key, n_trials=n_trials, warm_up=warm_up,
            )
        except Exception as exc:
            print(f"[study {key}] FAILED: {type(exc).__name__}: {exc}", flush=True)
            summaries[key] = {"status": "failed", "error": str(exc)}

    summary = {
        "started_at":   datetime.now(timezone.utc).isoformat(),
        "plan":         args.plan,
        "trials_per_variant": n_trials,
        "warm_up_trials":     warm_up,
        "total_seconds": round(time.perf_counter() - t0, 1),
        "variants": summaries,
    }
    SUMMARY_OUT.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_OUT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print()
    print(f"Wrote {SUMMARY_OUT}")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--smoke",    dest="plan", action="store_const", const="smoke")
    p.add_argument("--balanced", dest="plan", action="store_const", const="balanced")
    p.add_argument("--thorough", dest="plan", action="store_const", const="thorough")
    p.add_argument("--variant",  default=None,
                   help="restrict to one variant key (e.g. 837P_healthcare)")
    p.set_defaults(plan="smoke")
    args = p.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
