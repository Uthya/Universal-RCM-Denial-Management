"""Shadow prediction logging (R5 / CR-065).

When the user hits `/api/predictions/predict-file/{id}` or `predict-claim`,
this module runs the FeatureBuilder per-variant predictor in parallel with
the existing simple_pipeline production path and logs BOTH predictions as a
paired group in ``prediction_log``. The user-visible response is the
simple_pipeline result, unchanged.

Failure isolation: any shadow exception is logged but never propagates. The
production response is unaffected.

Kill switch: ``RCM_SHADOW_LOGGING=false`` makes every call an immediate no-op.

Per A-E principles enshrined in CR-061:
  - No new DB tables / MVs / indexes / partitions
  - Writes to existing prediction_log (partitioned monthly)
  - Reuses existing HealthcarePredictor + ModelArtifactBundle paths
  - The 3 shadow columns (pipeline_name / prediction_type / prediction_group_id)
    were added by migration 0013 in a prior session
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rcm.core.config import settings
from rcm.features.dataset import _CLAIM_COLUMNS, _empty_corpus, _load_children
from rcm.ml.artifacts import ModelArtifactBundle
from rcm.ml.predictor import HealthcarePredictor

logger = logging.getLogger(__name__)

ARTIFACT_ROOT = Path("artifacts/featurebuilder")

# Per Option C from R1: institutional_other (16 rows) routes to global fallback,
# not an independent model. No artifact directory for it.
_VARIANT_ARTIFACT_DIRS: dict[tuple[str, str], str] = {
    ("837D", "dental"): "837D_dental",
    ("837P", "healthcare"): "837P_healthcare",
    ("837I", "home_care"): "837I_home_care",
}

# simple_pipeline-side constants for prediction_log fields.
# The simple_pipeline ModelArtifact has model_version but no separate
# feature_engineering_version; we synthesize one stable identifier.
_SIMPLE_FE_VERSION = "simple_v1"
_SIMPLE_CALIBRATOR_VERSION = "simple_isotonic_v1"


def is_shadow_enabled() -> bool:
    """RCM_SHADOW_LOGGING kill switch. Default TRUE.

    Accepted truthy: 'true', '1', 'yes', 'on' (case-insensitive).
    Anything else (including unset 'false') disables shadow logging.
    """
    v = os.environ.get("RCM_SHADOW_LOGGING", "true").strip().lower()
    return v in {"true", "1", "yes", "on"}


class ShadowLogger:
    """Lazy-cached variant predictors. Module-level singleton via
    `get_shadow_logger()`.
    """

    def __init__(self) -> None:
        # None entries mean "tried to load and failed; don't retry this request"
        self._predictors: dict[tuple[str, str], HealthcarePredictor | None] = {}

    def get_predictor(
        self, variant: str, subtype: str,
    ) -> HealthcarePredictor | None:
        key = (variant, subtype)
        if key in self._predictors:
            return self._predictors[key]

        subdir = _VARIANT_ARTIFACT_DIRS.get(key)
        if subdir is None:
            # No artifact for this variant (e.g., institutional_other).
            self._predictors[key] = None
            return None

        path = ARTIFACT_ROOT / subdir
        if not path.is_dir():
            logger.warning(
                "Shadow artifact missing for %s/%s at %s — shadow skipped for "
                "this variant", variant, subtype, path,
            )
            self._predictors[key] = None
            return None

        try:
            predictor = HealthcarePredictor.load(path)
            self._predictors[key] = predictor
            logger.info("Loaded shadow predictor: %s/%s from %s", variant, subtype, path)
            return predictor
        except Exception as exc:
            logger.warning(
                "Failed to load shadow predictor for %s/%s: %s",
                variant, subtype, exc,
            )
            self._predictors[key] = None
            return None


_INSTANCE: ShadowLogger | None = None


def get_shadow_logger() -> ShadowLogger:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = ShadowLogger()
    return _INSTANCE


# ---------------------------------------------------------------------------
# Predict-time corpus loader
# ---------------------------------------------------------------------------
# Mirrors load_training_corpus's column shape but pulls claims by claim_id
# instead of via mv_claim_labels. This is necessary for predict-time scoring
# of claims that may not yet be adjudicated (and therefore not in
# mv_claim_labels). Reuses dataset.py's _CLAIM_COLUMNS + _load_children to
# stay byte-compatible with FeatureBuilder.transform's expected input.

_PREDICT_CORPUS_SQL = f"""
SELECT {_CLAIM_COLUMNS}
FROM claims c
LEFT JOIN payers py  ON py.id = c.payer_id
LEFT JOIN patients pt ON pt.id = c.patient_id
LEFT JOIN providers bpr ON bpr.id = c.billing_provider_id
LEFT JOIN providers rpr ON rpr.id = c.rendering_provider_id
LEFT JOIN subscribers sub ON sub.id = c.subscriber_id
WHERE c.id = ANY(:ids) AND c.deleted_at IS NULL
ORDER BY c.id
"""


async def _load_predict_corpus(
    session: AsyncSession, claim_ids: list[int],
) -> pd.DataFrame:
    """Build a predict-time DataFrame in the same shape as
    load_training_corpus, but pulled by claim_id list (not via
    mv_claim_labels). Reuses dataset.py's _load_children for child tables.
    """
    if not claim_ids:
        return _empty_corpus()

    base_rows = (
        await session.execute(text(_PREDICT_CORPUS_SQL), {"ids": claim_ids})
    ).mappings().all()
    if not base_rows:
        return _empty_corpus()

    base_df = pd.DataFrame(base_rows)
    # Provide a placeholder denied column (predict-time; not consumed by
    # FeatureBuilder.transform, but kept for schema-compat with training)
    base_df["denied"] = pd.Series([0] * len(base_df), dtype="int8")

    actual_ids = [int(r["claim_id"]) for r in base_rows]
    (
        lines_by_claim, dx_by_claim, attachments_by_claim, certs_by_claim,
        amounts_by_claim, episodes_by_claim, transports_by_claim,
    ) = await _load_children(session, actual_ids)

    rolled: list[dict[str, Any]] = []
    for cid in actual_ids:
        lines = lines_by_claim.get(cid, [])
        dxs = dx_by_claim.get(cid, [])
        primary_cpt = next(
            (l["procedure_code"] for l in lines if l.get("procedure_code")),
            None,
        )
        primary_dx = dxs[0]["diagnosis_code"] if dxs else None
        primary_dx_type = dxs[0]["diagnosis_type"] if dxs else None
        primary_pos = next(
            (l["place_of_service"] for l in lines if l.get("place_of_service")),
            None,
        )
        modifiers: list[str] = []
        for l in lines:
            for k in ("modifier1", "modifier2", "modifier3", "modifier4"):
                v = l.get(k)
                if v:
                    modifiers.append(v)
        rolled.append({
            "claim_id": cid,
            "primary_cpt": primary_cpt,
            "primary_dx": primary_dx,
            "primary_dx_type": primary_dx_type,
            "primary_pos": primary_pos,
            "procedure_codes": [l["procedure_code"] for l in lines if l.get("procedure_code")],
            "modifiers": modifiers,
            "revenue_codes": [l["revenue_code"] for l in lines if l.get("revenue_code")],
            "hipps_codes": [l["hipps_code"] for l in lines if l.get("hipps_code")],
            "tooth_numbers": [l["tooth_number"] for l in lines if l.get("tooth_number")],
            "ndc_drug_codes": [l["ndc_drug_code"] for l in lines if l.get("ndc_drug_code")],
            "lines_billed_sum": sum(float(l["billed_amount"] or 0) for l in lines),
            "lines_units_sum": sum(float(l["units"] or 0) for l in lines),
            "claim_lines_count": len(lines),
            "diagnoses_count": len(dxs),
            "diagnoses": [{"code": d["diagnosis_code"], "type": d["diagnosis_type"]} for d in dxs],
            "has_paperwork": bool(attachments_by_claim.get(cid)),
            "attachment_types": [a["report_type_code"] for a in attachments_by_claim.get(cid, [])],
            "has_certification": bool(certs_by_claim.get(cid)),
            "cert_types": [c["certification_type"] for c in certs_by_claim.get(cid, [])],
            "amounts": {a["amount_qualifier"]: float(a["amount"]) for a in amounts_by_claim.get(cid, [])},
            "home_care_episode": episodes_by_claim.get(cid),
            "transport_cert": transports_by_claim.get(cid),
        })

    rolled_df = pd.DataFrame(rolled)
    df = base_df.merge(rolled_df, on="claim_id", how="left")
    df = df.set_index("claim_id", drop=False)
    return df


# ---------------------------------------------------------------------------
# Bulk insert
# ---------------------------------------------------------------------------

_INSERT_SQL = """
INSERT INTO prediction_log (
    prediction_time, claim_id, claim_number, prediction_id,
    predicted_risk, predicted_label, risk_level,
    service_variant, claim_subtype,
    model_version, feature_engineering_version, calibrator_version,
    decision_threshold, fell_back_to_global,
    pipeline_name, prediction_type, prediction_group_id,
    created_at
) VALUES (
    $1::TIMESTAMPTZ, $2::BIGINT, $3::VARCHAR, $4::UUID,
    $5::NUMERIC, $6::INTEGER, $7::VARCHAR,
    $8::VARCHAR, $9::VARCHAR,
    $10::VARCHAR, $11::VARCHAR, $12::VARCHAR,
    $13::NUMERIC, $14::BOOLEAN,
    $15::VARCHAR, $16::VARCHAR, $17::UUID,
    $18::TIMESTAMPTZ
)
"""


def _to_decimal(x: float, places: int = 6) -> Decimal:
    return Decimal(f"{x:.{places}f}")


async def _bulk_insert_rows(rows: list[tuple]) -> int:
    """Direct asyncpg connection for the bulk insert. Avoids the SQLAlchemy
    session because the model class doesn't declare the 3 shadow columns yet
    (intentional — ORM sync is a separate future CR per R5 scope).
    """
    if not rows:
        return 0
    dsn = settings.sync_database_url()
    conn = await asyncpg.connect(dsn=dsn, timeout=10)
    try:
        await conn.executemany(_INSERT_SQL, rows)
    finally:
        await conn.close()
    return len(rows)


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

async def log_paired_predictions(
    session: AsyncSession,
    edi_file_id: int,
    scored_simple: list,
    simple_model_version: str = "v1.simple.unknown",
) -> dict:
    """Score every claim in `scored_simple` via the FeatureBuilder shadow
    path, then bulk-insert paired rows into prediction_log.

    Each pair shares a `prediction_group_id`. Production rows are always
    written; shadow rows are written when the FB predictor returns a result.
    Shadow exceptions are caught per-variant and per-claim — they DO NOT
    propagate to the caller.

    Returns a counts dict for observability:
        {
          "enabled": bool,
          "production_rows": int,
          "shadow_rows": int,
          "shadow_failures": int,
          "variants_processed": [...],
        }
    """
    if not is_shadow_enabled():
        return {
            "enabled": False, "production_rows": 0, "shadow_rows": 0,
            "shadow_failures": 0, "variants_processed": [],
        }
    if not scored_simple:
        return {
            "enabled": True, "production_rows": 0, "shadow_rows": 0,
            "shadow_failures": 0, "variants_processed": [],
        }

    sl = get_shadow_logger()
    now = datetime.now(timezone.utc)

    # Group scored claims by (variant, subtype) for batched shadow scoring
    by_variant: dict[tuple[str, str], list] = {}
    for s in scored_simple:
        key = (s.service_variant, s.claim_subtype)
        by_variant.setdefault(key, []).append(s)

    # Stable per-claim prediction_group_id (used for both prod and shadow rows)
    group_ids: dict[int, uuid.UUID] = {s.claim_id: uuid.uuid4() for s in scored_simple}

    # Build production rows (always written)
    insert_rows: list[tuple] = []
    for s in scored_simple:
        gid = group_ids[s.claim_id]
        insert_rows.append((
            now, s.claim_id, s.claim_number, uuid.uuid4(),  # row prediction_id
            _to_decimal(float(s.risk_score)),
            int(s.risk_score >= 0.5),  # binary label using simple-pipeline's 0.5 threshold
            s.risk_level,
            s.service_variant, s.claim_subtype,
            simple_model_version, _SIMPLE_FE_VERSION, _SIMPLE_CALIBRATOR_VERSION,
            _to_decimal(0.5),  # simple_pipeline's effective decision threshold
            False,  # fell_back_to_global
            "simple_pipeline", "production", gid,
            now,
        ))
    production_count = len(insert_rows)

    # Shadow rows per variant
    shadow_count = 0
    shadow_failures = 0
    variants_processed: list[str] = []
    for (variant, subtype), claims in by_variant.items():
        predictor = sl.get_predictor(variant, subtype)
        if predictor is None:
            # No artifact for this variant (Option C / missing) — skip shadow
            logger.debug("No shadow predictor for %s/%s; %d claims skipped",
                          variant, subtype, len(claims))
            continue

        claim_ids = [c.claim_id for c in claims]
        try:
            df = await _load_predict_corpus(session, claim_ids)
            if df.empty:
                logger.warning("Shadow corpus empty for %s/%s (claim_ids=%s)",
                               variant, subtype, claim_ids[:5])
                shadow_failures += len(claims)
                continue
            results = await predictor.predict(session, df)
        except Exception as exc:
            logger.warning(
                "Shadow predict failed for %s/%s (%d claims): %s",
                variant, subtype, len(claims), exc,
            )
            shadow_failures += len(claims)
            continue

        # Map results back to claim_ids; gather rows
        by_claim_result = {int(r.claim_id): r for r in results if r.claim_id is not None}
        for c in claims:
            r = by_claim_result.get(int(c.claim_id))
            if r is None:
                shadow_failures += 1
                continue
            gid = group_ids[c.claim_id]
            insert_rows.append((
                now, c.claim_id, c.claim_number, r.prediction_id,
                _to_decimal(float(r.risk_score)),
                int(r.predicted_label),
                r.risk_level,
                r.service_variant, r.claim_subtype,
                r.model_version, r.feature_engineering_version, r.calibrator_version,
                _to_decimal(float(r.decision_threshold)),
                bool(r.fell_back_to_global),
                "featurebuilder", "shadow", gid,
                now,
            ))
            shadow_count += 1
        variants_processed.append(f"{variant}/{subtype}")

    # Single bulk insert (production + shadow rows together, one transaction)
    try:
        inserted = await _bulk_insert_rows(insert_rows)
    except Exception as exc:
        logger.warning("Bulk insert to prediction_log failed: %s — paired log skipped this request", exc)
        return {
            "enabled": True, "production_rows": 0, "shadow_rows": 0,
            "shadow_failures": len(scored_simple),
            "variants_processed": variants_processed,
            "insert_error": str(exc),
        }

    return {
        "enabled": True,
        "production_rows": production_count,
        "shadow_rows": shadow_count,
        "shadow_failures": shadow_failures,
        "total_inserted": inserted,
        "variants_processed": variants_processed,
    }


# ---------------------------------------------------------------------------
# CR-067: inverted shadow — production=FeatureBuilder, shadow=simple_pipeline
# ---------------------------------------------------------------------------

async def log_paired_predictions_fb_primary(
    session: AsyncSession,
    edi_file_id: int,
    scored_fb: list,
    fb_scored_claim_ids: set[int] | None = None,
    simple_model_version: str = "v1.simple.unknown",
) -> dict:
    """Post-CR-067 / CR-076 paired logger.

    Production-row attribution (CR-076 #1):
      - For claims whose `claim_id` is in `fb_scored_claim_ids`: production
        row tagged `pipeline_name='featurebuilder'` using the FB bundle's
        actual model_version / threshold (CR-076 #2 + #8).
      - For Option-C fallback claims (claim_id NOT in `fb_scored_claim_ids`):
        production row tagged `pipeline_name='simple_pipeline'` with the
        simple bundle's actual model_version + threshold. These are
        production rows because simple_pipeline IS the production scorer
        for that variant.

    Shadow rows (only for FB-scored claims; no shadow for Option-C — there
    is no FB predictor for those variants by design):
      - Calls simple_pipeline.predict_file once for the whole file, picks
        the entries matching the FB-scored claim ids, emits as shadow.

    Each pair shares a `prediction_group_id`. Failure-isolated: shadow
    exceptions never propagate.
    """
    if not is_shadow_enabled():
        return {"enabled": False, "production_rows": 0, "shadow_rows": 0,
                "shadow_failures": 0, "variants_processed": []}
    if not scored_fb:
        return {"enabled": True, "production_rows": 0, "shadow_rows": 0,
                "shadow_failures": 0, "variants_processed": []}

    fb_scored_claim_ids = fb_scored_claim_ids or set()
    now = datetime.now(timezone.utc)
    group_ids: dict[int, uuid.UUID] = {s.claim_id: uuid.uuid4() for s in scored_fb}

    # CR-076 #2: read FB model_version / threshold per (variant, subtype)
    # from the actual loaded predictor bundles. No more hardcoded strings.
    sl = get_shadow_logger()
    def _fb_meta(variant: str, subtype: str) -> tuple[str, str, str | None, float]:
        pred = sl.get_predictor(variant, subtype)
        if pred is None:
            # Should not happen if fb_scored_claim_ids was populated for this
            # variant, but be defensive
            return ("v1.fb.unknown", _SIMPLE_FE_VERSION, None, 0.5)
        b = pred.bundle
        return (
            str(b.model_version),
            str(b.feature_engineering_version),
            str(b.calibrator_version) if b.calibrator_version else None,
            float(b.decision_threshold),
        )

    insert_rows: list[tuple] = []
    fb_production_count = 0
    fallback_production_count = 0
    variants_processed: set[str] = set()

    for s in scored_fb:
        gid = group_ids[s.claim_id]
        is_fb = int(s.claim_id) in fb_scored_claim_ids

        if is_fb:
            mv, fe, cv, thr = _fb_meta(s.service_variant, s.claim_subtype)
            insert_rows.append((
                now, s.claim_id, s.claim_number, uuid.uuid4(),
                _to_decimal(float(s.risk_score)),
                int(s.risk_level == "HIGH"),
                s.risk_level,
                s.service_variant, s.claim_subtype,
                mv, fe, cv,
                _to_decimal(thr),
                False,
                "featurebuilder", "production", gid,
                now,
            ))
            fb_production_count += 1
        else:
            # CR-076 #1: Option-C fallback was scored by simple_pipeline,
            # so the production row must reflect that, not 'featurebuilder'.
            insert_rows.append((
                now, s.claim_id, s.claim_number, uuid.uuid4(),
                _to_decimal(float(s.risk_score)),
                int(s.risk_score >= 0.5),
                s.risk_level,
                s.service_variant, s.claim_subtype,
                simple_model_version, _SIMPLE_FE_VERSION, _SIMPLE_CALIBRATOR_VERSION,
                _to_decimal(0.5),
                False,
                "simple_pipeline", "production", gid,
                now,
            ))
            fallback_production_count += 1
        variants_processed.add(f"{s.service_variant}/{s.claim_subtype}")

    # Shadow rows — only for FB-scored claims (Option-C claims have no FB
    # counterpart to shadow against; their production row IS the simple result)
    shadow_count = 0
    shadow_failures = 0
    if fb_scored_claim_ids:
        try:
            from rcm.core.config import settings as _settings
            from rcm.ml.simple_pipeline import predict_file as _simple_predict
            artifact_path = _settings.artifacts_dir / "simple_pipeline" / "simple_denial_model.pkl"
            if not artifact_path.exists():
                logger.debug("simple_pipeline artifact missing; FB shadow rows skipped")
            else:
                simple_results = await _simple_predict(session, artifact_path, edi_file_id)
                by_claim = {int(s.claim_id): s for s in simple_results}
                for fb in scored_fb:
                    if int(fb.claim_id) not in fb_scored_claim_ids:
                        continue  # Option-C; no FB shadow comparison
                    ss = by_claim.get(int(fb.claim_id))
                    if ss is None:
                        shadow_failures += 1
                        continue
                    gid = group_ids[fb.claim_id]
                    insert_rows.append((
                        now, ss.claim_id, ss.claim_number, uuid.uuid4(),
                        _to_decimal(float(ss.risk_score)),
                        int(ss.risk_score >= 0.5),
                        ss.risk_level,
                        ss.service_variant, ss.claim_subtype,
                        simple_model_version, _SIMPLE_FE_VERSION, _SIMPLE_CALIBRATOR_VERSION,
                        _to_decimal(0.5),
                        False,
                        "simple_pipeline", "shadow", gid,
                        now,
                    ))
                    shadow_count += 1
        except Exception as exc:
            logger.warning("simple_pipeline shadow failed: %s", exc)
            shadow_failures += len(fb_scored_claim_ids)

    try:
        inserted = await _bulk_insert_rows(insert_rows)
    except Exception as exc:
        logger.warning("Bulk insert to prediction_log failed: %s", exc)
        return {"enabled": True, "production_rows": 0, "shadow_rows": 0,
                "shadow_failures": len(scored_fb),
                "variants_processed": sorted(variants_processed),
                "insert_error": str(exc)}

    return {
        "enabled": True,
        "production_rows": fb_production_count + fallback_production_count,
        "fb_production_rows": fb_production_count,
        "fallback_production_rows": fallback_production_count,
        "shadow_rows": shadow_count,
        "shadow_failures": shadow_failures,
        "total_inserted": inserted,
        "variants_processed": sorted(variants_processed),
    }
