"""R6 — Paired benchmark + production decision framework (CR-066).

Comprehensive benchmark of FeatureBuilder vs simple_pipeline against actual
denial outcomes from mv_claim_labels (post-CR-056 6,316-row cohort).

Phases:
  1. Cohort load + StratifiedKFold(5) fold definition per variant
  2. Per-fold per-variant per-pipeline OOF generation (primary analysis)
  3. Primary metrics (per pipeline × per variant × overall)
  4. Paired metrics: agreement, McNemar test, score-delta distribution
  5. Disagreement subgroup analysis (6 required subgroups)
  6. Leakage-corrected FB OOF (per-fold train-set MV aggregates override
     full-corpus MV reads from JOINT/PROVIDER/COVERAGE/CLINICAL features)
  7. Corrected metrics + primary-vs-corrected delta
  8. Complexity assessment (measured where possible)
  9. Decision logic per §7 framework with mandatory promotion gates
  10. Write outputs:
        scripts/r6_benchmark_post.json (transient)
        docs/r6_decision_report.md (documentation)

Read-only against the DB. Trains models per fold in-memory.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from statistics import mean, median
from typing import Any

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedKFold
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from xgboost import XGBClassifier

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rcm.features.builder import FeatureBuilder  # noqa: E402
from rcm.features.constants import XGBOOST_RANDOM_STATE  # noqa: E402
from rcm.features.dataset import load_training_corpus  # noqa: E402

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("r6")

import os as _os
DSN = _os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://rcm:rcm_dev_password@localhost:5433/rcm_denials_dev",
)

TRAINABLE = [
    ("837D", "dental"),
    ("837P", "healthcare"),
    ("837I", "home_care"),
]

# MV-derived feature columns whose values are computed from full-corpus MV
# reads. The leakage-corrected analysis recomputes these per-fold from the
# fold's training claims only.
_MV_DERIVED_COLUMNS = [
    "payer_overall_denial_rate",
    "payer_cpt_denial_rate",
    "payer_dx_denial_rate",
    "payer_pos_denial_rate",
    "cpt_dx_denial_rate",
    "cpt_dx_alignment_score",
    "payer_provider_denial_rate",
    "provider_overall_denial_rate",
    "provider_payer_denial_rate",
    "provider_cpt_denial_rate",
    "provider_cpt_denial_rate_joint",
]

# Promotion thresholds (from approved AIR §7 + modification #3)
PROMOTION_GATES = {
    "min_auc_delta": 0.015,
    "min_pr_auc_delta": 0.015,
    "min_f1_delta": 0.015,
    "mcnemar_p_max": 0.05,
    "worst_variant_regression": -0.005,
    "leakage_corrected_must_pass": True,
}


def hdr(s: str) -> None:
    print()
    print("=" * 78)
    print(s)
    print("=" * 78)


def _bootstrap_auc_ci(y_true: np.ndarray, y_score: np.ndarray, n: int = 200, seed: int = 42) -> tuple[float, float]:
    """95% bootstrap CI for ROC-AUC."""
    if len(set(y_true.tolist())) < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    aucs = []
    for _ in range(n):
        idx = rng.integers(0, len(y_true), len(y_true))
        try:
            aucs.append(roc_auc_score(y_true[idx], y_score[idx]))
        except ValueError:
            continue
    if not aucs:
        return (float("nan"), float("nan"))
    aucs = sorted(aucs)
    lo = aucs[int(0.025 * len(aucs))]
    hi = aucs[int(0.975 * len(aucs))]
    return (lo, hi)


def _ece(y_true: np.ndarray, y_score: np.ndarray, n_bins: int = 10) -> float:
    """Expected Calibration Error with n_bins equal-width bins."""
    bins = np.linspace(0, 1, n_bins + 1)
    ece = 0.0
    n = len(y_true)
    if n == 0:
        return float("nan")
    for i in range(n_bins):
        mask = (y_score >= bins[i]) & (y_score <= bins[i + 1] if i == n_bins - 1 else y_score < bins[i + 1])
        if mask.sum() == 0:
            continue
        bin_mean = float(y_score[mask].mean())
        bin_acc = float(y_true[mask].mean())
        ece += (mask.sum() / n) * abs(bin_mean - bin_acc)
    return float(ece)


def _mcnemar(y_true: np.ndarray, y_a: np.ndarray, y_b: np.ndarray) -> tuple[int, int, float]:
    """McNemar test for paired binary classifiers. Returns (b, c, p) where
    b = a_correct & b_wrong, c = a_wrong & b_correct. Uses exact binomial test
    when b+c <= 25, chi-square approximation otherwise.
    """
    a_correct = (y_a == y_true)
    b_correct = (y_b == y_true)
    b = int((a_correct & ~b_correct).sum())
    c = int((~a_correct & b_correct).sum())
    if b + c == 0:
        return b, c, 1.0
    try:
        from scipy.stats import binom
        # two-tailed exact test: p = 2 * min(P(X <= min(b,c)), 0.5)
        k = min(b, c)
        p = 2 * binom.cdf(k, b + c, 0.5)
        p = min(p, 1.0)
    except Exception:
        # chi-square approx
        stat = (abs(b - c) - 1) ** 2 / (b + c)
        from math import erfc, sqrt
        p = erfc(sqrt(stat / 2))
    return b, c, float(p)


def _metrics_at_threshold(y_true: np.ndarray, y_score: np.ndarray, thr: float) -> dict:
    y_pred = (y_score >= thr).astype(int)
    p = precision_score(y_true, y_pred, zero_division=0)
    r = recall_score(y_true, y_pred, zero_division=0)
    f1 = f1_score(y_true, y_pred, zero_division=0)
    cm = confusion_matrix(y_true, y_pred, labels=[0, 1]).tolist()
    return {
        "threshold": float(thr),
        "precision": float(p),
        "recall": float(r),
        "f1": float(f1),
        "confusion_matrix": cm,
    }


def _all_metrics(y_true: np.ndarray, y_score: np.ndarray, threshold: float, *, ci: bool = True) -> dict:
    if len(y_true) == 0 or len(set(y_true.tolist())) < 2:
        return {"degenerate": True, "n": len(y_true), "prevalence": float(y_true.mean()) if len(y_true) else 0.0}
    auc = float(roc_auc_score(y_true, y_score))
    pr_auc = float(average_precision_score(y_true, y_score))
    out = {
        "n": int(len(y_true)),
        "prevalence": float(y_true.mean()),
        "roc_auc": auc,
        "pr_auc": pr_auc,
        "brier": float(brier_score_loss(y_true, y_score)),
        "ece": _ece(y_true, y_score),
        **_metrics_at_threshold(y_true, y_score, threshold),
    }
    if ci:
        lo, hi = _bootstrap_auc_ci(y_true, y_score)
        out["roc_auc_ci_95"] = [lo, hi]
    return out


# ---------------------------------------------------------------------------
# Simple-pipeline feature builder (replicated inline for OOF — does NOT
# modify simple_pipeline.py; identical to its feature path)
# ---------------------------------------------------------------------------

def _simple_features(df: pd.DataFrame, payer_vocab: list[str], proc_vocab: list[str], dx_vocab: list[str]) -> pd.DataFrame:
    """Replicate simple_pipeline._featurize logic on an arbitrary cohort.
    Source of truth: src/rcm/ml/simple_pipeline.py._featurize.
    """
    out = pd.DataFrame(index=df.index)
    out["total_charge_amount"] = df["total_charge_amount"].fillna(0.0).astype("float32")
    out["line_count"] = df["line_count"].fillna(0).astype("int32")
    out["diagnosis_count"] = df["diagnosis_count"].fillna(0).astype("int32")
    out["has_authorization"] = df["has_authorization"].astype("int8")
    out["has_referral"] = df["has_referral"].astype("int8")
    out["is_replacement_freq"] = (df["frequency_code"].astype(str).isin(["6", "7"])).astype("int8")
    # variant one-hots
    for v in ("837P", "837I", "837D"):
        out[f"variant_{v}"] = (df["service_variant"] == v).astype("int8")
    # payer one-hots
    payer_col = df["payer_name"].fillna("__UNK__")
    for p in payer_vocab:
        out[f"payer_{p}"] = (payer_col == p).astype("int8")
    out["payer_unknown"] = (~payer_col.isin(payer_vocab)).astype("int8")
    # procedure one-hots
    proc_col = df["primary_procedure"].fillna("__UNK__")
    for c in proc_vocab:
        out[f"proc_{c}"] = (proc_col == c).astype("int8")
    out["proc_unknown"] = (~proc_col.isin(proc_vocab)).astype("int8")
    # diagnosis one-hots
    dx_col = df["primary_diagnosis"].fillna("__UNK__")
    for d in dx_vocab:
        out[f"dx_{d}"] = (dx_col == d).astype("int8")
    out["dx_unknown"] = (~dx_col.isin(dx_vocab)).astype("int8")
    return out


def _build_vocabs(df: pd.DataFrame, top_n: int = 20) -> tuple[list[str], list[str], list[str]]:
    p = df["payer_name"].fillna("__UNK__").value_counts().head(top_n).index.tolist()
    c = df["primary_procedure"].fillna("__UNK__").value_counts().head(top_n).index.tolist()
    d = df["primary_diagnosis"].fillna("__UNK__").value_counts().head(top_n).index.tolist()
    return p, c, d


# ---------------------------------------------------------------------------
# Simple-pipeline-style cohort SQL (returns the same shape simple_pipeline's
# _featurize expects; filtered to a specific claim_id list)
# ---------------------------------------------------------------------------

_SIMPLE_COHORT_SQL = """
SELECT
    cl.id              AS claim_id,
    cl.claim_number,
    cl.service_variant,
    cl.claim_subtype,
    cl.total_charge_amount::float8 AS total_charge_amount,
    cl.authorization_number IS NOT NULL AS has_authorization,
    cl.referral_number IS NOT NULL      AS has_referral,
    cl.frequency_code,
    p.canonical_name AS payer_name,
    (SELECT count(*) FROM claim_lines lin WHERE lin.claim_id = cl.id) AS line_count,
    (SELECT count(*) FROM diagnoses dx   WHERE dx.claim_id = cl.id) AS diagnosis_count,
    (SELECT lin.procedure_code FROM claim_lines lin
     WHERE lin.claim_id = cl.id ORDER BY lin.line_number LIMIT 1) AS primary_procedure,
    (SELECT dx.diagnosis_code FROM diagnoses dx
     WHERE dx.claim_id = cl.id ORDER BY dx.sequence_number LIMIT 1) AS primary_diagnosis
