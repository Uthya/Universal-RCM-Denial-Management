"""add_shadow_logging: extend prediction_log for paired pipeline comparison.

Restoration plan R5: predictions endpoint scores via BOTH simple_pipeline
(production, returned to UI) and FeatureBuilder (shadow, logged only). One
prediction request → two prediction_log rows linked by a shared
`prediction_group_id`.

New columns (all nullable so historical rows aren't disturbed):
  pipeline_name       'simple_pipeline' | 'featurebuilder'
  prediction_type     'production' | 'shadow'
  prediction_group_id uuid — groups paired predictions for the same claim/request

Indices:
  ix_prediction_log_pipeline_type    (pipeline_name, prediction_type)
  ix_prediction_log_group            (prediction_group_id)

This migration is APPLIED only at R5 — when shadow logging actually goes
live. Until then the columns simply don't exist and the existing
prediction_log path is untouched.

Revision ID: 0013_add_shadow_logging
Revises: 0012_add_pending_pair_registry
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "0013_add_shadow_logging"
down_revision: str | Sequence[str] | None = "0012_add_pending_pair_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE prediction_log
            ADD COLUMN IF NOT EXISTS pipeline_name       VARCHAR(40),
            ADD COLUMN IF NOT EXISTS prediction_type     VARCHAR(20),
            ADD COLUMN IF NOT EXISTS prediction_group_id UUID
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_prediction_log_pipeline_type "
        "ON prediction_log (pipeline_name, prediction_type) "
        "WHERE pipeline_name IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_prediction_log_group "
        "ON prediction_log (prediction_group_id) "
        "WHERE prediction_group_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_prediction_log_group")
    op.execute("DROP INDEX IF EXISTS ix_prediction_log_pipeline_type")
    op.execute("""
        ALTER TABLE prediction_log
            DROP COLUMN IF EXISTS prediction_group_id,
            DROP COLUMN IF EXISTS prediction_type,
            DROP COLUMN IF EXISTS pipeline_name
    """)
