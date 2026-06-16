"""GET /api/ml/training-history  +  GET /api/ml/latest-training

Reads from model_training_metrics. Empty when no training has been run yet
(the v1 frontend renders "No training runs recorded yet." for that case)."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, HTTPException, Query

from rcm.core.config import settings
from rcm.schemas.public import TrainingHistoryItem, TrainingHistoryResponse

router = APIRouter()


async def _connect() -> asyncpg.Connection:
    try:
        return await asyncpg.connect(dsn=settings.sync_database_url(), timeout=8)
    except Exception as exc:
        raise HTTPException(503, detail=f"Database unreachable: {type(exc).__name__}: {exc}")


def _row_to_item(r) -> TrainingHistoryItem:
    metrics = r["metrics"] or {}
    if isinstance(metrics, str):
        import json
        try:
            metrics = json.loads(metrics)
        except Exception:
            metrics = {}
    return TrainingHistoryItem(
        training_id=str(r["training_id"]),
        training_timestamp=r["training_timestamp"].isoformat() if r["training_timestamp"] else "",
        model_version=r["model_version"],
        total_claims_used=int(r["total_claims_used"] or 0),
        training_samples=int(r["training_samples"] or 0),
        test_samples=int(r["validation_samples"] or 0),
        accuracy=metrics.get("accuracy"),
        f1_score=metrics.get("f1_at_threshold") or metrics.get("f1"),
        precision=metrics.get("precision_at_threshold") or metrics.get("precision"),
        recall=metrics.get("recall_at_threshold") or metrics.get("recall"),
        roc_auc=metrics.get("roc_auc"),
        denial_rate=metrics.get("prevalence"),
        training_time_seconds=float(r["training_duration_seconds"])
            if r["training_duration_seconds"] is not None else None,
    )


@router.get("/training-history", response_model=TrainingHistoryResponse)
async def training_history(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> TrainingHistoryResponse:
    c = await _connect()
    try:
        total = await c.fetchval("SELECT count(*) FROM model_training_metrics")
        rows = await c.fetch(
            """
            SELECT training_id, training_timestamp, model_version,
                   total_claims_used, training_samples, validation_samples,
                   metrics, training_duration_seconds
            FROM model_training_metrics
            ORDER BY training_timestamp DESC
            LIMIT $1 OFFSET $2
            """,
            limit, skip,
        )
    finally:
        await c.close()

    return TrainingHistoryResponse(
        items=[_row_to_item(r) for r in rows],
        total=int(total or 0),
    )


@router.get("/latest-training", response_model=TrainingHistoryItem | None)
async def latest_training() -> TrainingHistoryItem | None:
    c = await _connect()
    try:
        row = await c.fetchrow(
            """
            SELECT training_id, training_timestamp, model_version,
                   total_claims_used, training_samples, validation_samples,
                   metrics, training_duration_seconds
            FROM model_training_metrics
            ORDER BY training_timestamp DESC
            LIMIT 1
            """,
        )
    finally:
        await c.close()
    return _row_to_item(row) if row else None