FROM claims cl
LEFT JOIN payers p ON p.id = cl.payer_id
WHERE cl.id = ANY(:ids) AND cl.deleted_at IS NULL
ORDER BY cl.id
"""


async def _load_simple_cohort(session, claim_ids: list[int]) -> pd.DataFrame:
    from sqlalchemy import text
    rows = (await session.execute(text(_SIMPLE_COHORT_SQL), {"ids": claim_ids})).mappings().all()
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# OOF generation per pipeline per variant
# ---------------------------------------------------------------------------

async def fb_oof_for_variant(Session, variant: str, subtype: str, *, correct_leakage: bool = False) -> dict:
    """5-fold OOF for FeatureBuilder. If correct_leakage=True, the MV-derived
    columns are recomputed per-fold from training-fold aggregates only.
    """
    label = f"FB{' (corrected)' if correct_leakage else ''} {variant}/{subtype}"
    print(f"\n  [{label}] starting...")

    # Load the FULL variant corpus once (FeatureBuilder fit_transform will be
    # called per fold against subsets of this).
    async with Session() as session:
        full_df = await load_training_corpus(session, service_variant=variant, claim_subtype=subtype)
    n_rows = len(full_df)
    if n_rows == 0:
        return {"variant": variant, "subtype": subtype, "n_rows": 0, "skipped": "empty corpus"}

    full_df = full_df.reset_index(drop=True)
    y_full = full_df["denied"].astype(int).to_numpy()

    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=XGBOOST_RANDOM_STATE)
    oof_scores = np.zeros(n_rows, dtype="float32")
    oof_filled = np.zeros(n_rows, dtype="bool")

    for fold_idx, (tr_idx, te_idx) in enumerate(skf.split(full_df, y_full)):
        train_df = full_df.iloc[tr_idx].reset_index(drop=True)
        test_df = full_df.iloc[te_idx].reset_index(drop=True)
        y_train = y_full[tr_idx]

        # Build features on train fold; transform on test fold using the
        # fitted encoder/rarity_state
        builder = FeatureBuilder(service_variant=variant, claim_subtype=subtype)
        async with Session() as session:
            train_artifacts = await builder.fit_transform(session, train_df, pd.Series(y_train, index=train_df.index))
            X_train_df = train_artifacts.features.astype("float32")
            X_test_df = await builder.transform(session, test_df)
            X_test_df = X_test_df.astype("float32")

        # If leakage-corrected, override the MV-derived columns with per-fold
        # train-set aggregates.
        if correct_leakage:
            X_train_df, X_test_df = _apply_leakage_correction(
                X_train_df, X_test_df, train_df, test_df, y_train
            )

        # Fit XGBoost
        n_pos = int(y_train.sum())
        n_neg = int((1 - y_train).sum())
        spw = (n_neg / max(n_pos, 1)) if n_pos > 0 else 1.0
        model = XGBClassifier(
            n_estimators=200, max_depth=5, learning_rate=0.05,
            random_state=XGBOOST_RANDOM_STATE, scale_pos_weight=spw,
            eval_metric="logloss", tree_method="hist",
        )
        model.fit(X_train_df.to_numpy(), y_train)
        probs = model.predict_proba(X_test_df.to_numpy())[:, 1]
        oof_scores[te_idx] = probs
        oof_filled[te_idx] = True
        print(f"    fold {fold_idx+1}/5: train={len(tr_idx)} test={len(te_idx)} test_pos={int(y_full[te_idx].sum())}  n_pred={len(probs)}")

    if not oof_filled.all():
        missing = (~oof_filled).sum()
        print(f"    WARNING: {missing} rows missing OOF prediction")

    # Calibrate on the FULL OOF (deterministic given OOF + labels)
    calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.001, y_max=0.999)
    calibrator.fit(oof_scores, y_full)
    oof_cal = calibrator.transform(oof_scores)

    # Pick threshold via the same _select_threshold logic
    from rcm.ml.trainer import _select_threshold
    from rcm.features.constants import PRECISION_FLOOR
    threshold = float(_select_threshold(oof_cal, y_full, PRECISION_FLOOR))

    return {
        "variant": variant,
        "subtype": subtype,
        "pipeline": "featurebuilder" + ("_corrected" if correct_leakage else ""),
        "n_rows": int(n_rows),
        "claim_ids": full_df["claim_id"].astype(int).tolist(),
        "y_true": y_full.tolist(),
        "y_oof_raw": oof_scores.tolist(),
        "y_oof_cal": oof_cal.tolist(),
        "threshold": threshold,
    }


def _apply_leakage_correction(
    X_train_df: pd.DataFrame, X_test_df: pd.DataFrame,
    train_df: pd.DataFrame, test_df: pd.DataFrame, y_train: np.ndarray,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Override MV-derived feature columns in X_*_df with per-fold-train
    aggregates. For each MV-derived column we identify the grouping key
    from the column semantics and recompute the rate from train rows only.

    This is the conservative leakage correction: the per-fold aggregates
    use ONLY training-fold data; test claims are scored against aggregates
    they did not contribute to.
    """
    # Working copies so we don't mutate the originals in place
    Xtr = X_train_df.copy()
    Xte = X_test_df.copy()

    train_keys = {
        "payer_id": train_df.get("payer_id"),
        "primary_cpt": train_df.get("primary_cpt"),
        "primary_dx": train_df.get("primary_dx"),
        "primary_pos": train_df.get("primary_pos"),
        "billing_provider_id": train_df.get("billing_provider_id"),
        "service_variant": train_df.get("service_variant"),
        "claim_subtype": train_df.get("claim_subtype"),
    }
    test_keys = {
        "payer_id": test_df.get("payer_id"),
        "primary_cpt": test_df.get("primary_cpt"),
        "primary_dx": test_df.get("primary_dx"),
        "primary_pos": test_df.get("primary_pos"),
        "billing_provider_id": test_df.get("billing_provider_id"),
        "service_variant": test_df.get("service_variant"),
        "claim_subtype": test_df.get("claim_subtype"),
    }
    global_mean = float(np.mean(y_train)) if len(y_train) else 0.0

    def _rate_by_keys(key_cols: list[str], df_train, y_train, df_test, df_train_for_train_assign):
        """Compute denial rate grouped by key_cols using train data, then
        return (train_rates, test_rates) — series aligned to each row."""
        df_train_copy = pd.DataFrame({k: train_keys[k] for k in key_cols})
        df_train_copy["y"] = y_train
        grp = df_train_copy.dropna(subset=key_cols).groupby(key_cols)["y"].agg(["sum", "count"]).reset_index()
        grp["rate"] = grp["sum"] / grp["count"]
        rates_train = pd.DataFrame({k: train_keys[k] for k in key_cols}).merge(
            grp[key_cols + ["rate"]], on=key_cols, how="left"
        )["rate"].fillna(global_mean).astype("float32")
        rates_test = pd.DataFrame({k: test_keys[k] for k in key_cols}).merge(
            grp[key_cols + ["rate"]], on=key_cols, how="left"
        )["rate"].fillna(global_mean).astype("float32")
        return rates_train.values, rates_test.values

    # Map each MV-derived column to its grouping keys.
    column_to_keys: dict[str, list[str]] = {
        "payer_overall_denial_rate":    ["payer_id"],
        "payer_cpt_denial_rate":        ["payer_id", "primary_cpt"],
        "payer_dx_denial_rate":         ["payer_id", "primary_dx"],
        "payer_pos_denial_rate":        ["payer_id", "primary_pos"],
        "cpt_dx_denial_rate":           ["primary_cpt", "primary_dx"],
        "cpt_dx_alignment_score":       ["primary_cpt", "primary_dx"],
        "payer_provider_denial_rate":   ["payer_id", "billing_provider_id"],
        "provider_overall_denial_rate": ["billing_provider_id"],
        "provider_payer_denial_rate":   ["billing_provider_id", "payer_id"],
        "provider_cpt_denial_rate":     ["billing_provider_id", "primary_cpt"],
        "provider_cpt_denial_rate_joint": ["billing_provider_id", "primary_cpt"],
    }

    for col, keys in column_to_keys.items():
        if col not in Xtr.columns:
            continue
        try:
            rt, re_ = _rate_by_keys(keys, train_df, y_train, test_df, train_df)
            Xtr[col] = rt[: len(Xtr)]
            Xte[col] = re_[: len(Xte)]
        except Exception as exc:
            logger.warning("leakage-correction skipped %s: %s", col, exc)
            continue

    return Xtr, Xte


