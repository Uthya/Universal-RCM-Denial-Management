"""Model registry endpoint — Home dashboard tile + future ML Models page.

Per CR-041 #6 future-compat: this is the foundation of the ML Models page.
Today it surfaces every (variant, subtype) registered in the FE layer
along with whether a trained model exists for it. When Phase 4 ships
per-variant training, the same endpoint populates the `has_trained_model`
+ `model_version` + `metrics_*` fields without contract changes.

Source: FE registry (`features.registered_variants`) joined to PG's
`model_training_metrics` (latest successful row per variant × subtype).
"""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, HTTPException

from rcm.core.config import settings
from rcm.features import (
    feature_count_by_variant,
    registered_variants,
)
from rcm.schemas.dev import (
    ModelRegistryEntry,
    ModelRegistryResponse,
)

router = APIRouter()


async def _connect() -> asyncpg.Connection:
    try:
        return await asyncpg.connect(dsn=settings.sync_database_url(), timeout=8)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database unreachable: {type(exc).__name__}: {exc}",
        )


@router.get(
    "/registry",
    response_model=ModelRegistryResponse,
    summary="Per-variant model registry (FE-registered ∪ trained)",
    description=(
        "Returns one row per (variant, subtype) in the FE registry. For each, "
        "looks up the latest successful entry in model_training_metrics. When "
        "no model exists yet, `has_trained_model=false` and the metrics fields "
        "are null — empty-state behavior is well-defined, never crashes."
    ),
)
async def models_registry() -> ModelRegistryResponse:
    # FE registry — always available
    registered = registered_variants()
    counts = feature_count_by_variant()

    # Pull latest successful training per (variant, subtype) from DB.
    # Handles empty-state cleanly: returns empty result → all rows fall back
    # to has_trained_model=false.
    latest_by_key: dict[tuple[str, str], dict] = {}
    c = await _connect()
    try:
        rows = await c.fetch("""
            SELECT DISTINCT ON (service_variant, claim_subtype)
                service_variant,
                COALESCE(claim_subtype, '_global') AS claim_subtype,
                model_version,
                to_char(training_timestamp, 'YYYY-MM-DD"T"HH24:MI:SS') AS trained_at,
                training_samples,
                (metrics->>'pr_auc')::float AS pr_auc,
                (metrics->>'f1_at_threshold')::float AS f1,
                decision_threshold::float AS decision_threshold
            FROM model_training_metrics
            WHERE status = 'success'
            ORDER BY service_variant, claim_subtype, training_timestamp DESC
        """)
        for r in rows:
            latest_by_key[(r["service_variant"], r["claim_subtype"])] = dict(r)
    finally:
        await c.close()

    items: list[ModelRegistryEntry] = []
    trained_count = 0
    for variant, subtype in registered:
        # Skip _global pseudo-key in user-facing listing? Include it; consumers
        # can filter — it IS a real model artifact when trained.
        latest = latest_by_key.get((variant, subtype))
        has_trained = latest is not None
        if has_trained:
            trained_count += 1
        items.append(ModelRegistryEntry(
            service_variant=variant,
            claim_subtype=subtype,
            feature_count=counts.get((variant, subtype), 0),
            is_registered=True,
            has_trained_model=has_trained,
            model_version=latest["model_version"] if latest else None,
            trained_at=latest["trained_at"] if latest else None,
            training_size=int(latest["training_samples"]) if latest and latest["training_samples"] is not None else None,
            metrics_pr_auc=latest["pr_auc"] if latest else None,
            metrics_f1=latest["f1"] if latest else None,
            decision_threshold=float(latest["decision_threshold"]) if latest and latest["decision_threshold"] is not None else None,
        ))

    return ModelRegistryResponse(items=items, total=len(items), trained_count=trained_count)
