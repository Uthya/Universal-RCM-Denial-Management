"""add_ml_pipeline: Domain G — prediction_log (partitioned), model_training_metrics,
feature_snapshots, model_artifacts.

Revision ID: 0003_add_ml_pipeline
Revises: 0002_add_variant_extensions
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0003_add_ml_pipeline"
down_revision: str | Sequence[str] | None = "0002_add_variant_extensions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _month_bounds(anchor: datetime) -> tuple[str, str, str]:
    start = anchor.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 \
        else start.replace(month=start.month + 1)
    return f"{start:%Y_%m}", start.isoformat(), end.isoformat()


def _ensure_monthly_partitions(parent: str, months_ahead: int = 3) -> None:
    op.execute(f"CREATE TABLE IF NOT EXISTS {parent}_default PARTITION OF {parent} DEFAULT")
    now = datetime.now(timezone.utc)
    for m in range(months_ahead + 1):
        anchor = now + timedelta(days=31 * m)
        suffix, s, e = _month_bounds(anchor)
        op.execute(
            f"CREATE TABLE IF NOT EXISTS {parent}_p{suffix} "
            f"PARTITION OF {parent} FOR VALUES FROM ('{s}') TO ('{e}')"
        )


def upgrade() -> None:
    # Enable pgcrypto for gen_random_uuid() (commonly available; safe IF NOT EXISTS)
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.execute(
        "CREATE TYPE artifact_type AS ENUM "
        "('xgboost_model','calibrator','feature_encoder','feature_schema','distributions')"
    )

    # ---- prediction_log (PARTITIONED) ----
    op.execute(
        """
        CREATE TABLE prediction_log (
            id                          BIGSERIAL,
            prediction_time             TIMESTAMPTZ NOT NULL,
            claim_id                    BIGINT REFERENCES claims(id) ON DELETE SET NULL,
            claim_number                VARCHAR(50),
            prediction_id               UUID NOT NULL DEFAULT gen_random_uuid(),
            predicted_risk              NUMERIC(10,6) NOT NULL,
            predicted_label             INTEGER NOT NULL,
            risk_level                  VARCHAR(10) NOT NULL,
            service_variant             VARCHAR(10),
            claim_subtype               VARCHAR(20),
            model_version               VARCHAR(50) NOT NULL,
            feature_engineering_version VARCHAR(50) NOT NULL,
            calibrator_version          VARCHAR(50),
            decision_threshold          NUMERIC(10,6),
            fell_back_to_global         BOOLEAN NOT NULL DEFAULT FALSE,
            input_completeness          NUMERIC(3,2),
            top_risk_factors            JSONB,
            unseen_indicators           JSONB,
            feature_snapshot            JSONB,
            actual_denied               INTEGER,
            actual_status               VARCHAR(20),
            resolved_at                 TIMESTAMPTZ,
            resolved_by_remittance_id   BIGINT REFERENCES remittance_claims(id) ON DELETE SET NULL,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_prediction_log PRIMARY KEY (id, prediction_time),
            CONSTRAINT uq_prediction_log_pid UNIQUE (prediction_id, prediction_time)
        ) PARTITION BY RANGE (prediction_time)
        """
    )
    op.create_index("ix_prediction_log_pid", "prediction_log", ["prediction_id"])
    op.create_index("ix_prediction_log_claim_resolved", "prediction_log", ["claim_id", "resolved_at"])
    op.create_index("ix_prediction_log_resolved_time", "prediction_log", ["resolved_at", "prediction_time"])
    op.create_index(
        "ix_prediction_log_variant_subtype_time",
        "prediction_log",
        ["service_variant", "claim_subtype", "prediction_time"],
    )
    op.create_index("ix_prediction_log_model_version", "prediction_log", ["model_version"])
    _ensure_monthly_partitions("prediction_log")

    # ---- model_training_metrics ----
    op.create_table(
        "model_training_metrics",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("training_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("service_variant", sa.String(10), nullable=False),
        sa.Column("claim_subtype", sa.String(20), nullable=True),
        sa.Column("training_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_claims_used", sa.Integer(), nullable=True),
        sa.Column("training_samples", sa.Integer(), nullable=True),
        sa.Column("validation_samples", sa.Integer(), nullable=True),
        sa.Column("metrics", postgresql.JSONB(), nullable=True),
        sa.Column("feature_importance", postgresql.JSONB(), nullable=True),
        sa.Column("hyperparameters", postgresql.JSONB(), nullable=True),
        sa.Column("calibration_method", sa.String(20), nullable=True),
        sa.Column("decision_threshold", sa.Numeric(10, 6), nullable=True),
        sa.Column("training_duration_seconds", sa.Numeric(10, 3), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("model_version", sa.String(50), nullable=False),
        sa.Column("feature_engineering_version", sa.String(50), nullable=False),
        sa.Column("artifact_paths", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_model_training_variant_subtype_ts",
        "model_training_metrics",
        ["service_variant", "claim_subtype", "training_timestamp"],
    )
    op.create_index("ix_model_training_status_ts", "model_training_metrics", ["status", "training_timestamp"])

    op.create_table(
        "feature_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "training_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("model_training_metrics.training_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("claim_id", sa.BigInteger(), sa.ForeignKey("claims.id", ondelete="SET NULL"), nullable=True),
        sa.Column("service_variant", sa.String(10), nullable=False),
        sa.Column("feature_vector", postgresql.JSONB(), nullable=False),
        sa.Column("denied", sa.Integer(), nullable=False),
        sa.Column("fold_assignment", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_feature_snapshots_training", "feature_snapshots", ["training_id"])
    op.create_index("ix_feature_snapshots_claim", "feature_snapshots", ["claim_id"])

    op.create_table(
        "model_artifacts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "training_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("model_training_metrics.training_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("service_variant", sa.String(10), nullable=False),
        sa.Column("claim_subtype", sa.String(20), nullable=True),
        sa.Column("artifact_type", postgresql.ENUM(name="artifact_type", create_type=False), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_model_artifacts_variant_type_created",
        "model_artifacts",
        ["service_variant", "artifact_type", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("model_artifacts")
    op.drop_table("feature_snapshots")
    op.drop_table("model_training_metrics")
    op.execute("DROP TABLE IF EXISTS prediction_log CASCADE")
    op.execute("DROP TYPE IF EXISTS artifact_type")
