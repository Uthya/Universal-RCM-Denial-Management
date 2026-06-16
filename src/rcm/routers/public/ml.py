"""GET /api/ml/training-history  +  GET /api/ml/latest-training

Reads from ``model_training_metrics``. CR-080: collapses the per-variant rows
that came from a single ``/train`` invocation into one ``TrainingRunItem`` at
the API boundary — no schema change, no new column. The grouping uses the
existing ``training_timestamp`` field: rows whose timestamps fall inside a
60-second window are treated as one logical run.

Empty when no training has been run yet (the v1 frontend renders
"No training runs recorded yet." for that case).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any

import asyncpg
from fastapi import APIRouter, HTTPException, Query

from rcm.core.config import settings
from rcm.schemas.public import (
    TrainingHistoryResponse,
    TrainingRunItem,
    TrainingVariantMetrics,
)

router = APIRouter()
logger = logging.getLogger(__name__)


# Rows whose ``training_timestamp`` falls inside this window are grouped
# together. The trainer writes the three variant rows from one ``/train``
# call within ~milliseconds of each other; a successive ``/train`` invocation
# takes ≥ ~25 seconds wall-clock (CR-075 baseline 22 s). 60 s leaves a wide
# safety margin without merging unrelated invocations.
GROUPING_WINDOW_SECONDS = 60


async def _connect() -> asyncpg.Connection:
    try:
        return await asyncpg.connect(dsn=settings.sync_database_url(), timeout=8)
    except Exception as exc:
        raise HTTPException(503, detail=f"Database unreachable: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Grouping helpers
# ---------------------------------------------------------------------------

def _parse_metrics(raw: Any) -> dict:
    """asyncpg sometimes hands back JSONB as a str; normalise to dict."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


def _model_version_prefix(model_version: str | None) -> str | None:
    """Strip the ``.<variant>_<subtype>`` suffix from a model_version string.

    Example: ``v1.fb.20260616T060216.837P_healthcare`` → ``v1.fb.20260616T060216``.
    Used to detect a common-prefix across the variants of one run. Returns
    ``None`` if the input is empty or doesn't contain the expected suffix.
    """
    if not model_version:
        return None
    # Suffix is "<variant>_<subtype>" — strip the LAST dot-segment.
    if "." not in model_version:
        return model_version
    head, _, _tail = model_version.rpartition(".")
    return head or model_version


def _row_to_variant_block(row: dict) -> TrainingVariantMetrics:
    metrics = _parse_metrics(row.get("metrics"))
    held = metrics.get("held_out") or {}
    ts = row["training_timestamp"]
    return TrainingVariantMetrics(
        training_id=str(row["training_id"]),
        service_variant=row["service_variant"],
        claim_subtype=row.get("claim_subtype"),
        model_version=row.get("model_version"),
        training_timestamp=ts.isoformat() if ts else "",
        decision_threshold=(
            float(row["decision_threshold"])
            if row.get("decision_threshold") is not None else None
        ),
        total_claims_used=int(row.get("total_claims_used") or 0),
        training_samples=int(row.get("training_samples") or 0),
        validation_samples=int(row.get("validation_samples") or 0),
        accuracy=(
            held.get("accuracy_at_threshold")
            or metrics.get("accuracy_at_threshold")
            or metrics.get("accuracy")
        ),
        f1_score=(
            held.get("f1_at_threshold")
            or metrics.get("f1_at_threshold")
            or metrics.get("f1")
        ),
        precision=(
            held.get("precision_at_threshold")
            or metrics.get("precision_at_threshold")
            or metrics.get("precision")
        ),
        recall=(
            held.get("recall_at_threshold")
            or metrics.get("recall_at_threshold")
            or metrics.get("recall")
        ),
        roc_auc=held.get("roc_auc") or metrics.get("roc_auc"),
        pr_auc=held.get("pr_auc") or metrics.get("pr_auc"),
        denial_rate=metrics.get("prevalence") or held.get("prevalence"),
        training_time_seconds=(
            float(row["training_duration_seconds"])
            if row.get("training_duration_seconds") is not None else None
        ),
    )


