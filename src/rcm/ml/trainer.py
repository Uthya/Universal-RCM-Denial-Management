"""Minimal training: fit XGBoost + isotonic calibrator + precision-floor threshold.

Phase 3 vertical slice. Phase 4 will replace this with the full per-variant
Optuna search + drift baseline emission.
"""

from __future__ import annotations

import logging
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
from sklearn.model_selection import StratifiedKFold, cross_val_predict, train_test_split
from sqlalchemy.ext.asyncio import AsyncSession
from xgboost import XGBClassifier

from rcm.features.builder import (
    FeatureArtifacts,
    FeatureBuilder,
    compute_leakage_safe_denial_rates,
    compute_leakage_safe_recency_rates,
)
from rcm.features.constants import (
    LOW_PROB_CUTOFF,
    PRECISION_FLOOR,
    TARGET_ENCODER_CV_FOLDS,
    XGBOOST_RANDOM_STATE,
)
from rcm.features.dataset import load_training_corpus
from rcm.ml.artifacts import ModelArtifactBundle

logger = logging.getLogger(__name__)


def _select_threshold(scores: np.ndarray, y: np.ndarray, precision_floor: float) -> float:
    """Pick the highest-recall threshold meeting the precision floor.

    Falls back to the max-precision threshold if the floor is unreachable.
    """
    candidates = np.arange(0.01, 0.99, 0.01)
    best_threshold: float | None = None
    best_recall = -1.0
    max_precision = -1.0
    max_p_threshold = 0.5

    for t in candidates:
        y_pred = (scores >= t).astype(int)
        tp = int(((y_pred == 1) & (y == 1)).sum())
        fp = int(((y_pred == 1) & (y == 0)).sum())
        fn = int(((y_pred == 0) & (y == 1)).sum())
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0

        if precision > max_precision:
            max_precision = precision
            max_p_threshold = float(t)

        if precision >= precision_floor and recall > best_recall:
            best_recall = recall
            best_threshold = float(t)

    if best_threshold is None:
        logger.warning(
            "Precision floor %.2f unreachable; using max-precision threshold=%.3f (precision=%.3f)",
            precision_floor, max_p_threshold, max_precision,
        )
        return max_p_threshold
    return best_threshold


