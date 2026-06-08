"""Artifact bundle — persist + load contract for a trained model.

Layout:
    artifact_dir/
        model.json              — XGBoost booster
        calibrator.joblib       — sklearn IsotonicRegression
        encoder.joblib          — LeakageSafeTargetEncoder bundle
        rarity_state.joblib     — RarityState (training vocabulary)
        feature_schema.json     — column order, version, threshold, metrics, ref-data state
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import joblib
from sklearn.isotonic import IsotonicRegression
from xgboost import Booster, XGBClassifier

from rcm.features.categories.rarity import RarityState
from rcm.features.constants import FEATURE_ENGINEERING_VERSION
from rcm.features.encoders import LeakageSafeTargetEncoder

logger = logging.getLogger(__name__)


SCHEMA_VERSION = "v1.0.0"


@dataclass
class ModelArtifactBundle:
    """Everything needed at predict time + metrics for monitoring."""
    booster: Booster
    calibrator: IsotonicRegression | None
    encoder: LeakageSafeTargetEncoder
    rarity_state: RarityState
    feature_columns: list[str]
    service_variant: str
    claim_subtype: str
    decision_threshold: float
    feature_engineering_version: str = FEATURE_ENGINEERING_VERSION
    model_version: str = "v1.0.0"
    calibrator_version: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    training_size: int = 0
    training_prevalence: float = 0.0

    # ------------------------------------------------------------------
    def save(self, artifact_dir: Path) -> None:
        artifact_dir = Path(artifact_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)

        # 1. XGBoost booster
        self.booster.save_model(str(artifact_dir / "model.json"))

        # 2. Calibrator
        if self.calibrator is not None:
            joblib.dump(self.calibrator, artifact_dir / "calibrator.joblib")

        # 3. Encoder + rarity state
        self.encoder.save(artifact_dir / "encoder.joblib")
        joblib.dump(self.rarity_state, artifact_dir / "rarity_state.joblib")

        # 4. Schema + metadata
        schema = {
            "schema_version": SCHEMA_VERSION,
            "feature_engineering_version": self.feature_engineering_version,
            "model_version": self.model_version,
            "calibrator_version": self.calibrator_version,
            "service_variant": self.service_variant,
            "claim_subtype": self.claim_subtype,
            "feature_columns": self.feature_columns,
            "decision_threshold": self.decision_threshold,
            "training_size": self.training_size,
            "training_prevalence": self.training_prevalence,
            "metrics": self.metrics,
        }
        (artifact_dir / "feature_schema.json").write_text(
            json.dumps(schema, indent=2, default=str), encoding="utf-8",
        )
        logger.info("Saved artifact bundle to %s", artifact_dir)

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, artifact_dir: Path) -> "ModelArtifactBundle":
        artifact_dir = Path(artifact_dir)
        schema = json.loads((artifact_dir / "feature_schema.json").read_text(encoding="utf-8"))
        if schema["schema_version"] != SCHEMA_VERSION:
            logger.warning(
                "Artifact bundle schema version mismatch: saved=%s expected=%s",
                schema["schema_version"], SCHEMA_VERSION,
            )

        booster = Booster()
        booster.load_model(str(artifact_dir / "model.json"))

        calibrator: IsotonicRegression | None = None
        cal_path = artifact_dir / "calibrator.joblib"
        if cal_path.exists():
            calibrator = joblib.load(cal_path)

        encoder = LeakageSafeTargetEncoder.load(artifact_dir / "encoder.joblib")
        rarity_state: RarityState = joblib.load(artifact_dir / "rarity_state.joblib")

        return cls(
            booster=booster,
            calibrator=calibrator,
            encoder=encoder,
            rarity_state=rarity_state,
            feature_columns=schema["feature_columns"],
            service_variant=schema["service_variant"],
            claim_subtype=schema["claim_subtype"],
            decision_threshold=float(schema["decision_threshold"]),
            feature_engineering_version=schema["feature_engineering_version"],
            model_version=schema["model_version"],
            calibrator_version=schema.get("calibrator_version"),
            metrics=schema.get("metrics", {}),
            training_size=int(schema.get("training_size", 0)),
            training_prevalence=float(schema.get("training_prevalence", 0.0)),
        )
