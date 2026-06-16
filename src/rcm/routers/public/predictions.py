"""ML-prediction endpoints.

Post-CR-067 architecture:
    POST /train                  → train per-variant FeatureBuilder artifacts
                                   (837P/healthcare, 837D/dental, 837I/home_care)
    POST /predict-file/{id}      → dispatch each claim to its variant's FB
                                   predictor; Option C fallback to
                                   simple_pipeline for institutional_other +
                                   unknown variants
    POST /predict-claim/{id}     → same per-claim dispatch
    GET  /dataset-stats          → claim-volume + denial-rate snapshot

Kill switch:
    Setting env var RCM_FB_PRIMARY=false reverts the predict + train
    endpoints to the pre-CR-067 simple_pipeline path. Used for ops
    mitigation without code rollback.

Shadow logging:
    After CR-067, production rows in `prediction_log` carry
    pipeline_name='featurebuilder'; shadow rows carry 'simple_pipeline'.
    See `rcm.ml.shadow.log_paired_predictions_fb_primary`.
"""

from __future__ import annotations

import logging
import os
import time
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
from fastapi import APIRouter, HTTPException

from rcm.core.config import settings
from rcm.core.database import async_session
from rcm.ml.reason_renderer import render_risk_factors
from rcm.ml.simple_pipeline import (
    NoTrainingDataError,
    ScoredClaim,
    load_artifact,
    predict_file as run_predict_file,
    train as run_train,
)
from rcm.schemas.public import (
    DatasetStatsResponse,
    PredictClaimResponse,
    PredictFileResponse,
    RiskFactorItem,
    TrainEvaluation,
    TrainMetrics,
    TrainModelResponse,
    TrainSplit,
    UnseenIndicators,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# FeatureBuilder per-variant dispatch (CR-067 cutover)
# ---------------------------------------------------------------------------

_FB_ARTIFACT_ROOT = Path("artifacts/featurebuilder")

# (service_variant, claim_subtype) -> artifact subdirectory name
_FB_VARIANT_KEYS: dict[tuple[str, str], str] = {
    ("837P", "healthcare"): "837P_healthcare",
    ("837D", "dental"):     "837D_dental",
    ("837I", "home_care"):  "837I_home_care",
}

# Lazy-loaded predictor cache. None = "tried, failed, don't retry this process".
_FB_PREDICTOR_CACHE: dict[str, object] = {}


def _fb_primary_enabled() -> bool:
    """RCM_FB_PRIMARY=false kill switch — reverts to legacy simple_pipeline."""
    v = os.environ.get("RCM_FB_PRIMARY", "true").strip().lower()
    return v in {"true", "1", "yes", "on"}


def _simple_artifact_path() -> Path:
    """Legacy simple_pipeline artifact — used by Option C fallback + shadow."""
    return settings.artifacts_dir / "simple_pipeline" / "simple_denial_model.pkl"


def _fb_predictor_for(variant: str | None, subtype: str | None):
    """Return the cached HealthcarePredictor for (variant, subtype), or None
    if no artifact exists for this variant — caller must fall back to
    simple_pipeline (Option C)."""
    if variant is None:
        return None
    key = _FB_VARIANT_KEYS.get((variant, subtype or ""))
    if key is None:
        return None
    if key in _FB_PREDICTOR_CACHE:
        return _FB_PREDICTOR_CACHE[key]
    path = _FB_ARTIFACT_ROOT / key
    if not path.is_dir():
        _FB_PREDICTOR_CACHE[key] = None
        return None
    try:
        from rcm.ml.predictor import HealthcarePredictor
        pred = HealthcarePredictor.load(path)
        _FB_PREDICTOR_CACHE[key] = pred
        logger.info("Loaded FB predictor: %s/%s from %s", variant, subtype, path)
        return pred
    except Exception as exc:
        logger.warning("Failed to load FB predictor %s/%s: %s", variant, subtype, exc)
        _FB_PREDICTOR_CACHE[key] = None
        return None


def _any_fb_artifact_exists() -> bool:
    """True iff at least one FB artifact directory is present on disk.
    Used by the 503 'no model yet' check post-cutover."""
    if not _FB_ARTIFACT_ROOT.is_dir():
        return False
    for key in _FB_VARIANT_KEYS.values():
        if (_FB_ARTIFACT_ROOT / key).is_dir():
            return True
    return False


def _read_bundle_schema(artifact_dir: Path) -> dict | None:
    """Lightweight peek at a bundle's ``feature_schema.json`` without loading
    the booster. Used by the reload endpoint to report what's on disk."""
    schema_path = artifact_dir / "feature_schema.json"
    if not schema_path.is_file():
        return None
    try:
        import json
        return json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _inventory_available_bundles() -> list[dict]:
    """Enumerate every (variant, subtype) FB artifact directory present and
    return its model_version / threshold / FE version. Read-only; no booster
    load. Order matches ``_FB_VARIANT_KEYS`` so the response is stable."""
    out: list[dict] = []
    for (variant, subtype), key in _FB_VARIANT_KEYS.items():
        artifact_dir = _FB_ARTIFACT_ROOT / key
        if not artifact_dir.is_dir():
            continue
        schema = _read_bundle_schema(artifact_dir) or {}
        out.append({
            "service_variant": variant,
            "claim_subtype":   subtype,
            "artifact_dir":    str(artifact_dir),
            "model_version":   schema.get("model_version"),
            "feature_engineering_version": schema.get("feature_engineering_version"),
            "calibrator_version": schema.get("calibrator_version"),
            "decision_threshold": (
                float(schema["decision_threshold"])
                if schema.get("decision_threshold") is not None else None
            ),
            "training_size":      schema.get("training_size"),
            "training_prevalence":schema.get("training_prevalence"),
        })
    return out


def _current_simple_model_version() -> str:
    """Used for shadow row tagging post-cutover."""
    try:
        from rcm.ml.simple_pipeline import load_artifact
        a = load_artifact(_simple_artifact_path())
        return str(getattr(a, "model_version", "v1.simple.unknown"))
    except Exception:
        return "v1.simple.unknown"


# ---------------------------------------------------------------------------
# Legacy helpers (pre-CR-067 path; retained for the kill-switch)
# ---------------------------------------------------------------------------

def _artifact_path() -> Path:
    """Legacy alias — simple_pipeline artifact path. Kept for the kill-switch
    code-path and any external caller that imported this symbol."""
    return _simple_artifact_path()


def _model_exists() -> bool:
    """Post-CR-067: a usable model exists iff at least one FB variant artifact
    is present, OR (kill-switch case) the legacy simple artifact is present."""
    if _fb_primary_enabled():
        return _any_fb_artifact_exists()
    return _simple_artifact_path().exists()


async def _connect() -> asyncpg.Connection:
    try:
        return await asyncpg.connect(dsn=settings.sync_database_url(), timeout=8)
    except Exception as exc:
        raise HTTPException(503, detail=f"Database unreachable: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Dataset stats
# ---------------------------------------------------------------------------

@router.get("/dataset-stats", response_model=DatasetStatsResponse)
async def dataset_stats() -> DatasetStatsResponse:
    """Counts of adjudicated claims (those with at least one remittance_claim).

    A claim is "denied" if any of its remits carries CLP02='4'; "paid" if any
    remit has paid_amount > 0 and the claim wasn't otherwise marked denied.
    Matches the labeling rule used by the trainer."""
    c = await _connect()
    try:
        total = await c.fetchval("""
            SELECT count(DISTINCT cl.id)
            FROM claims cl JOIN remittance_claims rc ON rc.claim_id = cl.id
            WHERE cl.deleted_at IS NULL
        """)
        denied = await c.fetchval("""
            SELECT count(DISTINCT cl.id)
            FROM claims cl JOIN remittance_claims rc ON rc.claim_id = cl.id
            WHERE cl.deleted_at IS NULL AND rc.claim_status_code = '4'
        """)
    finally:
        await c.close()

    total = int(total or 0)
    denied = int(denied or 0)
    paid = max(0, total - denied)
    return DatasetStatsResponse(
        total=total, denied=denied, paid=paid,
        denial_rate=(denied / total) if total > 0 else 0.0,
    )


# ---------------------------------------------------------------------------
# Train
# ---------------------------------------------------------------------------

@router.post("/train", response_model=TrainModelResponse)
async def train_endpoint() -> TrainModelResponse:
    """Train all supported FeatureBuilder per-variant artifacts.

    Post-CR-067: replaces the legacy simple_pipeline training call. Trains
    837P/healthcare, 837D/dental, 837I/home_care via `train_variant()`.
    Each variant's artifact is written to
    `artifacts/featurebuilder/<variant>_<subtype>/`.

    The aggregated `TrainMetrics` in the response uses the 837P/healthcare
    variant's OOF metrics as the primary signal (the dominant trainable
    variant in this corpus). `split.train_samples` is the SUM across all
    variants; `split.test_samples = 0` because FB uses OOF, not a held-out
    test set.

    Kill switch: `RCM_FB_PRIMARY=false` reverts to the legacy simple_pipeline
    training endpoint behavior.
    """
    if not _fb_primary_enabled():
        return await _legacy_train_simple()

    # CR-080: in-memory run handle. Not persisted (the history grouper
    # reconstructs the group from training_timestamp proximity). Surfaced on
    # the response so the immediate frontend refresh has a stable key.
    training_run_id = f"run-{_uuid.uuid4().hex[:12]}"
    t0 = time.perf_counter()
    from rcm.ml.trainer import train_variant

    _FB_ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    variants_to_train: list[tuple[str, str]] = [
        ("837P", "healthcare"),
        ("837D", "dental"),
        ("837I", "home_care"),
    ]
    per_variant: dict[str, dict] = {}
    total_train_rows = 0
    bundles_written: list[str] = []

    try:
        async with async_session() as session:
            for variant, subtype in variants_to_train:
                key = f"{variant}_{subtype}"
                artifact_dir = _FB_ARTIFACT_ROOT / key
                artifact_dir.mkdir(parents=True, exist_ok=True)
                try:
                    bundle = await train_variant(
                        session, artifact_dir,
                        service_variant=variant, claim_subtype=subtype,
                    )
                except ValueError as exc:
                    # Insufficient data for this variant (e.g., institutional_other)
                    logger.warning("train_variant skipped %s/%s: %s",
                                   variant, subtype, exc)
                    per_variant[key] = {"status": "skipped", "reason": str(exc)}
                    continue
                m = bundle.metrics or {}
                per_variant[key] = {
                    "status": "success",
                    "n_training_rows": int(m.get("n_training_rows", 0)),
                    "n_validation_rows": int(m.get("n_validation_rows", 0)),
                    "n_held_out_rows": int(m.get("n_held_out_rows", 0)),
                    "prevalence": float(m.get("prevalence", 0.0)),
                    # Headline metrics — HELD-OUT (post-CR-075)
                    "accuracy": float(m.get("accuracy_at_threshold", 0.0)),
                    "precision": float(m.get("precision_at_threshold", 0.0)),
                    "recall": float(m.get("recall_at_threshold", 0.0)),
                    "f1": float(m.get("f1_at_threshold", 0.0)),
                    "roc_auc": float(m.get("roc_auc")) if m.get("roc_auc") is not None else None,
                    "pr_auc": float(m.get("pr_auc")) if m.get("pr_auc") is not None else None,
                    "brier_calibrated": float(m.get("brier_calibrated", 0.0)),
                    "positive_rate": float(m.get("positive_rate", 0.0)),
                    "decision_threshold": float(bundle.decision_threshold),
                    "model_version": bundle.model_version,
                    # Three diagnostic blocks (full set of metrics per slice)
                    "oof_block": m.get("oof"),
                    "validation_block": m.get("validation"),
                    "held_out_block": m.get("held_out"),
                }
                total_train_rows += int(m.get("n_training_rows", 0))
                bundles_written.append(key)
    except NoTrainingDataError as exc:
        raise HTTPException(400, detail=str(exc))
    except Exception as exc:
        logger.exception("FB training failed")
        raise HTTPException(500, detail=f"Training failed: {type(exc).__name__}: {exc}")

    if not bundles_written:
        raise HTTPException(
            400,
            detail="No FB variants could be trained — every per-variant call "
                   "raised ValueError (likely insufficient labelled data).",
        )

    # Force-invalidate the cache so the next predict reloads fresh artifacts
    _FB_PREDICTOR_CACHE.clear()

    duration = round(time.perf_counter() - t0, 3)

    # Use 837P/healthcare as the primary metrics surface (dominant variant)
    primary = per_variant.get("837P_healthcare") or next(
        (v for v in per_variant.values() if v.get("status") == "success"), {}
    )
    model_version = (
        f"v1.fb.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    )

    # Best-effort metrics row (one per variant)
    await _persist_fb_training_runs(per_variant, duration, model_version)

    # CR-075: build the three diagnostic blocks for the primary variant
    def _to_train_metrics(block: dict | None) -> TrainMetrics | None:
        if not block or block.get("degenerate"):
            return None
        return TrainMetrics(
            accuracy=block.get("accuracy_at_threshold"),
            precision=block.get("precision_at_threshold"),
            recall=block.get("recall_at_threshold"),
            f1=block.get("f1_at_threshold"),
            roc_auc=block.get("roc_auc"),
            pr_auc=block.get("pr_auc"),
            brier=block.get("brier_calibrated"),
        )

    return TrainModelResponse(
        status="success",
        split=TrainSplit(
            train_samples=primary.get("n_training_rows", 0),
            validation_samples=primary.get("n_validation_rows", 0),
            held_out_samples=primary.get("n_held_out_rows", 0),
            test_samples=primary.get("n_held_out_rows", 0),  # v1 frontend alias
        ),
        # Headline metrics are HELD-OUT (honest reporting per CR-075)
        metrics=TrainMetrics(
            accuracy=primary.get("accuracy"),
            precision=primary.get("precision"),
            recall=primary.get("recall"),
            f1=primary.get("f1"),
            roc_auc=primary.get("roc_auc"),
            pr_auc=primary.get("pr_auc"),
            brier=primary.get("brier_calibrated"),
        ),
        training_time_seconds=duration,
        model_version=model_version,
        evaluation=TrainEvaluation(
            oof_metrics=_to_train_metrics(primary.get("oof_block")),
            validation_metrics=_to_train_metrics(primary.get("validation_block")),
            held_out_metrics=_to_train_metrics(primary.get("held_out_block")),
            decision_threshold=primary.get("decision_threshold"),
        ),
        training_run_id=training_run_id,
    )


async def _legacy_train_simple() -> TrainModelResponse:
    """Kill-switch path: train simple_pipeline (pre-CR-067 behavior)."""
    t0 = time.perf_counter()
    try:
        async with async_session() as session:
            result = await run_train(session, _simple_artifact_path())
    except NoTrainingDataError as exc:
        raise HTTPException(400, detail=str(exc))
    except Exception as exc:
        logger.exception("Legacy simple_pipeline training failed")
        raise HTTPException(500, detail=f"Training failed: {type(exc).__name__}: {exc}")
    duration = round(time.perf_counter() - t0, 3)
    await _persist_training_run(result, duration)
    metrics = result.get("metrics") or {}
    split = result.get("split") or {}
    return TrainModelResponse(
        status=result.get("status", "success"),
        split=TrainSplit(
            train_samples=int(split.get("train_samples", 0)),
            test_samples=int(split.get("test_samples", 0)),
        ),
        metrics=TrainMetrics(
            accuracy=metrics.get("accuracy"),
            precision=metrics.get("precision"),
            recall=metrics.get("recall"),
            f1=metrics.get("f1"),
            roc_auc=metrics.get("roc_auc"),
        ),
        training_time_seconds=duration,
        model_version=result.get("model_version"),
    )


async def _persist_fb_training_runs(
    per_variant: dict[str, dict], duration: float, model_version_aggregate: str,
) -> None:
    """Best-effort: one row per trained variant in model_training_metrics.

    CR-076 #5/#6/#7 — `validation_samples`, `total_claims_used` and
    `model_version` are now derived from the actual per-variant bundle, not
    hardcoded.
    """
    try:
        from sqlalchemy import insert
        from rcm.models.ml_pipeline import ModelTrainingMetric
        async with async_session() as session:
            for key, m in per_variant.items():
                if m.get("status") != "success":
                    continue
                variant, subtype = key.split("_", 1)
                n_train = int(m.get("n_training_rows", 0))
                n_val   = int(m.get("n_validation_rows", 0))
                n_held  = int(m.get("n_held_out_rows", 0))
                bundle_version = str(m.get("model_version") or model_version_aggregate)
                await session.execute(insert(ModelTrainingMetric).values(
                    training_id=_uuid.uuid4(),
                    service_variant=variant,
                    claim_subtype=subtype,
                    training_timestamp=datetime.now(timezone.utc),
                    total_claims_used=n_train + n_val + n_held,     # CR-076 #6
                    training_samples=n_train,
                    validation_samples=n_val,                       # CR-076 #5
                    metrics=m,
                    hyperparameters={"n_estimators": 200, "max_depth": 5,
                                      "learning_rate": 0.05,
                                      "calibration": "isotonic"},
                    calibration_method="isotonic",
                    decision_threshold=float(m.get("decision_threshold", 0.5)),
                    training_duration_seconds=duration,
                    status="success",
                    model_version=bundle_version,                   # CR-076 #7
                    feature_engineering_version="fb_v1",
                    artifact_paths={"dir": str(_FB_ARTIFACT_ROOT / key)},
                ))
            await session.commit()
    except Exception:
        logger.exception("Failed to persist FB training run rows (non-fatal)")


async def _persist_training_run(result: dict, duration: float) -> None:
    """Best-effort insert into model_training_metrics so the History card on
    the upload page picks up the new run. Swallows failures — the model is on
    disk regardless of whether we log it."""
    try:
        from sqlalchemy import insert
        from rcm.models.ml_pipeline import ModelTrainingMetric

        async with async_session() as session:
            split = result.get("split") or {}
            metrics = dict(result.get("metrics") or {})
            metrics["prevalence"] = (
                (result.get("n_denied") or 0)
                / max(1, (result.get("n_denied") or 0) + (result.get("n_paid") or 0))
            )
            await session.execute(insert(ModelTrainingMetric).values(
                training_id=_uuid.uuid4(),
                service_variant="ALL",
                claim_subtype=None,
                training_timestamp=datetime.now(timezone.utc),
                total_claims_used=int(split.get("train_samples", 0)
                                      + split.get("test_samples", 0)),
                training_samples=int(split.get("train_samples", 0)),
                validation_samples=int(split.get("test_samples", 0)),
                metrics=metrics,
                hyperparameters={"n_estimators": 200, "max_depth": 4,
                                  "learning_rate": 0.08},
                calibration_method="isotonic",
                decision_threshold=0.5,
                training_duration_seconds=duration,
                status="success",
                model_version=result.get("model_version") or "v1.simple",
                feature_engineering_version="simple_v1",
                artifact_paths={"pickle": str(_artifact_path())},
            ))
            await session.commit()
    except Exception:
        logger.exception("Failed to persist training run (non-fatal)")


# ---------------------------------------------------------------------------
# Reload bundles — CR-081 Issue A
# ---------------------------------------------------------------------------

@router.post(
    "/reload-bundles",
    summary="Invalidate the per-variant FB predictor cache",
    description=(
        "Clears the in-process FB predictor cache so the next predict call "
        "lazy-loads bundles fresh from `artifacts/featurebuilder/`. Call "
        "this after promoting tuned candidates with `cr079_promote.py --apply` "
        "(or any other on-disk swap) so the running uvicorn picks up the new "
        "weights without a restart."
    ),
)
async def reload_bundles() -> dict:
    """CR-081 Issue A — clears _FB_PREDICTOR_CACHE and returns the on-disk
    bundle inventory the next predict will load from."""
    cleared = len(_FB_PREDICTOR_CACHE)
    _FB_PREDICTOR_CACHE.clear()
    available = _inventory_available_bundles()
    logger.info(
        "reload-bundles: cleared %d cached predictor(s); %d bundle(s) on disk",
        cleared, len(available),
    )
    return {
        "status":               "ok",
        "cleared_predictors":   cleared,
        "available_bundles":    available,
        "fb_primary_enabled":   _fb_primary_enabled(),
    }


# ---------------------------------------------------------------------------
# Predict
# ---------------------------------------------------------------------------

def _no_model_503() -> HTTPException:
    return HTTPException(
        503,
        detail=(
            "No trained model yet — click 'Train Model' below first. "
            f"Looked at: {_artifact_path()}"
        ),
    )


@router.post("/predict-file/{edi_file_id}", response_model=PredictFileResponse)
async def predict_file(edi_file_id: int) -> PredictFileResponse:
    if not _model_exists():
        raise _no_model_503()

    # Kill switch: revert to pre-CR-067 simple_pipeline path
    if not _fb_primary_enabled():
        return await _legacy_predict_file_simple(edi_file_id)

    try:
        async with async_session() as session:
            scored, fb_id_set = await _predict_file_via_fb(session, edi_file_id)
    except Exception as exc:
        logger.exception("predict_file failed (FB primary path)")
        raise HTTPException(500, detail=f"Prediction failed: {type(exc).__name__}: {exc}")

    # CR-076 #4: 404 when the edi_file has no claims at all (missing id or
    # an edi_file without scoreable claims like an 835 row).
    if not scored:
        c = await _connect()
        try:
            ef_exists = await c.fetchval(
                "SELECT 1 FROM edi_files WHERE id = $1 AND deleted_at IS NULL",
                edi_file_id,
            )
        finally:
            await c.close()
        if not ef_exists:
            raise HTTPException(404, detail=f"edi_file {edi_file_id} not found")
        # File exists but has 0 scoreable claims (e.g. 835 with no claims_count).
        # Return the empty 200 to preserve frontend behaviour on legitimately
        # empty files.

    # Inverted shadow logging: FB-scored claims → featurebuilder/production +
    # simple_pipeline/shadow; Option-C-fallback claims → simple_pipeline/
    # production (no FB shadow because no FB predictor exists for those
    # variants). CR-076 #1 + #2.
    try:
        from rcm.ml.shadow import log_paired_predictions_fb_primary
        async with async_session() as session:
            shadow_result = await log_paired_predictions_fb_primary(
                session, edi_file_id, scored,
                fb_scored_claim_ids=fb_id_set,
                simple_model_version=_current_simple_model_version(),
            )
        logger.info("shadow logging (fb-primary): %s", shadow_result)
    except Exception:
        logger.warning("shadow logging failed for file %s", edi_file_id, exc_info=True)

    buckets = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for s in scored:
        buckets[s.risk_level] = buckets.get(s.risk_level, 0) + 1
    high_risk = [
        {
            "claim_id": s.claim_id,
            "claim_number": s.claim_number,
            "payer_name": s.payer_name,
            "service_variant": s.service_variant,
            "claim_subtype": s.claim_subtype,
            "risk_score": s.risk_score,
            "risk_level": s.risk_level,
            "top_denial_reasons": s.top_denial_reasons,
        }
        for s in scored if s.risk_level == "HIGH"
    ]
    return PredictFileResponse(
        edi_file_id=edi_file_id,
        predicted_claims=len(scored),
        risk_summary=buckets,
        high_risk_claims=high_risk,
    )


async def _legacy_predict_file_simple(edi_file_id: int) -> PredictFileResponse:
    """Kill-switch path: pre-CR-067 simple_pipeline behaviour."""
    if not _simple_artifact_path().exists():
        raise _no_model_503()
    try:
        async with async_session() as session:
            scored = await run_predict_file(session, _simple_artifact_path(), edi_file_id)
    except Exception as exc:
        logger.exception("legacy predict_file failed")
        raise HTTPException(500, detail=f"Prediction failed: {type(exc).__name__}: {exc}")
    try:
        from rcm.ml.shadow import log_paired_predictions
        async with async_session() as session:
            await log_paired_predictions(
                session, edi_file_id, scored,
                simple_model_version=_current_simple_model_version(),
            )
    except Exception:
        logger.warning("legacy shadow logging failed", exc_info=True)
    buckets = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for s in scored:
        buckets[s.risk_level] = buckets.get(s.risk_level, 0) + 1
    high_risk = [
        {"claim_id": s.claim_id, "claim_number": s.claim_number,
         "payer_name": s.payer_name, "service_variant": s.service_variant,
         "claim_subtype": s.claim_subtype, "risk_score": s.risk_score,
         "risk_level": s.risk_level, "top_denial_reasons": s.top_denial_reasons}
        for s in scored if s.risk_level == "HIGH"
    ]
    return PredictFileResponse(
        edi_file_id=edi_file_id, predicted_claims=len(scored),
        risk_summary=buckets, high_risk_claims=high_risk,
    )


async def _predict_file_via_fb(
    session, edi_file_id: int,
) -> tuple[list[ScoredClaim], set[int]]:
    """Per-variant FB dispatch for an entire file.

    Buckets the file's claims by (service_variant, claim_subtype). For each
    bucket with a loadable FB predictor, runs that predictor and converts
    PredictionResult → ScoredClaim. For buckets without a predictor (e.g.,
    institutional_other) or where the predictor fails to load, falls back to
    simple_pipeline.predict_file (Option C).

    Returns:
        (scored_claims, fb_scored_claim_ids) — the latter is the set of
        claim_ids actually scored by FB (everything else came from the
        simple_pipeline Option-C fallback). The caller passes this set to
        the shadow logger so prediction_log attribution is truthful.
    """
    # 1. Load claim metadata for this file
    c = await _connect()
    try:
        rows = await c.fetch(
            """
            SELECT cl.id, cl.claim_number, cl.service_variant, cl.claim_subtype,
                   cl.payer_id, p.canonical_name AS payer_name
              FROM claims cl
              LEFT JOIN payers p ON p.id = cl.payer_id
             WHERE cl.edi_file_id = $1 AND cl.deleted_at IS NULL
             ORDER BY cl.id
            """,
            edi_file_id,
        )
    finally:
        await c.close()
    if not rows:
        return [], set()

    fb_bucket: dict[tuple[str, str], list[int]] = {}
    fallback_ids: list[int] = []
    meta: dict[int, dict] = {}
    for r in rows:
        cid = int(r["id"])
        v = r["service_variant"]
        st = r["claim_subtype"]
        meta[cid] = {
            "claim_number": r["claim_number"],
            "payer_name": r["payer_name"],
            "service_variant": v,
            "claim_subtype": st,
        }
        if _fb_predictor_for(v, st) is not None:
            fb_bucket.setdefault((v, st), []).append(cid)
        else:
            fallback_ids.append(cid)

    scored_by_id: dict[int, ScoredClaim] = {}

    # 2. FB per-variant scoring
    from rcm.ml.shadow import _load_predict_corpus
    for (v, st), ids in fb_bucket.items():
        predictor = _fb_predictor_for(v, st)
        if predictor is None:
            fallback_ids.extend(ids)
            continue
        try:
            df = await _load_predict_corpus(session, ids)
            if df.empty:
                fallback_ids.extend(ids)
                continue
            results = await predictor.predict(session, df)
        except Exception as exc:
            logger.warning("FB predict failed for %s/%s (%d claims): %s — "
                           "falling back to simple_pipeline", v, st, len(ids), exc)
            fallback_ids.extend(ids)
            continue
        for pr in results:
            if pr.claim_id is None:
                continue
            cid = int(pr.claim_id)
            m = meta.get(cid, {})
            scored_by_id[cid] = ScoredClaim(
                claim_id=cid,
                claim_number=str(m.get("claim_number") or pr.claim_number or ""),
                payer_name=m.get("payer_name"),
                service_variant=str(m.get("service_variant") or pr.service_variant),
                claim_subtype=str(m.get("claim_subtype") or pr.claim_subtype),
                risk_score=float(pr.risk_score),
                risk_level=pr.risk_level,
                # CR-078 / CR-078A: business-language denial reasons. The
                # renderer collapses many FB feature names into 11 review-
                # oriented sentences, dedupes by bucket, and caps at 5
                # distinct reasons. ``claim_subtype`` lets the renderer pick
                # variant-aware wording (e.g. "Clinical documentation may
                # require review." for healthcare vs. "Treatment documentation
                # may require review." for dental). Raw FB feature names
                # never leave this boundary.
                top_denial_reasons=render_risk_factors(
                    pr.top_risk_factors, top_k=5,
                    claim_subtype=str(m.get("claim_subtype") or pr.claim_subtype or ""),
                ),
            )

    # 3. Option C fallback — run simple_pipeline once for the whole file,
    #    take its scored entries only for the un-FB-routed claim IDs.
    if fallback_ids:
        try:
            if _simple_artifact_path().exists():
                all_simple = await run_predict_file(
                    session, _simple_artifact_path(), edi_file_id,
                )
                fb_id_set = set(scored_by_id.keys())
                for s in all_simple:
                    if int(s.claim_id) in fb_id_set:
                        continue
                    scored_by_id[int(s.claim_id)] = s
            else:
                logger.warning(
                    "Option C fallback for %d claims skipped — simple_pipeline "
                    "artifact missing", len(fallback_ids),
                )
        except Exception:
            logger.exception("Option C fallback via simple_pipeline failed")

    # Truthful FB-attribution set — claims that ACTUALLY got an FB score
    # (not the ones that fell through to simple_pipeline Option-C).
    fallback_set = set(fallback_ids)
    fb_id_set: set[int] = {cid for cid in scored_by_id if cid not in fallback_set}

    # Stable claim-id order
    ordered = [scored_by_id[cid] for cid in sorted(scored_by_id)]
    return ordered, fb_id_set


# ---------------------------------------------------------------------------
# CARC / RARC reason lookup
# ---------------------------------------------------------------------------
# The full WPC code list (308 CARC + 1198 RARC) lives in the `code_masters`
# table — loaded via the data-load script and migration 0011_extend_code_masters.
# We pull every row into a process-local dict at first use; the table changes
# only when WPC publishes quarterly updates, so a long TTL is fine.

_CARC_CACHE: dict[str, str] | None = None
_RARC_CACHE: dict[str, str] | None = None


async def _load_code_cache() -> None:
    """Populate the in-memory CARC / RARC reason lookup from code_masters.

    Prefer denial_reason_plain (the curated short English sentence), fall
    back to short_description / description for codes whose enriched fields
    aren't populated yet."""
    global _CARC_CACHE, _RARC_CACHE
    c = await _connect()
    try:
        rows = await c.fetch(
            """
            SELECT code_type, code,
                   COALESCE(NULLIF(denial_reason_plain, ''),
                            NULLIF(short_description, ''),
                            description) AS reason
            FROM code_masters
            WHERE code_type IN ('CARC', 'RARC')
            """,
        )
    finally:
        await c.close()

    carc, rarc = {}, {}
    for r in rows:
        bucket = carc if r["code_type"] == "CARC" else rarc
        if r["reason"]:
            bucket[r["code"]] = r["reason"]
    _CARC_CACHE, _RARC_CACHE = carc, rarc
    logger.info("Loaded reason cache: CARC=%d  RARC=%d", len(carc), len(rarc))


async def _ensure_code_cache() -> None:
    if _CARC_CACHE is None or _RARC_CACHE is None:
        await _load_code_cache()


def _invalidate_code_cache() -> None:
    """Manual cache buster — call after re-importing WPC updates."""
    global _CARC_CACHE, _RARC_CACHE
    _CARC_CACHE = _RARC_CACHE = None


# ---------------------------------------------------------------------------
# Hardcoded fallback — used only if the DB cache fails to populate (e.g. the
# table is empty on a fresh dev DB). Kept short on purpose; the real source
# of truth is `code_masters`.
# ---------------------------------------------------------------------------

_CARC_FALLBACK: dict[str, str] = {
    "1":   "The amount was applied to the patient's deductible.",
    "2":   "The amount was applied to the patient's coinsurance.",
    "3":   "The amount was applied to the patient's copay.",
    "4":   "The procedure code is inconsistent with the modifier used.",
    "5":   "The procedure code or type of bill is inconsistent with the place "
           "of service.",
    "6":   "The procedure or revenue code is inconsistent with the patient's "
           "age.",
    "7":   "The procedure or revenue code is inconsistent with the patient's "
           "gender.",
    "8":   "The procedure code is inconsistent with the provider type or "
           "specialty.",
    "9":   "The diagnosis is inconsistent with the patient's age.",
    "10":  "The diagnosis is inconsistent with the patient's gender.",
    "11":  "The diagnosis is inconsistent with the procedure billed.",
    "12":  "The diagnosis is inconsistent with the provider type.",
    "15":  "The authorization number is missing, invalid, or does not apply "
           "to the billed services or provider.",
    "16":  "The claim is missing information the payer needs to adjudicate "
           "(usually a procedure / modifier / diagnosis pointer).",
    "17":  "Requested information was not provided or was insufficient or "
           "incomplete.",
    "18":  "The payer treated this as a duplicate of an earlier claim.",
    "19":  "The payer denied because the claim is work-related and the "
           "workers' comp carrier should be billed.",
    "20":  "The payer says this injury or illness is covered by the liability "
           "carrier.",
    "22":  "The patient is covered by another insurance that should be billed "
           "first.",
    "23":  "The payer says this charge is the responsibility of another payer "
           "(coordination of benefits).",
    "26":  "The expenses were incurred before this coverage began.",
    "27":  "The expenses were incurred after coverage ended.",
    "29":  "The claim was submitted past the payer's timely-filing deadline.",
    "31":  "The patient cannot be identified as our insured.",
    "32":  "The patient does not meet eligibility requirements for the date "
           "of service.",
    "39":  "Services were denied at the time authorization or pre-certification "
           "was requested.",
    "40":  "Charges do not meet the qualifications for emergent or urgent care.",
    "45":  "The charge exceeded the contracted fee schedule with this payer.",
    "49":  "These are routine or screening exam charges not covered under the "
           "patient's plan.",
    "50":  "The payer considers this service not medically necessary based on "
           "the diagnosis submitted.",
    "54":  "Multiple physicians or assistants are not covered for this "
           "procedure.",
    "55":  "The procedure or treatment is deemed experimental or investigational "
           "by the payer.",
    "58":  "The treatment was deemed by the payer to have been rendered in an "
           "inappropriate or invalid place of service.",
    "59":  "The treatment was processed based on multiple or concurrent "
           "procedure rules.",
    "96":  "The service is not covered by the patient's benefit plan.",
    "97":  "The payment for this service is already included in another "
           "service that was paid (bundled).",
    "100": "The payment was already made to the patient or another provider.",
    "107": "The related or qualifying claim or service was not identified on "
           "this claim.",
    "109": "The claim was submitted to the wrong payer or contractor.",
    "110": "The billing date predates the service date.",
    "119": "The benefit limit (frequency or dollar maximum) for this service "
           "has been reached.",
    "125": "The payer made a clerical or submission/billing-error adjustment.",
    "146": "The diagnosis was invalid for the date of service reported.",
    "151": "The payer says the documentation does not support this many units "
           "of service.",
    "167": "The diagnosis submitted is not covered for this service.",
    "170": "The payment is denied when performed or billed by this type of "
           "provider.",
    "171": "The payment is denied when performed or billed by this type of "
           "provider in this type of facility.",
    "181": "The procedure code was invalid on the date of service.",
    "182": "The procedure modifier was invalid on the date of service.",
    "185": "The rendering provider is not eligible to perform the service "
           "billed.",
    "197": "Prior authorization or pre-certification was required but was not "
           "obtained.",
    "198": "Prior authorization or pre-certification limits (visits, dollars, "
           "or units) were exceeded.",
    "199": "The revenue code and procedure code do not match.",
    "204": "The service is not covered under the patient's current benefit "
           "plan at the date of service.",
    "B7":  "The provider was not certified or eligible to be paid for this "
           "service on the date it was provided.",
    "B15": "This service requires a related qualifying service or procedure "
           "to have been performed first.",
    "B16": "The payer says the 'new patient' qualifications were not met.",
    "B20": "The procedure or service was partially or fully furnished by "
           "another provider.",
}


def _carc_sentence(code: str) -> str:
    """Resolve a CARC code → human-readable reason.

    Order of preference: live DB cache → hardcoded fallback dict →
    generic "see CARC reference" message. Caller is responsible for
    `_ensure_code_cache()` having been awaited before this function fires."""
    if _CARC_CACHE is not None and code in _CARC_CACHE:
        return _CARC_CACHE[code]
    if code in _CARC_FALLBACK:
        return _CARC_FALLBACK[code]
    return f"Adjustment reason code {code} — see the payer's CARC reference."


def _rarc_sentence(code: str) -> str:
    """Resolve a RARC (remark) code → human-readable reason."""
    if _RARC_CACHE is not None and code in _RARC_CACHE:
        return _RARC_CACHE[code]
    return f"Remark code {code} — see the payer's RARC reference."


@router.post("/denials-for-file/{edi_file_id}")
async def denials_for_file(edi_file_id: int) -> dict:
    """Return per-claim denial reasons for an 835 file.

    Combines CARC (claim adjustment) + RARC (remark) codes attached to each
    denied remittance into one ordered list of plain-English reasons. Reasons
    come from the `code_masters` table (308 CARC + 1198 RARC loaded from WPC);
    the in-process cache is populated on first request.
    """
    await _ensure_code_cache()

    c = await _connect()
    try:
        remits = await c.fetch(
            """
            SELECT rc.id AS remit_id, rc.claim_id, rc.claim_status_code,
                   rc.paid_amount, rc.billed_amount,
                   cl.claim_number, cl.service_variant, cl.claim_subtype,
                   p.canonical_name AS payer_name
            FROM remittance_claims rc
            LEFT JOIN claims  cl ON cl.id  = rc.claim_id
            LEFT JOIN payers  p  ON p.id   = cl.payer_id
            WHERE rc.edi_file_id = $1
              AND (rc.claim_status_code = '4' OR rc.paid_amount = 0)
            ORDER BY rc.id
            """,
            edi_file_id,
        )
        remit_ids = [int(r["remit_id"]) for r in remits]
        adj_by_remit: dict[int, list[tuple[str, str]]] = {}
        rarc_by_remit: dict[int, list[str]] = {}
        if remit_ids:
            adj_rows = await c.fetch(
                """
                SELECT remittance_claim_id, adjustment_group_code, adjustment_reason_code
                FROM adjustments
                WHERE remittance_claim_id = ANY($1::bigint[])
                ORDER BY id
                """,
                remit_ids,
            )
            for a in adj_rows:
                adj_by_remit.setdefault(int(a["remittance_claim_id"]), []).append(
                    (a["adjustment_group_code"], a["adjustment_reason_code"]),
                )

            rarc_rows = await c.fetch(
                """
                SELECT remittance_claim_id, remark_code
                FROM remark_codes
                WHERE remittance_claim_id = ANY($1::bigint[])
                ORDER BY id
                """,
                remit_ids,
            )
            for r in rarc_rows:
                rarc_by_remit.setdefault(int(r["remittance_claim_id"]), []).append(
                    r["remark_code"],
                )
    finally:
        await c.close()

    denied_claims = []
    for r in remits:
        rid = int(r["remit_id"])
        seen: set[str] = set()
        reasons = []
        # CARC first (the primary denial driver)
        for _grp, code in adj_by_remit.get(rid, []):
            sentence = _carc_sentence(code)
            if sentence in seen:
                continue
            seen.add(sentence)
            reasons.append({
                "feature": f"CARC_{code}",
                "label": f"Reason code {code}",
                "reason": sentence,
                "impact": 1.0,                # CARC reasons are facts, not predictions
                "direction": "denied by payer",
            })
        # RARC second (qualifying remark — usually adds detail to the CARC)
        for code in rarc_by_remit.get(rid, []):
            sentence = _rarc_sentence(code)
            if sentence in seen:
                continue
            seen.add(sentence)
            reasons.append({
                "feature": f"RARC_{code}",
                "label": f"Remark code {code}",
                "reason": sentence,
                "impact": 0.5,
                "direction": "qualifying remark",
            })
        denied_claims.append({
            "claim_id": int(r["claim_id"]) if r["claim_id"] is not None else None,
            "claim_number": r["claim_number"] or "(unmatched)",
            "payer_name": r["payer_name"],
            "service_variant": r["service_variant"],
            "claim_subtype": r["claim_subtype"],
            "risk_score": 1.0,
            "risk_level": "HIGH",
            "top_denial_reasons": reasons,
        })

    return {
        "edi_file_id": edi_file_id,
        "predicted_claims": len(denied_claims),
        "risk_summary": {"HIGH": len(denied_claims), "MEDIUM": 0, "LOW": 0},
        "high_risk_claims": denied_claims,
        "source": "carc_rarc",
    }


@router.post(
    "/reload-code-cache",
    summary="Reload CARC/RARC cache from code_masters (call after WPC import)",
)
async def reload_code_cache() -> dict:
    _invalidate_code_cache()
    await _ensure_code_cache()
    return {
        "status": "ok",
        "carc_codes_loaded": len(_CARC_CACHE or {}),
        "rarc_codes_loaded": len(_RARC_CACHE or {}),
    }


@router.post("/predict-claim/{claim_id}", response_model=PredictClaimResponse)
async def predict_claim(claim_id: int) -> PredictClaimResponse:
    if not _model_exists():
        raise _no_model_503()

    # Look up the claim's edi_file_id, reuse the file-level dispatch, take
    # the single row we care about. Keeps FB vs simple_pipeline logic in
    # one place.
    c = await _connect()
    try:
        file_id = await c.fetchval(
            "SELECT edi_file_id FROM claims WHERE id = $1 AND deleted_at IS NULL",
            claim_id,
        )
    finally:
        await c.close()
    if file_id is None:
        raise HTTPException(404, detail=f"Claim {claim_id} not found")

    # Kill switch: revert to legacy simple_pipeline behaviour
    if not _fb_primary_enabled():
        return await _legacy_predict_claim_simple(claim_id, int(file_id))

    try:
        async with async_session() as session:
            scored, fb_id_set = await _predict_file_via_fb(session, int(file_id))
    except Exception as exc:
        logger.exception("predict_claim failed (FB primary path)")
        raise HTTPException(500, detail=f"Prediction failed: {type(exc).__name__}: {exc}")

    s = next((x for x in scored if x.claim_id == claim_id), None)
    if s is None:
        raise HTTPException(404, detail=f"Claim {claim_id} not eligible for prediction")

    # Inverted shadow logging for the single claim. Failure-isolated.
    # CR-076 #1: pass single-claim fb_scored_claim_ids so attribution is truthful
    single_fb_set = {int(claim_id)} if int(claim_id) in fb_id_set else set()
    try:
        from rcm.ml.shadow import log_paired_predictions_fb_primary
        async with async_session() as session:
            await log_paired_predictions_fb_primary(
                session, int(file_id), [s],
                fb_scored_claim_ids=single_fb_set,
                simple_model_version=_current_simple_model_version(),
            )
    except Exception:
        logger.warning("shadow logging failed for claim %s", claim_id, exc_info=True)

    # Resolve a model_version label from the FB predictor for this claim's variant.
    fb_pred = _fb_predictor_for(s.service_variant, s.claim_subtype)
    if fb_pred is not None:
        model_version = fb_pred.bundle.model_version
    else:
        # The claim was scored via Option C (simple_pipeline fallback)
        model_version = _current_simple_model_version()

    return PredictClaimResponse(
        risk_level=s.risk_level,
        risk_score=float(s.risk_score),
        prediction_timestamp=datetime.now(timezone.utc).isoformat(),
        top_risk_factors=[
            RiskFactorItem(
                feature=r.get("label") or r.get("feature"),
                direction=r.get("direction", "increases denial risk"),
                impact=str(r.get("impact", 0)),
            )
            for r in s.top_denial_reasons
        ],
        unseen_indicators=UnseenIndicators(),
        model_version=model_version,
    )


async def _legacy_predict_claim_simple(claim_id: int, file_id: int) -> PredictClaimResponse:
    """Kill-switch path: pre-CR-067 simple_pipeline predict_claim."""
    if not _simple_artifact_path().exists():
        raise _no_model_503()
    try:
        async with async_session() as session:
            scored = await run_predict_file(session, _simple_artifact_path(), file_id)
    except Exception as exc:
        logger.exception("legacy predict_claim failed")
        raise HTTPException(500, detail=f"Prediction failed: {type(exc).__name__}: {exc}")
    s = next((x for x in scored if x.claim_id == claim_id), None)
    if s is None:
        raise HTTPException(404, detail=f"Claim {claim_id} not eligible for prediction")
    try:
        from rcm.ml.shadow import log_paired_predictions
        async with async_session() as session:
            await log_paired_predictions(
                session, file_id, [s],
                simple_model_version=_current_simple_model_version(),
            )
    except Exception:
        logger.warning("legacy shadow failed for claim %s", claim_id, exc_info=True)
    artifact = load_artifact(_simple_artifact_path())
    return PredictClaimResponse(
        risk_level=s.risk_level,
        risk_score=float(s.risk_score),
        prediction_timestamp=datetime.now(timezone.utc).isoformat(),
        top_risk_factors=[
            RiskFactorItem(
                feature=r.get("label") or r.get("feature"),
                direction=r.get("direction", "increases denial risk"),
                impact=str(r.get("impact", 0)),
            )
            for r in s.top_denial_reasons
        ],
        unseen_indicators=UnseenIndicators(),
        model_version=artifact.model_version,
    )
