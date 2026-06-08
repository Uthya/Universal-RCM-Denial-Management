"""Minimal ML layer for Phase 3 vertical-slice validation.

NOT a production training pipeline — this is the smallest possible path
that lets us prove train/predict parity end-to-end with the FE layer:

    train_healthcare_model(session, *, …) -> ModelArtifactBundle
    HealthcarePredictor.load(artifact_dir).predict_one(claim_features) -> PredictionResult

Optuna search, fold-aware calibration with multiple methods, threshold
sweep with PR-floor, drift baselines, etc. are intentionally OUT of scope
for this phase. Phase 4 (ML proper) lifts those concerns.
"""

from rcm.ml.artifacts import ModelArtifactBundle
from rcm.ml.predictor import HealthcarePredictor, PredictionResult
from rcm.ml.trainer import train_healthcare_model

__all__ = [
    "HealthcarePredictor",
    "ModelArtifactBundle",
    "PredictionResult",
    "train_healthcare_model",
]