async def train_variant(
    session: AsyncSession,
    artifact_dir: Path,
    *,
    service_variant: str,
    claim_subtype: str,
    limit: int | None = None,
    n_estimators: int = 200,
    max_depth: int = 5,
    learning_rate: float = 0.05,
    include_lifecycle: bool = False,
    include_freq7: bool = False,
) -> ModelArtifactBundle:
    """Train a per-variant denial-prediction model end-to-end (CR-064).

    Variant-agnostic generalization of the original `train_healthcare_model`.
    Pipeline (byte-equivalent to the pre-CR-064 healthcare path when called
    with service_variant='837P', claim_subtype='healthcare'):

        1. load_training_corpus(session, service_variant, claim_subtype)
        2. FeatureBuilder.fit_transform → leakage-safe encoded matrix + state
        3. XGBoost fit on full matrix
        4. Isotonic calibrator fit on out-of-fold raw scores via cross_val_predict
        5. Precision-floor threshold selection on calibrated OOF
        6. Save artifact bundle

    No FeatureBuilder / schema / DB / hyperparameter / tuning change is
    introduced by R4 — this is a refactor only.
    """
    df = await load_training_corpus(
        session,
        service_variant=service_variant,
        claim_subtype=claim_subtype,
        limit=limit,
        include_freq7=include_freq7,
    )
    if df.empty:
        raise ValueError(
            f"No training data — mv_claim_labels is empty for "
            f"{service_variant}/{claim_subtype}"
        )

    if len(df) < 20:
        raise ValueError(
            f"Too few rows ({len(df)}) for {service_variant}/{claim_subtype}; "
            f"need >=20 for the 70/15/15 split to retain ≥3 per slice"
        )

    y_all = df["denied"].astype(int).to_numpy()
    n_pos_total = int(y_all.sum())
    n_neg_total = int((1 - y_all).sum())
    if n_pos_total < 5 or n_neg_total < 5:
        raise ValueError(
            f"Per-class count too small ({n_pos_total} pos / {n_neg_total} neg) "
            f"for {service_variant}/{claim_subtype}; need ≥5 of each class"
        )

    # ------------------------------------------------------------------
    # CR-075: stratified 70/15/15 split — train / validation / held-out
    # ------------------------------------------------------------------
    # train      → FB.fit_transform + XGBoost.fit (OOF only for diagnostics)
    # validation → calibrator fit + threshold selection
    # held_out   → reported metrics (never used for any training step)
    idx_all = np.arange(len(df))
    idx_train, idx_temp = train_test_split(
        idx_all, test_size=0.30, random_state=XGBOOST_RANDOM_STATE, stratify=y_all,
    )
    idx_val, idx_held = train_test_split(
        idx_temp, test_size=0.50, random_state=XGBOOST_RANDOM_STATE, stratify=y_all[idx_temp],
    )
    df_train = df.iloc[idx_train].copy()
    df_val   = df.iloc[idx_val].copy()
    df_held  = df.iloc[idx_held].copy()
    y_train  = y_all[idx_train]
    y_val    = y_all[idx_val]
    y_held   = y_all[idx_held]

    # CR-107: compute leakage-safe denial rates from TRAIN labels for every
    # row in train+val+held. Each row's value uses only EARLIER-DATED TRAIN
    # rows (strict-< on service_from_date). Production transform() callers
    # do NOT pass safe_rates and continue to use the global MV.
    safe_rates_all = compute_leakage_safe_denial_rates(
        df, df_train, pd.Series(y_train, index=df_train.index),
    )
    safe_rates_train = safe_rates_all.loc[df_train.index]
    safe_rates_val   = safe_rates_all.loc[df_val.index]
    safe_rates_held  = safe_rates_all.loc[df_held.index]

    # CR-126B: leakage-safe recency rates (2 features). Same train-only-rows
    # restriction; predict-time path reads from the new mv_payer_denial_rates_*
    # MVs via coverage.py / joint.py.
    safe_recency_all = compute_leakage_safe_recency_rates(
        df, df_train, pd.Series(y_train, index=df_train.index),
    )
    safe_recency_train = safe_recency_all.loc[df_train.index]
    safe_recency_val   = safe_recency_all.loc[df_val.index]
    safe_recency_held  = safe_recency_all.loc[df_held.index]

    # Fit FB on TRAIN only — encoder vocab + rarity_state come from train rows
    builder = FeatureBuilder(
        service_variant=service_variant,
        claim_subtype=claim_subtype,
        include_lifecycle=include_lifecycle,
    )
    artifacts = await builder.fit_transform(
        session, df_train, pd.Series(y_train, index=df_train.index),
        safe_rates=safe_rates_train,
        safe_recency_rates=safe_recency_train,
    )
    X_train = artifacts.features.astype("float32")

    # Transform val + held with the fitted FB (with leakage-safe rates)
    X_val  = (await builder.transform(session, df_val,
                                       safe_rates=safe_rates_val,
                                       safe_recency_rates=safe_recency_val)).astype("float32")
    X_held = (await builder.transform(session, df_held,
                                       safe_rates=safe_rates_held,
                                       safe_recency_rates=safe_recency_held)).astype("float32")

    # Class balance for XGBoost
    spw = (1 - y_train).sum() / max(1, int(y_train.sum()))

    base_model = XGBClassifier(
        n_estimators=n_estimators,
        max_depth=max_depth,
        learning_rate=learning_rate,
        random_state=XGBOOST_RANDOM_STATE,
        scale_pos_weight=float(spw),
        eval_metric="logloss",
        tree_method="hist",
    )

    # OOF on the TRAIN slice — diagnostic only (NOT used for threshold).
    # CR-076 #3: pass DataFrame to preserve feature names on the booster.
    # This eliminates positional-only attribution and lets predictor.py's
    # FeatureSchemaError fire if the predict-time column ORDER drifts.
    n_pos_tr = int(y_train.sum()); n_neg_tr = int((1 - y_train).sum())
    n_splits = min(TARGET_ENCODER_CV_FOLDS, max(2, n_pos_tr), max(2, n_neg_tr))
    cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=XGBOOST_RANDOM_STATE)
    oof_raw = cross_val_predict(
        base_model, X_train, y_train, cv=cv, method="predict_proba",
    )[:, 1]

    # Fit final model on TRAIN only — DataFrame so feature_names persist
    base_model.fit(X_train, y_train)
    booster = base_model.get_booster()
    # CR-076 #3 invariant: booster must know its feature names + count
    if not booster.feature_names or len(booster.feature_names) != X_train.shape[1]:
        raise AssertionError(
            f"booster.feature_names integrity failure: expected "
            f"{X_train.shape[1]} names, got "
            f"{len(booster.feature_names) if booster.feature_names else 0}"
        )
    if list(booster.feature_names) != list(X_train.columns):
        raise AssertionError(
            "booster.feature_names diverged from X_train.columns at fit time"
        )

    # Final-model predictions for val + held-out (raw, pre-calibration).
    # CR-076 #3: pass DataFrames so feature-name validation in XGBoost runs.
    val_raw  = base_model.predict_proba(X_val)[:, 1]
    held_raw = base_model.predict_proba(X_held)[:, 1]

    # Isotonic calibration fit on VALIDATION predictions (cleaner than OOF —
    # val_raw comes from the same final model used at predict time, so the
    # calibration target distribution matches the production distribution)
    calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.001, y_max=0.999)
    calibrator.fit(val_raw, y_val)

    test_pts = np.linspace(0, 1, 20)
    if not bool(np.all(np.diff(calibrator.transform(test_pts)) >= -1e-9)):
        logger.warning("Isotonic calibrator non-monotonic — falling back to identity")
        calibrator = None
        val_cal  = val_raw
        held_cal = held_raw
        oof_cal  = oof_raw
    else:
        val_cal  = calibrator.transform(val_raw)
        held_cal = calibrator.transform(held_raw)
        oof_cal  = calibrator.transform(oof_raw)

    # Threshold selection — ON VALIDATION predictions ONLY (never held-out)
    threshold = _select_threshold(val_cal, y_val, PRECISION_FLOOR)

    # ------------------------------------------------------------------
    # Metric blocks — three independent surfaces
    # ------------------------------------------------------------------
    def _block(y_true: np.ndarray, p_cal: np.ndarray, p_raw: np.ndarray) -> dict:
        if len(set(y_true.tolist())) < 2:
            return {"degenerate": True, "n": int(len(y_true))}
        yp = (p_cal >= threshold).astype(int)
        return {
            "n":                     int(len(y_true)),
            "prevalence":            float(y_true.mean()),
            "accuracy_at_threshold": float(accuracy_score(y_true, yp)),
            "precision_at_threshold":float(precision_score(y_true, yp, zero_division=0)),
            "recall_at_threshold":   float(recall_score(y_true, yp, zero_division=0)),
            "f1_at_threshold":       float(f1_score(y_true, yp, zero_division=0)),
            "roc_auc":               float(roc_auc_score(y_true, p_cal)),
            "pr_auc":                float(average_precision_score(y_true, p_cal)),
            "brier_uncalibrated":    float(brier_score_loss(y_true, p_raw)),
            "brier_calibrated":      float(brier_score_loss(y_true, p_cal)),
            "positive_rate":         float(yp.mean()),
        }

    oof_block  = _block(y_train, oof_cal, oof_raw)
    val_block  = _block(y_val,   val_cal, val_raw)
    held_block = _block(y_held,  held_cal, held_raw)

    metrics = {
        # Headline metrics surface the HELD-OUT values (honest reporting)
        "n_training_rows":         int(len(y_train)),
        "n_validation_rows":       int(len(y_val)),
        "n_held_out_rows":         int(len(y_held)),
        "prevalence":              float(y_all.mean()),
        "accuracy_at_threshold":   held_block.get("accuracy_at_threshold"),
        "precision_at_threshold":  held_block.get("precision_at_threshold"),
        "recall_at_threshold":     held_block.get("recall_at_threshold"),
        "f1_at_threshold":         held_block.get("f1_at_threshold"),
        "roc_auc":                 held_block.get("roc_auc"),
        "pr_auc":                  held_block.get("pr_auc"),
        "brier_uncalibrated":      held_block.get("brier_uncalibrated"),
        "brier_calibrated":        held_block.get("brier_calibrated"),
        "positive_rate":           held_block.get("positive_rate"),
        "low_prob_cutoff":         float(LOW_PROB_CUTOFF),
        # Three full diagnostic blocks for the response + history persistence
        "oof":         oof_block,
        "validation":  val_block,
        "held_out":    held_block,
    }

    # CR-076 #2: unique per-run version. Embeds variant so multi-variant runs
    # produce distinguishable strings, and a UTC timestamp so re-runs differ.
    fb_model_version = (
        "v1.fb." + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        + f".{service_variant}_{claim_subtype}"
    )
    bundle = ModelArtifactBundle(
        booster=booster,
        calibrator=calibrator,
        encoder=artifacts.encoder,
        rarity_state=artifacts.rarity_state,
        feature_columns=list(X_train.columns),
        service_variant=service_variant,
        claim_subtype=claim_subtype,
        decision_threshold=float(threshold),
        feature_engineering_version=artifacts.schema_version,
        model_version=fb_model_version,
        calibrator_version=("isotonic_v1" if calibrator is not None else None),
        metrics=metrics,
        training_size=int(len(y_train)),
        training_prevalence=float(y_train.mean()),
        include_lifecycle=include_lifecycle,
    )
    bundle.save(Path(artifact_dir))
    return bundle


async def train_healthcare_model(
    session: AsyncSession,
    artifact_dir: Path,
    *,
    limit: int | None = None,
    n_estimators: int = 200,
    max_depth: int = 5,
    learning_rate: float = 0.05,
) -> ModelArtifactBundle:
    """Backward-compatible wrapper (CR-064).

    Equivalent to `train_variant(... service_variant='837P', claim_subtype='healthcare')`.
    Preserved so existing callers continue to work; new code should call
    `train_variant` directly.
    """
    return await train_variant(
        session,
        artifact_dir,
        service_variant="837P",
        claim_subtype="healthcare",
        limit=limit,
        n_estimators=n_estimators,
        max_depth=max_depth,
        learning_rate=learning_rate,
    )
