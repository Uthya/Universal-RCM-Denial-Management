"""Domain G — ML pipeline: prediction_log, model_training_metrics,
feature_snapshots, model_artifacts.

``prediction_log`` is partitioned monthly by ``prediction_time``.
Every row must carry ``model_version`` + ``calibrator_version`` +
``decision_threshold`` so predictions remain reproducible across retrains
(lessons H5/H6).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from rcm.core.database import Base
from rcm.core.enums import ArtifactType
from rcm.models._mixins import CreatedAtMixin


class PredictionLog(Base):
    """Partitioned monthly by prediction_time."""

    __tablename__ = "prediction_log"

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, nullable=False)
    prediction_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )  # partition key

    claim_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("claims.id", ondelete="SET NULL"), nullable=True
    )
    claim_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    prediction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, default=uuid.uuid4
    )

    predicted_risk: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    predicted_label: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(10), nullable=False)  # HIGH/MEDIUM/LOW

    service_variant: Mapped[str | None] = mapped_column(String(10), nullable=True)
    claim_subtype: Mapped[str | None] = mapped_column(String(20), nullable=True)

    model_version: Mapped[str] = mapped_column(String(50), nullable=False)
    feature_engineering_version: Mapped[str] = mapped_column(String(50), nullable=False)
    calibrator_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    decision_threshold: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)

    fell_back_to_global: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    input_completeness: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    top_risk_factors: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    unseen_indicators: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    feature_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    actual_denied: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_remittance_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("remittance_claims.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        PrimaryKeyConstraint("id", "prediction_time", name="pk_prediction_log"),
        UniqueConstraint("prediction_id", "prediction_time", name="uq_prediction_log_pid"),
        Index("ix_prediction_log_pid", "prediction_id"),
        Index("ix_prediction_log_claim_resolved", "claim_id", "resolved_at"),
        Index("ix_prediction_log_resolved_time", "resolved_at", "prediction_time"),
        Index(
            "ix_prediction_log_variant_subtype_time",
            "service_variant",
            "claim_subtype",
            "prediction_time",
        ),
        Index("ix_prediction_log_model_version", "model_version"),
        {"postgresql_partition_by": "RANGE (prediction_time)"},
    )


class ModelTrainingMetric(Base, CreatedAtMixin):
    __tablename__ = "model_training_metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    training_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, default=uuid.uuid4
    )

    service_variant: Mapped[str] = mapped_column(String(10), nullable=False)
    claim_subtype: Mapped[str | None] = mapped_column(String(20), nullable=True)
    training_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    total_claims_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    training_samples: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validation_samples: Mapped[int | None] = mapped_column(Integer, nullable=True)

    metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    feature_importance: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    hyperparameters: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    calibration_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    decision_threshold: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    training_duration_seconds: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 3), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    model_version: Mapped[str] = mapped_column(String(50), nullable=False)
    feature_engineering_version: Mapped[str] = mapped_column(String(50), nullable=False)
    artifact_paths: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index(
            "ix_model_training_variant_subtype_ts",
            "service_variant",
            "claim_subtype",
            "training_timestamp",
        ),
        Index("ix_model_training_status_ts", "status", "training_timestamp"),
    )


class FeatureSnapshot(Base, CreatedAtMixin):
    """Persisted per-training-row features. Used for drift analysis and to
    re-attribute predictions when a feature definition changes."""

    __tablename__ = "feature_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    training_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("model_training_metrics.training_id", ondelete="CASCADE"),
        nullable=False,
    )
    claim_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("claims.id", ondelete="SET NULL"), nullable=True
    )
    service_variant: Mapped[str] = mapped_column(String(10), nullable=False)
    feature_vector: Mapped[dict] = mapped_column(JSONB, nullable=False)
    denied: Mapped[int] = mapped_column(Integer, nullable=False)
    fold_assignment: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("ix_feature_snapshots_training", "training_id"),
        Index("ix_feature_snapshots_claim", "claim_id"),
    )


class ModelArtifact(Base, CreatedAtMixin):
    __tablename__ = "model_artifacts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    training_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("model_training_metrics.training_id", ondelete="CASCADE"),
        nullable=False,
    )
    service_variant: Mapped[str] = mapped_column(String(10), nullable=False)
    claim_subtype: Mapped[str | None] = mapped_column(String(20), nullable=True)
    artifact_type: Mapped[ArtifactType] = mapped_column(
        Enum(ArtifactType, name="artifact_type", create_type=False, native_enum=True),
        nullable=False,
    )
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (
        Index(
            "ix_model_artifacts_variant_type_created",
            "service_variant",
            "artifact_type",
            "created_at",
        ),
    )