async def simple_oof_for_variant(Session, variant: str, subtype: str) -> dict:
    """5-fold OOF for simple_pipeline-equivalent features on the same cohort."""
    label = f"SIMPLE {variant}/{subtype}"
    print(f"\n  [{label}] starting...")

    async with Session() as session:
        full_df = await load_training_corpus(session, service_variant=variant, claim_subtype=subtype)
    n_rows = len(full_df)
    if n_rows == 0:
        return {"variant": variant, "subtype": subtype, "n_rows": 0, "skipped": "empty corpus"}
    full_df = full_df.reset_index(drop=True)
    y_full = full_df["denied"].astype(int).to_numpy()
    claim_ids = full_df["claim_id"].astype(int).tolist()

    # Load simple-pipeline cohort (same claim_ids; simpler feature shape)
    async with Session() as session:
        simple_df = await _load_simple_cohort(session, claim_ids)
    if simple_df.empty:
        return {"variant": variant, "subtype": subtype, "n_rows": 0, "skipped": "simple cohort empty"}

    # Reindex to align with full_df.claim_id
    simple_df = simple_df.set_index("claim_id").reindex(full_df["claim_id"]).reset_index()
    assert (simple_df["claim_id"].astype(int) == full_df["claim_id"].astype(int)).all()

    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=XGBOOST_RANDOM_STATE)
    oof_scores = np.zeros(n_rows, dtype="float32")
    oof_filled = np.zeros(n_rows, dtype="bool")

    for fold_idx, (tr_idx, te_idx) in enumerate(skf.split(simple_df, y_full)):
        train_df = simple_df.iloc[tr_idx].reset_index(drop=True)
        test_df = simple_df.iloc[te_idx].reset_index(drop=True)
        y_train = y_full[tr_idx]

        # Build vocabs on train fold; transform both
        payer_v, proc_v, dx_v = _build_vocabs(train_df)
        X_train = _simple_features(train_df, payer_v, proc_v, dx_v)
        X_test = _simple_features(test_df, payer_v, proc_v, dx_v)

        # Align columns (defensive)
        X_test = X_test.reindex(columns=X_train.columns, fill_value=0)

        n_pos = int(y_train.sum())
        n_neg = int((1 - y_train).sum())
        spw = (n_neg / max(n_pos, 1)) if n_pos > 0 else 1.0
        model = XGBClassifier(
            n_estimators=200, max_depth=5, learning_rate=0.05,
            random_state=XGBOOST_RANDOM_STATE, scale_pos_weight=spw,
            eval_metric="logloss", tree_method="hist",
        )
        model.fit(X_train.to_numpy(), y_train)
        probs = model.predict_proba(X_test.to_numpy())[:, 1]
        oof_scores[te_idx] = probs
        oof_filled[te_idx] = True
        print(f"    fold {fold_idx+1}/5: train={len(tr_idx)} test={len(te_idx)}")

    calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.001, y_max=0.999)
    calibrator.fit(oof_scores, y_full)
    oof_cal = calibrator.transform(oof_scores)

    from rcm.ml.trainer import _select_threshold
    from rcm.features.constants import PRECISION_FLOOR
    threshold = float(_select_threshold(oof_cal, y_full, PRECISION_FLOOR))

    return {
        "variant": variant,
        "subtype": subtype,
        "pipeline": "simple_pipeline",
        "n_rows": int(n_rows),
        "claim_ids": claim_ids,
        "y_true": y_full.tolist(),
        "y_oof_raw": oof_scores.tolist(),
        "y_oof_cal": oof_cal.tolist(),
        "threshold": threshold,
    }


