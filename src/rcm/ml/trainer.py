"""Minimal training: fit XGBoost + isotonic calibrator + precision-floor threshold.

Phase 3 vertical slice. Phase 4 will replace this with the full per-variant
Optuna search + drift baseline emission.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sqlalchemy.ext.asyncio import AsyncSession
from xgboost import XGBClassifier

from rcm.features.builder import FeatureArtifacts, FeatureBuilder
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


async def train_healthcare_model(
    session: AsyncSession,
    artifact_dir: Path,
    *,
    limit: int | None = None,
    n_estimators: int = 200,
    max_depth: int = 5,
    learning_rate: float = 0.05,
) -> ModelArtifactBundle:
    """Fit a healthcare model end-to-end from the live DB.

    Pipeline:
        1. load_training_corpus(service_variant='837P', claim_subtype='healthcare')
        2. FeatureBuilder.fit_transform → leakage-safe encoded matrix + state
        3. XGBoost fit on full matrix (no CV split; minimum viable Phase 3)
        4. Isotonic calibrator fit on out-of-fold raw scores via cross_val_predict
        5. Precision-floor threshold selection on calibrated OOF
        6. Save artifact bundle
    """
    df = await load_training_corpus(
        session,
        service_variant="837P",
        claim_subtype="healthcare",
        limit=limit,
    )
    if df.empty:
        raise ValueError("No healthcare training data — mv_claim_labels is empty for 837P/healthcare")

    if len(df) < 10:
        raise ValueError(
            f"Too few rows ({len(df)}) for a usable train; need >=10 for the CV to run"
        )

    y = df["denied"].astype(int).to_numpy()

    builder = FeatureBuilder(service_variant="837P", claim_subtype="healthcare")
    artifacts = await builder.fit_transform(session, df, pd.Series(y, index=df.index))
    X = artifacts.features.astype("float32")

    # Compute scale_pos_weight from class balance
    n_pos = int(y.sum())
    n_neg = int((1 - y).sum())
    spw = (n_neg / n_pos) if n_pos > 0 else 1.0

    base_model = XGBClassifier(
        n_estimators=n_estimators,
        max_depth=max_depth,
        learning_rate=learning_rate,
        random_state=XGBOOST_RANDOM_STATE,
        scale_pos_weight=spw,
        eval_metric="logloss",
        tree_method="hist",
    )

    # OOF raw scores for calibration + threshold
    n_splits = min(TARGET_ENCODER_CV_FOLDS, max(2, n_pos), max(2, n_neg))
    cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=XGBOOST_RANDOM_STATE)
    oof_raw = cross_val_predict(base_model, X.to_numpy(), y, cv=cv, method="predict_proba")[:, 1]

    # Fit final model on all rows
    base_model.fit(X.to_numpy(), y)
    booster = base_model.get_booster()

    # Isotonic calibration on OOF
    calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.001, y_max=0.999)
    calibrator.fit(oof_raw, y)

    # Monotonicity sanity (post-fit)
    test_pts = np.linspace(0, 1, 20)
    calibrated_pts = calibrator.transform(test_pts)
    monotonic = bool(np.all(np.diff(calibrated_pts) >= -1e-9))
    if not monotonic:
        logger.warning("Isotonic calibrator non-monotonic after fit — falling back to identity")
        calibrator = None
        oof_calibrated = oof_raw
    else:
        oof_calibrated = calibrator.transform(oof_raw)

    threshold = _select_threshold(oof_calibrated, y, PRECISION_FLOOR)

    # Compute metrics on OOF (for monitoring snapshot)
    y_pred = (oof_calibrated >= threshold).astype(int)
    metrics = {
        "n_training_rows": int(len(y)),
        "prevalence": float(y.mean()),
        "precision_at_threshold": float(precision_score(y, y_pred, zero_division=0)),
        "recall_at_threshold": float(recall_score(y, y_pred, zero_division=0)),
        "f1_at_threshold": float(f1_score(y, y_pred, zero_division=0)),
        "roc_auc": float(roc_auc_score(y, oof_calibrated)) if len(set(y.tolist())) == 2 else None,
        "pr_auc": float(average_precision_score(y, oof_calibrated)) if len(set(y.tolist())) == 2 else None,
        "brier_uncalibrated": float(brier_score_loss(y, oof_raw)),
        "brier_calibrated": float(brier_score_loss(y, oof_calibrated)),
        "low_prob_cutoff": float(LOW_PROB_CUTOFF),
    }

    bundle = ModelArtifactBundle(
        booster=booster,
        calibrator=calibrator,
        encoder=artifacts.encoder,
        rarity_state=artifacts.rarity_state,
        feature_columns=list(X.columns),
        service_variant="837P",
        claim_subtype="healthcare",
        decision_threshold=float(threshold),
        feature_engineering_version=artifacts.schema_version,
        model_version="v1.0.0",
        calibrator_version=("isotonic_v1" if calibrator is not None else None),
        metrics=metrics,
        training_size=int(len(y)),
        training_prevalence=float(y.mean()),
    )
    bundle.save(Path(artifact_dir))
    return bundle
