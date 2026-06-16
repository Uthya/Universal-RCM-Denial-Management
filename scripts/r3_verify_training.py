"""R3 — End-to-end training verification per trainable variant (CR-063).

Per the approved R3 AIR with refinements:
  - Single training helper executes ALL trainable variants through the same
    code path. trainer.py is NOT modified; this script's helper is a
    variant-parameterized mirror of train_healthcare_model.
  - Assertion #10: after each artifact is saved, reload it from disk and
    run predict_proba on a small sample to verify persistence integrity.
  - scripts/r3_verify_post.json is transient (CR-063 reporting only).

Verification-only:
  - No Optuna, no hyperparameter tuning, no model selection, no benchmarking.
  - Fixed hyperparameters from trainer.py defaults (n_estimators=200, max_depth=5, lr=0.05).
  - No FeatureBuilder / trainer.py / SQL / schema / DB-object changes.

Stops on the first failing assertion per the user's standing protocol.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import psutil
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
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from xgboost import XGBClassifier

# Project src on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rcm.features.builder import FeatureBuilder  # noqa: E402
from rcm.features.constants import (  # noqa: E402
    LOW_PROB_CUTOFF,
    PRECISION_FLOOR,
    TARGET_ENCODER_CV_FOLDS,
    XGBOOST_RANDOM_STATE,
)
from rcm.features.dataset import load_training_corpus  # noqa: E402
from rcm.ml.artifacts import ModelArtifactBundle  # noqa: E402
from rcm.ml.trainer import _select_threshold  # noqa: E402 (reusing the existing helper)

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("r3")

DSN_SA = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://rcm:rcm_dev_password@localhost:5433/rcm_denials_dev",
)
ARTIFACT_ROOT = Path("artifacts/featurebuilder")

TRAINABLE_VARIANTS = [
    ("837D", "dental"),
    ("837P", "healthcare"),
    ("837I", "home_care"),
]


def hdr(s: str) -> None:
    print()
    print("=" * 78)
    print(s)
    print("=" * 78)


def _now_rss_mb() -> float:
    return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)


def _dir_size_bytes(p: Path) -> int:
    total = 0
    for f in p.rglob("*"):
        if f.is_file():
            total += f.stat().st_size
    return total


async def train_one_variant(
    Session,
    variant: str,
    subtype: str,
    artifact_root: Path,
) -> dict:
    """End-to-end pipeline for ONE variant. Mirrors `train_healthcare_model`
    (`src/rcm/ml/trainer.py:77`) but parameterized for any (variant, subtype).
    trainer.py itself is NOT modified.
    """
    result: dict = {
        "variant": variant,
        "subtype": subtype,
        "assertions": {},
        "timings": {},
        "rss": {},
        "artifact": {},
        "metrics": {},
        "viability_notes": [],
        "exception": None,
        "overall_pass": False,
    }

    art_dir = artifact_root / f"{variant}_{subtype}"
    print(f"\n--- {variant}/{subtype} → {art_dir} ---")

    rss_start = _now_rss_mb()
    result["rss"]["start_mb"] = round(rss_start, 1)

    # ---- 1. corpus load ----
    t0 = time.monotonic()
    try:
        async with Session() as session:
            df = await load_training_corpus(
                session, service_variant=variant, claim_subtype=subtype,
            )
    except Exception as e:
        result["assertions"]["1_corpus_loaded"] = {
            "pass": False, "detail": f"{type(e).__name__}: {e}",
        }
        result["exception"] = str(e)
        result["traceback"] = traceback.format_exc()
        print(f"  FAIL #1 corpus_loaded: {e}")
        return result
    result["timings"]["load_seconds"] = round(time.monotonic() - t0, 2)
    n_rows = len(df)
    result["assertions"]["1_corpus_loaded"] = {
        "pass": n_rows > 0, "detail": f"rows={n_rows}",
    }
    if n_rows == 0:
        print(f"  FAIL #1 corpus_loaded: 0 rows")
        return result
    print(f"  loaded corpus rows={n_rows} ({result['timings']['load_seconds']}s)")

    # ---- 2. FeatureBuilder.fit_transform ----
    y = df["denied"].astype(int).to_numpy()
    builder = FeatureBuilder(service_variant=variant, claim_subtype=subtype)
    t0 = time.monotonic()
    try:
        async with Session() as session:
            artifacts = await builder.fit_transform(
                session, df, pd.Series(y, index=df.index),
            )
    except Exception as e:
        result["assertions"]["2_featurebuilder_fit_transform"] = {
            "pass": False, "detail": f"{type(e).__name__}: {e}",
        }
        result["exception"] = str(e)
        result["traceback"] = traceback.format_exc()
        print(f"  FAIL #2 fit_transform: {e}")
        return result
    result["timings"]["fit_transform_seconds"] = round(time.monotonic() - t0, 2)
    X = artifacts.features.astype("float32")
    result["assertions"]["2_featurebuilder_fit_transform"] = {
        "pass": X.shape == (n_rows, X.shape[1]),
        "detail": f"shape={X.shape}",
    }
    print(f"  fit_transform shape={X.shape} ({result['timings']['fit_transform_seconds']}s)")
    result["feature_count"] = X.shape[1]

    # ---- 3. Feature matrix shape ----
    shape_ok = (X.shape[0] == n_rows) and (X.shape[1] > 0)
    result["assertions"]["3_feature_matrix_shape"] = {
        "pass": shape_ok, "detail": f"rows={X.shape[0]} cols={X.shape[1]} input_rows={n_rows}",
    }
    if not shape_ok:
        return result

    # ---- 4. XGBoost fit (full matrix, FIXED hyperparameters from trainer.py defaults) ----
    n_pos = int(y.sum())
    n_neg = int((1 - y).sum())
    scale_pos_weight = (n_neg / n_pos) if n_pos > 0 else 1.0
    base_model = XGBClassifier(
        n_estimators=200,
        max_depth=5,
        learning_rate=0.05,
        random_state=XGBOOST_RANDOM_STATE,
        scale_pos_weight=scale_pos_weight,
        eval_metric="logloss",
        tree_method="hist",
    )
    t0 = time.monotonic()
    try:
        base_model.fit(X.to_numpy(), y)
    except Exception as e:
        result["assertions"]["4_xgb_fit"] = {
            "pass": False, "detail": f"{type(e).__name__}: {e}",
        }
        result["exception"] = str(e)
        result["traceback"] = traceback.format_exc()
        print(f"  FAIL #4 xgb_fit: {e}")
        return result
    result["timings"]["xgb_fit_seconds"] = round(time.monotonic() - t0, 2)
    booster = base_model.get_booster()
    result["assertions"]["4_xgb_fit"] = {
        "pass": True, "detail": f"trees={booster.num_boosted_rounds()}",
    }
    print(f"  xgb_fit trees={booster.num_boosted_rounds()} ({result['timings']['xgb_fit_seconds']}s)")

    # ---- 5. Cross-val OOF (adaptive n_splits) ----
    n_splits = min(TARGET_ENCODER_CV_FOLDS, max(2, n_pos), max(2, n_neg))
    cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=XGBOOST_RANDOM_STATE)
    t0 = time.monotonic()
    try:
        oof_raw = cross_val_predict(
            base_model, X.to_numpy(), y, cv=cv, method="predict_proba",
        )[:, 1]
    except Exception as e:
        result["assertions"]["5_cv_oof_predict"] = {
            "pass": False, "detail": f"{type(e).__name__}: {e}",
        }
        result["exception"] = str(e)
        result["traceback"] = traceback.format_exc()
        print(f"  FAIL #5 cv_oof_predict: {e}")
        return result
    result["timings"]["cv_oof_seconds"] = round(time.monotonic() - t0, 2)
    result["assertions"]["5_cv_oof_predict"] = {
        "pass": oof_raw.shape == (n_rows,),
        "detail": f"shape={oof_raw.shape} n_splits={n_splits}",
    }
    if not result["assertions"]["5_cv_oof_predict"]["pass"]:
        return result
    print(f"  cv_oof shape={oof_raw.shape} n_splits={n_splits} ({result['timings']['cv_oof_seconds']}s)")

    # ---- 6. Calibrator (graceful identity-passthrough if non-monotonic) ----
    t0 = time.monotonic()
    calibrator: IsotonicRegression | None = IsotonicRegression(
        out_of_bounds="clip", y_min=0.001, y_max=0.999,
    )
    try:
        calibrator.fit(oof_raw, y)
    except Exception as e:
        result["assertions"]["6_calibrator_fit"] = {
            "pass": False, "detail": f"{type(e).__name__}: {e}",
        }
        result["exception"] = str(e)
        result["traceback"] = traceback.format_exc()
        print(f"  FAIL #6 calibrator_fit: {e}")
        return result
    test_pts = np.linspace(0, 1, 20)
    cal_pts = calibrator.transform(test_pts)
    monotonic = bool(np.all(np.diff(cal_pts) >= -1e-9))
    if not monotonic:
        logger.warning("Non-monotonic calibrator on %s/%s — using identity", variant, subtype)
        calibrator = None
        oof_calibrated = oof_raw
        result["viability_notes"].append("calibrator non-monotonic — identity passthrough")
    else:
        oof_calibrated = calibrator.transform(oof_raw)
        result["viability_notes"].append("calibrator monotonic")
    result["timings"]["calibrator_seconds"] = round(time.monotonic() - t0, 2)
    result["assertions"]["6_calibrator_fit"] = {
        "pass": True,
        "detail": f"monotonic={monotonic}",
    }

    # ---- 7. Threshold selection ----
    t0 = time.monotonic()
    try:
        threshold = _select_threshold(oof_calibrated, y, PRECISION_FLOOR)
    except Exception as e:
        result["assertions"]["7_threshold_selection"] = {
            "pass": False, "detail": f"{type(e).__name__}: {e}",
        }
        result["exception"] = str(e)
        result["traceback"] = traceback.format_exc()
        print(f"  FAIL #7 threshold_selection: {e}")
        return result
    result["timings"]["threshold_seconds"] = round(time.monotonic() - t0, 2)
    result["assertions"]["7_threshold_selection"] = {
        "pass": 0.01 <= threshold <= 0.99,
        "detail": f"threshold={threshold:.3f}",
    }
    if not result["assertions"]["7_threshold_selection"]["pass"]:
        return result
    print(f"  threshold={threshold:.3f}")
    result["viability_notes"].append(f"decision threshold within [0.01, 0.99] (={threshold:.3f})")

    # OOF metrics (descriptive — not assertion gates)
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
    result["metrics"] = metrics

    # ---- 8. Artifact save ----
    bundle = ModelArtifactBundle(
        booster=booster,
        calibrator=calibrator,
        encoder=artifacts.encoder,
        rarity_state=artifacts.rarity_state,
        feature_columns=list(X.columns),
        service_variant=variant,
        claim_subtype=subtype,
        decision_threshold=float(threshold),
        feature_engineering_version=artifacts.schema_version,
        model_version="v1.0.0",
        calibrator_version=("isotonic_v1" if calibrator is not None else None),
        metrics=metrics,
        training_size=int(len(y)),
        training_prevalence=float(y.mean()),
    )
    t0 = time.monotonic()
    try:
        bundle.save(art_dir)
    except Exception as e:
        result["assertions"]["8_artifact_save"] = {
            "pass": False, "detail": f"{type(e).__name__}: {e}",
        }
        result["exception"] = str(e)
        result["traceback"] = traceback.format_exc()
        print(f"  FAIL #8 artifact_save: {e}")
        return result
    result["timings"]["save_seconds"] = round(time.monotonic() - t0, 2)
    art_bytes = _dir_size_bytes(art_dir)
    result["assertions"]["8_artifact_save"] = {
        "pass": art_dir.is_dir() and art_bytes > 0,
        "detail": f"dir={art_dir} size_bytes={art_bytes}",
    }
    if not result["assertions"]["8_artifact_save"]["pass"]:
        return result
    print(f"  saved artifact size={art_bytes/1024:.1f} KB ({result['timings']['save_seconds']}s)")

    # ---- 9. OOF metric sanity (roc_auc not NaN, within [0, 1]) ----
    auc = metrics.get("roc_auc")
    auc_ok = (auc is not None) and (not np.isnan(auc)) and (0.0 <= auc <= 1.0)
    result["assertions"]["9_oof_metric_sanity"] = {
        "pass": auc_ok,
        "detail": f"roc_auc={auc}",
    }
    if not auc_ok:
        return result
    print(f"  roc_auc={auc:.4f}  pr_auc={metrics['pr_auc']:.4f}")

    # ---- 10. Reload + predict_proba on sample (persistence integrity) ----
    sample_size = min(8, n_rows)
    sample_X = X.iloc[:sample_size].to_numpy()
    t0 = time.monotonic()
    try:
        reloaded = ModelArtifactBundle.load(art_dir)
        # Booster.predict gives raw scores; replicate XGBClassifier.predict_proba's
        # binary output shape by wrapping as a 2-col probability matrix.
        import xgboost as xgb
        dmat = xgb.DMatrix(sample_X, feature_names=reloaded.feature_columns)
        raw_scores = reloaded.booster.predict(dmat)
        # Apply the saved calibrator (matches the predict-time pipeline)
        if reloaded.calibrator is not None:
            calibrated = reloaded.calibrator.transform(raw_scores)
        else:
            calibrated = raw_scores
        probas = np.column_stack([1.0 - calibrated, calibrated])
    except Exception as e:
        result["assertions"]["10_reload_predict_proba"] = {
            "pass": False, "detail": f"{type(e).__name__}: {e}",
        }
        result["exception"] = str(e)
        result["traceback"] = traceback.format_exc()
        print(f"  FAIL #10 reload_predict_proba: {e}")
        return result
    result["timings"]["reload_predict_seconds"] = round(time.monotonic() - t0, 2)
    shape_ok = probas.shape == (sample_size, 2)
    sums_to_one = bool(np.allclose(probas.sum(axis=1), 1.0, atol=1e-6))
    range_ok = bool(np.all((probas >= 0.0) & (probas <= 1.0)))
    reload_ok = shape_ok and sums_to_one and range_ok
    result["assertions"]["10_reload_predict_proba"] = {
        "pass": reload_ok,
        "detail": f"shape={probas.shape} sums_to_one={sums_to_one} range_ok={range_ok}",
    }
    if not reload_ok:
        return result
    print(f"  reload+predict OK shape={probas.shape} ({result['timings']['reload_predict_seconds']}s)")
    result["viability_notes"].append(
        f"artifact reload + predict_proba sample (n={sample_size}) shape={probas.shape} probabilities sum to 1.0"
    )

    # ---- Artifact metadata ----
    result["artifact"] = {
        "name": f"{variant}_{subtype}",
        "path": str(art_dir),
        "size_bytes": art_bytes,
        "size_kb": round(art_bytes / 1024, 1),
        "size_mb": round(art_bytes / (1024 * 1024), 3),
        "training_rows": n_rows,
        "feature_count": X.shape[1],
        "denied_rows": n_pos,
        "paid_rows": n_neg,
        "class_balance_denial_rate": round(n_pos / n_rows, 4),
        "cv_folds_used": n_splits,
        "decision_threshold": round(float(threshold), 4),
        "feature_engineering_version": artifacts.schema_version,
        "model_version": "v1.0.0",
        "calibrator_version": "isotonic_v1" if calibrator is not None else None,
    }

    # Viability classification (text — not assertion)
    if n_splits >= 5:
        result["viability_notes"].append(f"5-fold CV viable (n_pos={n_pos}, n_neg={n_neg})")
    elif n_splits >= 3:
        result["viability_notes"].append(f"reduced {n_splits}-fold CV (sparse class)")
    else:
        result["viability_notes"].append(f"only {n_splits}-fold CV — borderline")

    # ---- RSS tracking ----
    rss_end = _now_rss_mb()
    result["rss"]["end_mb"] = round(rss_end, 1)
    result["rss"]["delta_mb"] = round(rss_end - rss_start, 1)
    result["timings"]["total_seconds"] = round(
        sum(v for v in result["timings"].values()), 2
    )
    print(f"  total={result['timings']['total_seconds']}s  rss_delta={result['rss']['delta_mb']}MB")

    # Final overall_pass = all 10 assertions PASS
    result["overall_pass"] = all(a["pass"] for a in result["assertions"].values())
    return result


async def main() -> None:
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(DSN_SA, pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    overall = {
        "status": "GO",
        "findings": [],
        "alembic_head": "0016_mv_pch_deleted",
        "artifact_root": str(ARTIFACT_ROOT),
        "variants": {},
    }

    hdr("R3 — End-to-end training verification (CR-063)")
    for variant, subtype in TRAINABLE_VARIANTS:
        r = await train_one_variant(Session, variant, subtype, ARTIFACT_ROOT)
        overall["variants"][f"{variant}/{subtype}"] = r
        if not r["overall_pass"]:
            overall["status"] = "NO-GO"
            failed = [k for k, a in r["assertions"].items() if not a["pass"]]
            overall["findings"].append({
                "variant": f"{variant}/{subtype}",
                "failed_assertions": failed,
                "exception": r.get("exception"),
            })

    # ---- Per-variant assertion outcomes ----
    hdr("Per-Variant Assertion Outcomes (10 assertions × 3 variants = 30 checks)")
    for v_s, r in overall["variants"].items():
        marker = "PASS" if r["overall_pass"] else "FAIL"
        print(f"\n  [{marker}] {v_s}")
        for name, a in r["assertions"].items():
            m = "PASS" if a["pass"] else "FAIL"
            print(f"    [{m}] {name:35s} {a.get('detail', '')}")

    # ---- Training Artifact Inventory ----
    hdr("Training Artifact Inventory")
    print(f"  {'artifact':30s} {'size':>8s} {'rows':>5s} {'feats':>5s} {'denied':>6s} {'paid':>5s} {'rate':>6s} {'cv':>3s} {'thresh':>6s}")
    for v_s, r in overall["variants"].items():
        a = r.get("artifact", {})
        if not a:
            print(f"  {v_s:30s} <no artifact saved>")
            continue
        print(f"  {a['name']:30s} {a['size_mb']:>6.2f}MB "
              f"{a['training_rows']:>5} {a['feature_count']:>5} "
              f"{a['denied_rows']:>6} {a['paid_rows']:>5} "
              f"{a['class_balance_denial_rate']*100:>5.2f}% "
              f"{a['cv_folds_used']:>3} {a['decision_threshold']:>5.3f}")

    # ---- Per-variant report ----
    hdr("Per-Variant Report — timings + memory + viability")
    for v_s, r in overall["variants"].items():
        a = r.get("artifact", {})
        m = r.get("metrics", {})
        t = r.get("timings", {})
        rss = r.get("rss", {})
        print(f"\n  {v_s}")
        if not a:
            print(f"    (no artifact — verification halted)")
            continue
        print(f"    timings: load={t.get('load_seconds')}s  fit_transform={t.get('fit_transform_seconds')}s  "
              f"xgb_fit={t.get('xgb_fit_seconds')}s  cv_oof={t.get('cv_oof_seconds')}s  "
              f"calibrator={t.get('calibrator_seconds')}s  threshold={t.get('threshold_seconds')}s  "
              f"save={t.get('save_seconds')}s  reload_predict={t.get('reload_predict_seconds')}s  "
              f"TOTAL={t.get('total_seconds')}s")
        print(f"    rss: start={rss.get('start_mb')}MB  end={rss.get('end_mb')}MB  delta={rss.get('delta_mb')}MB")
        print(f"    features used by model: {a.get('feature_count')}")
        print(f"    OOF: roc_auc={m.get('roc_auc'):.4f}  pr_auc={m.get('pr_auc'):.4f}  "
              f"f1@thresh={m.get('f1_at_threshold'):.4f}  precision={m.get('precision_at_threshold'):.4f}  "
              f"recall={m.get('recall_at_threshold'):.4f}")
        print(f"    brier: uncal={m.get('brier_uncalibrated'):.4f}  cal={m.get('brier_calibrated'):.4f}")
        print(f"    viability:")
        for n in r["viability_notes"]:
            print(f"      - {n}")

    hdr("R3 STATUS")
    print(f"  ★ status: {overall['status']}")
    if overall["findings"]:
        for f in overall["findings"]:
            print(f"  - {f['variant']}: failed={f['failed_assertions']}  exception={f['exception']}")

    # ---- Transient JSON snapshot ----
    snap = Path("scripts/r3_verify_post.json")
    snap.write_text(json.dumps(overall, indent=2, default=str), encoding="utf-8")
    print(f"\n  wrote {snap} (transient — CR-063 reporting only)")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
