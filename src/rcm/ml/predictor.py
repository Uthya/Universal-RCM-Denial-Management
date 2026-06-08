"""Healthcare predictor — loads an artifact bundle and produces a
calibrated risk score for a single claim (or a batch).

Uses the SAME FeatureBuilder.transform() that training used. The encoder
and rarity_state come from the artifact bundle. Output matches the
PredictionResult contract in spec §6.3.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xgboost as xgb
from sqlalchemy.ext.asyncio import AsyncSession

from rcm.features.builder import FeatureBuilder
from rcm.features.constants import LOW_PROB_CUTOFF
from rcm.features.registry import (
    FEATURE_REGISTRY,
    FeatureSchemaError,
    validate_feature_frame,
)
from rcm.ml.artifacts import ModelArtifactBundle

logger = logging.getLogger(__name__)


@dataclass
class RiskFactor:
    feature: str
    impact: float
    direction: str   # "risk" / "protective"


@dataclass
class UnseenIndicators:
    payer: bool = False
    cpt: bool = False
    dx: bool = False
    billing_provider: bool = False
    rendering_provider: bool = False
    any: bool = False


@dataclass
class PredictionResult:
    prediction_id: uuid.UUID
    claim_id: int | None
    claim_number: str | None
    risk_score: float                      # calibrated
    raw_risk_score: float
    predicted_label: int                   # 0/1
    risk_level: str                        # HIGH / MEDIUM / LOW
    decision_threshold: float
    service_variant: str
    claim_subtype: str
    model_variant: str
    fell_back_to_global: bool
    model_version: str
    feature_engineering_version: str
    calibrator_version: str | None
    top_risk_factors: list[RiskFactor]
    unseen_indicators: UnseenIndicators
    input_completeness: float              # how many features were non-zero
    reference_data_completeness: float     # mean of avail_* flags
    feature_snapshot: dict[str, Any]       # raw input row
    prediction_timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class HealthcarePredictor:
    """Single-variant predictor for 837P/healthcare. Phase 4 will add
    `PredictorRouter` that wires per-variant predictors together."""

    def __init__(self, bundle: ModelArtifactBundle):
        self.bundle = bundle
        # Pre-fit a FeatureBuilder using the loaded encoder + rarity_state
        self.builder = FeatureBuilder(
            service_variant=bundle.service_variant,
            claim_subtype=bundle.claim_subtype,
            encoder=bundle.encoder,
            rarity_state=bundle.rarity_state,
        )

    @classmethod
    def load(cls, artifact_dir: Path) -> "HealthcarePredictor":
        bundle = ModelArtifactBundle.load(Path(artifact_dir))
        return cls(bundle)

    async def predict(
        self, session: AsyncSession, df: pd.DataFrame,
    ) -> list[PredictionResult]:
        """Run prediction on a batch of claim rows (output of load_training_corpus
        or load_predict_corpus). Returns one PredictionResult per row."""
        if df.empty:
            return []

        X = await self.builder.transform(session, df)
        validate_feature_frame(X, self.bundle.service_variant, self.bundle.claim_subtype)

        # XGBoost predict via DMatrix
        dmat = xgb.DMatrix(X.to_numpy(), feature_names=list(X.columns))
        raw_scores = np.asarray(self.bundle.booster.predict(dmat)).ravel()

        # Calibration
        if self.bundle.calibrator is not None:
            calibrated = np.asarray(self.bundle.calibrator.transform(raw_scores)).ravel()
        else:
            calibrated = raw_scores

        # Per-row SHAP top-N for explainability
        try:
            shap_matrix = self.bundle.booster.predict(dmat, pred_contribs=True)
            # last column is the bias; drop
            shap_matrix = np.asarray(shap_matrix)[:, :-1]
        except Exception as exc:
            logger.warning("SHAP unavailable, falling back to feature_importances: %s", exc)
            shap_matrix = None

        results: list[PredictionResult] = []
        for i, (_, row) in enumerate(df.iterrows()):
            raw = float(raw_scores[i])
            cal = float(calibrated[i])
            label = int(cal >= self.bundle.decision_threshold)
            risk = self._risk_level(cal)
            top = self._top_risk_factors(X.columns, shap_matrix[i] if shap_matrix is not None else None)
            unseen = self._compute_unseen(row)
            avail = float(X.iloc[i].get("reference_data_completeness", 0.0))
            input_complete = float((X.iloc[i].fillna(0).abs() > 1e-9).mean())

            results.append(PredictionResult(
                prediction_id=uuid.uuid4(),
                claim_id=int(row["claim_id"]) if row.get("claim_id") is not None else None,
                claim_number=str(row["claim_number"]) if row.get("claim_number") is not None else None,
                risk_score=cal,
                raw_risk_score=raw,
                predicted_label=label,
                risk_level=risk,
                decision_threshold=self.bundle.decision_threshold,
                service_variant=self.bundle.service_variant,
                claim_subtype=self.bundle.claim_subtype,
                model_variant=f"{self.bundle.service_variant}__{self.bundle.claim_subtype}",
                fell_back_to_global=False,
                model_version=self.bundle.model_version,
                feature_engineering_version=self.bundle.feature_engineering_version,
                calibrator_version=self.bundle.calibrator_version,
                top_risk_factors=top,
                unseen_indicators=unseen,
                input_completeness=input_complete,
                reference_data_completeness=avail,
                feature_snapshot={
                    "payer": row.get("payer_canonical_name"),
                    "primary_cpt": row.get("primary_cpt"),
                    "primary_dx": row.get("primary_dx"),
                    "billing_provider_npi": row.get("billing_provider_npi"),
                    "service_from_date": str(row.get("service_from_date")),
                    "total_charge_amount": float(row.get("total_charge_amount") or 0.0),
                },
            ))
        return results

    async def predict_one(self, session: AsyncSession, df: pd.DataFrame) -> PredictionResult:
        if len(df) != 1:
            raise ValueError(f"predict_one expects exactly 1 row, got {len(df)}")
        results = await self.predict(session, df)
        return results[0]

    # ----- helpers -----
    def _risk_level(self, score: float) -> str:
        if score >= self.bundle.decision_threshold:
            return "HIGH"
        if score >= LOW_PROB_CUTOFF:
            return "MEDIUM"
        return "LOW"

    def _top_risk_factors(self, cols, contribs, k: int = 5) -> list[RiskFactor]:
        if contribs is None:
            return []
        idx = np.argsort(np.abs(contribs))[::-1][:k]
        return [
            RiskFactor(
                feature=str(cols[j]),
                impact=float(contribs[j]),
                direction=("risk" if contribs[j] > 0 else "protective"),
            )
            for j in idx
        ]

    def _compute_unseen(self, row) -> UnseenIndicators:
        state = self.bundle.rarity_state
        u = UnseenIndicators(
            payer=not state.known("payer_canonical_name", row.get("payer_canonical_name")),
            cpt=not state.known("primary_cpt", row.get("primary_cpt")),
            dx=not state.known("primary_dx", row.get("primary_dx")),
            billing_provider=not state.known("billing_provider_npi", row.get("billing_provider_npi")),
            rendering_provider=not state.known("rendering_provider_npi", row.get("rendering_provider_npi")),
        )
        u.any = u.payer or u.cpt or u.dx or u.billing_provider or u.rendering_provider
        return u