def _build_run(group_rows: list[dict]) -> TrainingRunItem:
    """Collapse a list of variant rows (already known to belong to the same
    logical run) into a single ``TrainingRunItem``."""
    # Stable: order variants by training_timestamp asc so 'started_at' is the
    # earliest and 'ended_at' is the latest.
    ordered = sorted(group_rows, key=lambda r: r["training_timestamp"])
    started_ts = ordered[0]["training_timestamp"]
    ended_ts = ordered[-1]["training_timestamp"]

    # Deterministic synthetic run id — uses the earliest member's id so the
    # value is stable across API calls (React keys, deep-links).
    h = hashlib.sha1(str(ordered[0]["id"]).encode("utf-8")).hexdigest()[:12]
    run_id = f"run-{h}"

    # Per-subtype dedupe — if the same variant appears twice inside one
    # window (rare retry case), the LATER training_timestamp wins.
    by_subtype: dict[str, dict] = {}
    for row in ordered:
        key = (row.get("claim_subtype") or row["service_variant"]).lower()
        by_subtype[key] = row  # last write wins
    variant_blocks = {k: _row_to_variant_block(v) for k, v in by_subtype.items()}

    # Status: success if every retained row has status='success'
    statuses = {r.get("status") or "success" for r in by_subtype.values()}
    status = "success" if statuses == {"success"} else "partial"

    # Duration: sum the per-variant durations (None if any are missing).
    durations = [
        float(r["training_duration_seconds"])
        for r in by_subtype.values()
        if r.get("training_duration_seconds") is not None
    ]
    total_seconds = round(sum(durations), 3) if durations else None

    # Model-version group: common prefix across the variants when they share
    # one. Falls back to the latest member's version string otherwise.
    prefixes = {
        _model_version_prefix(r.get("model_version"))
        for r in by_subtype.values()
        if r.get("model_version")
    }
    prefixes.discard(None)
    if len(prefixes) == 1:
        model_version_group: str | None = next(iter(prefixes))
    elif prefixes:
        model_version_group = ", ".join(sorted(prefixes))
    else:
        model_version_group = None

    return TrainingRunItem(
        training_run_id=run_id,
        started_at=started_ts.isoformat() if started_ts else "",
        ended_at=ended_ts.isoformat() if ended_ts else "",
        training_time_seconds=total_seconds,
        model_version_group=model_version_group,
        status=status,
        variant_count=len(variant_blocks),
        variants=variant_blocks,
    )


def _group_rows_into_runs(
    rows: list[dict],
    *,
    window_seconds: int = GROUPING_WINDOW_SECONDS,
) -> list[TrainingRunItem]:
    """Walk rows ordered by training_timestamp DESC; start a new run whenever
    the gap from the current run's earliest timestamp exceeds ``window_seconds``.

    The DB query returns rows newest-first; that order is preserved in the
    output so the UI naturally lists the most recent training run on top.
    """
    if not rows:
        return []
    window = timedelta(seconds=window_seconds)

    runs: list[list[dict]] = []
    current: list[dict] = [rows[0]]
    current_earliest: datetime = rows[0]["training_timestamp"]

    for row in rows[1:]:
        ts = row["training_timestamp"]
        # Anchor the run on the earliest timestamp so a long, multi-second
        # run (e.g. CR-067 837P training took 22 s) doesn't artificially
        # split at the 60s mark.
        if current_earliest - ts <= window:
            current.append(row)
            if ts < current_earliest:
                current_earliest = ts
        else:
            runs.append(current)
            current = [row]
            current_earliest = ts
    runs.append(current)

    return [_build_run(g) for g in runs]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/training-history", response_model=TrainingHistoryResponse)
async def training_history(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> TrainingHistoryResponse:
    """CR-080: ``limit`` and ``skip`` count *runs*, not rows.

    We pull up to ``3 * (skip + limit)`` rows from the DB (enough headroom
    for three-variant runs plus a small safety buffer), group, then slice.
    """
    fetch_count = max((skip + limit) * 4, 12)
    c = await _connect()
    try:
        rows = await c.fetch(
            """
            SELECT id, training_id, training_timestamp, model_version,
                   service_variant, claim_subtype,
                   total_claims_used, training_samples, validation_samples,
                   metrics, training_duration_seconds, decision_threshold,
                   status
            FROM model_training_metrics
            ORDER BY training_timestamp DESC, id DESC
            LIMIT $1
            """,
            fetch_count,
        )
    finally:
        await c.close()

    all_runs = _group_rows_into_runs([dict(r) for r in rows])
    items = all_runs[skip: skip + limit]
    return TrainingHistoryResponse(items=items, total=len(all_runs))


@router.get("/latest-training", response_model=TrainingRunItem | None)
async def latest_training() -> TrainingRunItem | None:
    """CR-080: returns one ``TrainingRunItem`` (the most recent grouped run)."""
    c = await _connect()
    try:
        rows = await c.fetch(
            """
            SELECT id, training_id, training_timestamp, model_version,
                   service_variant, claim_subtype,
                   total_claims_used, training_samples, validation_samples,
                   metrics, training_duration_seconds, decision_threshold,
                   status
            FROM model_training_metrics
            ORDER BY training_timestamp DESC, id DESC
            LIMIT 12
            """,
        )
    finally:
        await c.close()
    runs = _group_rows_into_runs([dict(r) for r in rows])
    return runs[0] if runs else None