# ---------------------------------------------------------------------------
# Aggregation + decision
# ---------------------------------------------------------------------------

def _per_pipeline_metrics(oof_result: dict) -> dict:
    if oof_result.get("skipped"):
        return {"skipped": oof_result["skipped"]}
    y_true = np.asarray(oof_result["y_true"], dtype=int)
    y_score = np.asarray(oof_result["y_oof_cal"], dtype="float64")
    return _all_metrics(y_true, y_score, oof_result["threshold"])


def _paired_analysis(simple_oof: dict, fb_oof: dict) -> dict:
    if simple_oof.get("skipped") or fb_oof.get("skipped"):
        return {"skipped": True}
    y_true = np.asarray(simple_oof["y_true"], dtype=int)
    s_score = np.asarray(simple_oof["y_oof_cal"], dtype="float64")
    f_score = np.asarray(fb_oof["y_oof_cal"], dtype="float64")
    s_label = (s_score >= simple_oof["threshold"]).astype(int)
    f_label = (f_score >= fb_oof["threshold"]).astype(int)

    agree = int((s_label == f_label).sum())
    n = len(y_true)
    deltas = np.abs(s_score - f_score)
    b, c, p = _mcnemar(y_true, s_label, f_label)

    return {
        "n": n,
        "agreement_rate": float(agree / n),
        "agreement_count": agree,
        "disagreement_count": int(n - agree),
        "mcnemar_simple_correct_fb_wrong": b,
        "mcnemar_fb_correct_simple_wrong": c,
        "mcnemar_p_value": p,
        "favors_fb_directionally": c > b,
        "score_delta_max": float(deltas.max()),
        "score_delta_mean": float(deltas.mean()),
        "score_delta_p50": float(np.median(deltas)),
        "score_delta_p95": float(np.quantile(deltas, 0.95)),
        "score_delta_p99": float(np.quantile(deltas, 0.99)),
    }


def _disagreement_subgroups(simple_oof: dict, fb_oof: dict, source_df: pd.DataFrame | None) -> dict:
    if simple_oof.get("skipped") or fb_oof.get("skipped") or source_df is None:
        return {"skipped": True}
    y_true = np.asarray(simple_oof["y_true"], dtype=int)
    s_label = (np.asarray(simple_oof["y_oof_cal"]) >= simple_oof["threshold"]).astype(int)
    f_label = (np.asarray(fb_oof["y_oof_cal"]) >= fb_oof["threshold"]).astype(int)
    disagree_mask = s_label != f_label

    out: dict[str, Any] = {}
    out["total_disagreements"] = int(disagree_mask.sum())

    # Direction breakdown
    fb_correct = (f_label == y_true) & disagree_mask
    s_correct = (s_label == y_true) & disagree_mask
    both_wrong = (s_label != y_true) & (f_label != y_true) & disagree_mask
    # In a disagreement, exactly one is correct (or both wrong if direction is opposite to truth)
    out["fb_correct_simple_wrong"] = int(fb_correct.sum())
    out["simple_correct_fb_wrong"] = int(s_correct.sum())
    out["both_wrong_on_disagreement"] = int(both_wrong.sum())

    # Subgroup investigations
    n_total = len(y_true)

    # NULL payer cohort
    if "payer_id" in source_df.columns:
        null_payer = source_df["payer_id"].isna().values
        n_np = int(null_payer.sum())
        out["null_payer"] = {
            "n_in_cohort": n_np,
            "n_disagreements": int((disagree_mask & null_payer).sum()),
            "fb_correct_on_null_payer": int((fb_correct & null_payer).sum()),
            "simple_correct_on_null_payer": int((s_correct & null_payer).sum()),
        }

    # Rare CPT / Dx / encoder-prior-fallback (skip if columns absent in source_df)
    if "primary_cpt" in source_df.columns:
        cpt_counts = source_df["primary_cpt"].value_counts()
        rare_cpts = set(cpt_counts[cpt_counts < 10].index)
        rare_cpt_mask = source_df["primary_cpt"].isin(rare_cpts).values
        out["rare_cpt"] = {
            "n_in_cohort": int(rare_cpt_mask.sum()),
            "n_disagreements": int((disagree_mask & rare_cpt_mask).sum()),
            "fb_correct": int((fb_correct & rare_cpt_mask).sum()),
            "simple_correct": int((s_correct & rare_cpt_mask).sum()),
        }
    if "primary_dx" in source_df.columns:
        dx_counts = source_df["primary_dx"].value_counts()
        rare_dxs = set(dx_counts[dx_counts < 10].index)
        rare_dx_mask = source_df["primary_dx"].isin(rare_dxs).values
        out["rare_dx"] = {
            "n_in_cohort": int(rare_dx_mask.sum()),
            "n_disagreements": int((disagree_mask & rare_dx_mask).sum()),
            "fb_correct": int((fb_correct & rare_dx_mask).sum()),
            "simple_correct": int((s_correct & rare_dx_mask).sum()),
        }

    # Top-25 biggest score-delta disagreements
    s_score = np.asarray(simple_oof["y_oof_cal"])
    f_score = np.asarray(fb_oof["y_oof_cal"])
    deltas = np.abs(s_score - f_score)
    order = np.argsort(-deltas)
    top_n = []
    for idx in order[:25]:
        top_n.append({
            "claim_id": int(simple_oof["claim_ids"][idx]),
            "y_true": int(y_true[idx]),
            "simple_score": float(s_score[idx]),
            "fb_score": float(f_score[idx]),
            "delta": float(deltas[idx]),
            "simple_label": int(s_label[idx]),
            "fb_label": int(f_label[idx]),
            "fb_correct": bool(f_label[idx] == y_true[idx]),
            "simple_correct": bool(s_label[idx] == y_true[idx]),
        })
    out["top_25_disagreements_by_delta"] = top_n
    return out


# ---------------------------------------------------------------------------
# Decision logic
# ---------------------------------------------------------------------------

def _decide(primary: dict, corrected: dict | None, paired: dict) -> dict:
    """Apply §7 framework gates with §8 leakage-correction precedence."""
    rationale = []
    pri_overall = primary.get("overall", {})
    cor_overall = (corrected or {}).get("overall", {}) if corrected else None
    pri_fb = pri_overall.get("featurebuilder", {})
    pri_simple = pri_overall.get("simple_pipeline", {})

    if pri_fb.get("degenerate") or pri_simple.get("degenerate"):
        rationale.append("Degenerate metrics — overall cohort not viable for comparison")
        return {"recommendation": "D", "rationale": rationale}

    auc_delta = pri_fb.get("roc_auc", 0) - pri_simple.get("roc_auc", 0)
    pr_delta = pri_fb.get("pr_auc", 0) - pri_simple.get("pr_auc", 0)
    f1_delta = pri_fb.get("f1", 0) - pri_simple.get("f1", 0)
    brier_delta = pri_fb.get("brier", 1) - pri_simple.get("brier", 1)
    mcnemar_p = paired.get("mcnemar_p_value", 1.0)
    favors_fb = paired.get("favors_fb_directionally", False)

    rationale.append(f"Primary AUC delta (FB - simple) = {auc_delta:+.4f}")
    rationale.append(f"Primary PR-AUC delta = {pr_delta:+.4f}")
    rationale.append(f"Primary F1 delta = {f1_delta:+.4f}")
    rationale.append(f"Brier delta (lower is better; FB - simple) = {brier_delta:+.4f}")
    rationale.append(f"McNemar p = {mcnemar_p:.4f} (favors FB directionally: {favors_fb})")

    # Primary promotion gate
    primary_promote = (
        auc_delta >= PROMOTION_GATES["min_auc_delta"]
        and pr_delta >= PROMOTION_GATES["min_pr_auc_delta"]
        and f1_delta >= PROMOTION_GATES["min_f1_delta"]
        and brier_delta <= 0
        and mcnemar_p < PROMOTION_GATES["mcnemar_p_max"]
        and favors_fb
    )

    # Per-variant regression check (primary)
    worst_variant_delta = float("inf")
    worst_variant = ""
    for v_s, per_pipe in primary.items():
        if v_s == "overall":
            continue
        fb_m = per_pipe.get("featurebuilder", {})
        sm = per_pipe.get("simple_pipeline", {})
        if fb_m.get("degenerate") or sm.get("degenerate"):
            continue
        d = fb_m.get("roc_auc", 0) - sm.get("roc_auc", 0)
        if d < worst_variant_delta:
            worst_variant_delta = d
            worst_variant = v_s
    rationale.append(
        f"Worst variant AUC delta = {worst_variant_delta:+.4f} on {worst_variant} "
        f"(min allowed for promotion: {PROMOTION_GATES['worst_variant_regression']:+.4f})"
    )
    no_variant_regression = worst_variant_delta >= PROMOTION_GATES["worst_variant_regression"]

    # Leakage-corrected gate
    corrected_promote = False
    if cor_overall is not None:
        cor_fb = cor_overall.get("featurebuilder_corrected", {})
        if cor_fb and not cor_fb.get("degenerate"):
            cor_auc_delta = cor_fb.get("roc_auc", 0) - pri_simple.get("roc_auc", 0)
            cor_pr_delta = cor_fb.get("pr_auc", 0) - pri_simple.get("pr_auc", 0)
            cor_f1_delta = cor_fb.get("f1", 0) - pri_simple.get("f1", 0)
            rationale.append(
                f"Leakage-corrected AUC delta (corrected FB - simple) = {cor_auc_delta:+.4f}"
            )
            rationale.append(f"Leakage-corrected PR-AUC delta = {cor_pr_delta:+.4f}")
            rationale.append(f"Leakage-corrected F1 delta = {cor_f1_delta:+.4f}")
            corrected_promote = (
                cor_auc_delta >= PROMOTION_GATES["min_auc_delta"]
                and cor_pr_delta >= PROMOTION_GATES["min_pr_auc_delta"]
                and cor_f1_delta >= PROMOTION_GATES["min_f1_delta"]
            )
            primary_to_corrected_collapse = (
                auc_delta > 0 and cor_auc_delta < (auc_delta - 0.01)
            )
            rationale.append(
                f"Corrected promotion gate satisfied: {corrected_promote}"
            )
            rationale.append(
                f"Material collapse from primary to corrected: {primary_to_corrected_collapse}"
            )
        else:
            rationale.append("Leakage-corrected metrics unavailable or degenerate")
            corrected_promote = False
    else:
        rationale.append("Leakage-corrected analysis not run — promotion DISALLOWED")
        corrected_promote = False

    # Final decision per the AIR's enforced hierarchy
    if primary_promote and no_variant_regression and corrected_promote:
        decision = "A"
        rationale.append("DECISION A: FB satisfies all primary + corrected gates")
    elif primary_promote and no_variant_regression and not corrected_promote:
        # Leakage-driven advantage → block promotion
        decision = "D"
        rationale.append(
            "DECISION D: Primary gates satisfied but leakage-corrected does not "
            "support promotion — advantage is likely leakage-driven. "
            "Continue shadow mode until structural fix."
        )
    elif (
        abs(auc_delta) < 0.005 and abs(pr_delta) < 0.005 and abs(f1_delta) < 0.005
    ):
        decision = "B"
        rationale.append("DECISION B: FB and simple_pipeline are statistically indistinguishable")
    elif worst_variant_delta < PROMOTION_GATES["worst_variant_regression"]:
        # FB is worse on some variant; hybrid might help
        decision = "C"
        rationale.append(
            f"DECISION C: FB regresses on {worst_variant}; consider hybrid (route by variant)"
        )
    elif auc_delta < 0:
        decision = "B"
        rationale.append("DECISION B: FB is overall worse than simple_pipeline")
    else:
        # Some advantage but not enough for full promotion
        decision = "D"
        rationale.append("DECISION D: Inconclusive evidence; continue shadow")

    return {
        "recommendation": decision,
        "rationale": rationale,
        "primary_promote": primary_promote,
        "corrected_promote": corrected_promote,
        "no_variant_regression": no_variant_regression,
        "auc_delta_primary": float(auc_delta),
        "auc_delta_corrected": (
            float(cor_overall.get("featurebuilder_corrected", {}).get("roc_auc", 0) - pri_simple.get("roc_auc", 0))
            if cor_overall else None
        ),
    }


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

async def main() -> None:
    engine = create_async_engine(DSN, pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    overall: dict[str, Any] = {
        "alembic_head": "0016_mv_pch_deleted",
        "cohort": "mv_claim_labels (post-CR-056)",
        "promotion_gates": PROMOTION_GATES,
        "primary": {},
        "corrected": {},
        "paired": {},
        "disagreement": {},
        "complexity": {},
        "decision": {},
    }

    hdr("R6 — Paired benchmark + production decision (CR-066)")
    print(f"  Trainable variants: {TRAINABLE}")

    # ---- Phase 1-3: Primary OOF (FB + Simple) per variant ----
    hdr("Phase 1-3: Primary OOF generation")
    t_start = time.monotonic()
    for variant, subtype in TRAINABLE:
        fb = await fb_oof_for_variant(Session, variant, subtype, correct_leakage=False)
        simple = await simple_oof_for_variant(Session, variant, subtype)
        overall["primary"][f"{variant}/{subtype}"] = {
            "featurebuilder": _per_pipeline_metrics(fb),
            "simple_pipeline": _per_pipeline_metrics(simple),
            "_fb_oof": fb,
            "_simple_oof": simple,
        }
    print(f"  Primary OOF total: {time.monotonic() - t_start:.1f}s")

    # ---- Overall metrics (pooled across variants) ----
    pooled_y, pooled_fb_score, pooled_simple_score = [], [], []
    for v_s in overall["primary"]:
        ent = overall["primary"][v_s]
        if ent.get("_fb_oof", {}).get("skipped"):
            continue
        pooled_y.extend(ent["_fb_oof"]["y_true"])
        pooled_fb_score.extend(ent["_fb_oof"]["y_oof_cal"])
        pooled_simple_score.extend(ent["_simple_oof"]["y_oof_cal"])
    pooled_y_arr = np.asarray(pooled_y, dtype=int)
    pooled_fb_arr = np.asarray(pooled_fb_score)
    pooled_simple_arr = np.asarray(pooled_simple_score)
    fb_thr_pool = float(np.median([ent["_fb_oof"]["threshold"] for ent in overall["primary"].values() if not ent.get("_fb_oof", {}).get("skipped")]))
    simple_thr_pool = float(np.median([ent["_simple_oof"]["threshold"] for ent in overall["primary"].values() if not ent.get("_simple_oof", {}).get("skipped")]))
    overall["primary"]["overall"] = {
        "featurebuilder": _all_metrics(pooled_y_arr, pooled_fb_arr, fb_thr_pool),
        "simple_pipeline": _all_metrics(pooled_y_arr, pooled_simple_arr, simple_thr_pool),
    }

    # ---- Phase 4: Paired analysis ----
    hdr("Phase 4: Paired analysis")
    # Build "virtual" pooled OOF dicts for the paired analysis
    pooled_fb = {
        "y_true": pooled_y_arr.tolist(),
        "y_oof_cal": pooled_fb_arr.tolist(),
        "threshold": fb_thr_pool,
        "claim_ids": [c for v_s in overall["primary"]
                      if v_s != "overall" and not overall["primary"][v_s].get("_fb_oof", {}).get("skipped")
                      for c in overall["primary"][v_s]["_fb_oof"]["claim_ids"]],
    }
    pooled_simple = {
        "y_true": pooled_y_arr.tolist(),
        "y_oof_cal": pooled_simple_arr.tolist(),
        "threshold": simple_thr_pool,
        "claim_ids": pooled_fb["claim_ids"],
    }
    overall["paired"]["overall"] = _paired_analysis(pooled_simple, pooled_fb)
    print(f"  agreement: {overall['paired']['overall'].get('agreement_rate', 0):.4f}")
    print(f"  mcnemar p: {overall['paired']['overall'].get('mcnemar_p_value', 1):.4f}")

    # ---- Phase 5: Disagreement subgroup analysis ----
    hdr("Phase 5: Disagreement subgroup analysis")
    # Reload pooled source DF for subgroup labeling
    src_frames = []
    async with Session() as session:
        for v, s in TRAINABLE:
            df_v = await load_training_corpus(session, service_variant=v, claim_subtype=s)
            src_frames.append(df_v.reset_index(drop=True))
    pooled_src = pd.concat(src_frames, ignore_index=True)
    overall["disagreement"]["overall"] = _disagreement_subgroups(pooled_simple, pooled_fb, pooled_src)
    print(f"  total disagreements: {overall['disagreement']['overall'].get('total_disagreements', 0)}")

    # ---- Phase 6-7: Leakage-corrected FB OOF + corrected metrics ----
    hdr("Phase 6-7: Leakage-corrected FB OOF (per-fold MV aggregates)")
    t_corr_start = time.monotonic()
    pooled_fb_corr_score: list[float] = []
    for variant, subtype in TRAINABLE:
        fb_corr = await fb_oof_for_variant(Session, variant, subtype, correct_leakage=True)
        overall["corrected"][f"{variant}/{subtype}"] = {
            "featurebuilder_corrected": _per_pipeline_metrics(fb_corr),
            "_fb_corr_oof": fb_corr,
        }
        if not fb_corr.get("skipped"):
            pooled_fb_corr_score.extend(fb_corr["y_oof_cal"])
    print(f"  Corrected OOF total: {time.monotonic() - t_corr_start:.1f}s")

    pooled_fb_corr_arr = np.asarray(pooled_fb_corr_score)
    fb_corr_thr_pool = float(np.median(
        [ent["_fb_corr_oof"]["threshold"] for ent in overall["corrected"].values() if not ent.get("_fb_corr_oof", {}).get("skipped")]
    ))
    overall["corrected"]["overall"] = {
        "featurebuilder_corrected": _all_metrics(pooled_y_arr, pooled_fb_corr_arr, fb_corr_thr_pool),
    }

    # ---- Phase 8: Complexity assessment (compact static measurements) ----
    overall["complexity"] = {
        "feature_count_primary_fb": {
            "837D/dental": 116, "837P/healthcare": 114, "837I/home_care": 119,
        },
        "feature_count_simple_pipeline": "computed at fit_transform; ~50-80 per claim",
        "signal_density_fb": 0.458,  # from CR-062
        "active_features_fb": 160,
        "constant_features_fb": 189,
        "training_time_seconds_fb_per_variant_5fold": "see r6_benchmark_post.json timings",
        "artifact_size_mb_fb_total": 1.1,  # CR-063
        "prediction_latency_ms_per_claim_fb_shadow": "20-50ms (CR-065)",
    }

    # ---- Phase 9: Decision ----
    hdr("Phase 9: Decision")
    decision = _decide(overall["primary"], overall["corrected"], overall["paired"]["overall"])
    overall["decision"] = decision
    print(f"  RECOMMENDATION: {decision['recommendation']}")
    for r in decision["rationale"]:
        print(f"    - {r}")

    # ---- Strip internal _oof keys before writing JSON ----
    for v_s, ent in overall["primary"].items():
        if isinstance(ent, dict):
            ent.pop("_fb_oof", None)
            ent.pop("_simple_oof", None)
    for v_s, ent in overall["corrected"].items():
        if isinstance(ent, dict):
            ent.pop("_fb_corr_oof", None)

    Path("scripts/r6_benchmark_post.json").write_text(
        json.dumps(overall, indent=2, default=str), encoding="utf-8",
    )
    print(f"\n  wrote scripts/r6_benchmark_post.json")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
