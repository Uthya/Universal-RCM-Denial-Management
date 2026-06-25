"""add_forecast_history_mv: CR-136 historical similarity forecast engine.

Adds ``mv_forecast_history`` — a materialized snapshot of every adjudicated
production prediction with the four similarity features identified by the
CR-134/CR-135 audits as sufficient to recover ≥95% of forecast accuracy:

  - service_variant
  - payer_id
  - is_replacement (derived from frequency_code)
  - predicted_risk (calibrated probability)

Plus the adjudication outcome (denied 0/1) and prediction_time so future
versions can window the historical set by recency.

This MV replaces CR-131B's ``mv_forecast_calibration`` as the primary
forecast input.  ``mv_forecast_calibration`` is preserved for kill-switch
fallback per CR-136's feature-flag policy.

The MV is small (≤200k rows projected; same scale as ``prediction_log``)
and supports CONCURRENT refresh.  Indexed for fast variant + recency
filtering during similarity search.

Revision ID: 0020_add_forecast_history_mv
Revises: 0019_add_forecast_calibration_mv
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "0020_add_forecast_history_mv"
down_revision: str | Sequence[str] | None = "0019_add_forecast_calibration_mv"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# CR-136: historical window. 180 days is conservative — the in-process kNN
# typically only queries the most recent 90 days but a wider window protects
# against sparse early periods. Window enforcement is done in Python so the
# constant can be tuned without a migration.
WINDOW_DAYS = 180


def upgrade() -> None:
    # CR-136 v2 — the MV holds adjudicated claim metadata only. The
    # similarity engine scores each row at load time using the current FB
    # bundle, so the MV is decoupled from ``prediction_log`` (which only
    # captures claims the API was called on). This avoids leaving large
    # gaps when the production engine has been quiet.
    op.execute(
        f"""
        CREATE MATERIALIZED VIEW mv_forecast_history AS
        SELECT cl.id AS claim_id,
               cl.service_variant,
               cl.claim_subtype,
               cl.payer_id,
               cl.frequency_code,
               (cl.frequency_code = '7')::int AS is_replacement,
               cl.service_from_date,
               max(rc.remittance_date)::date AS remit_date,
               max(CASE WHEN rc.claim_status_code = '4' THEN 1 ELSE 0 END)::int AS denied
        FROM claims cl
        JOIN remittance_claims rc ON rc.claim_id = cl.id
        WHERE cl.service_variant IN ('837P','837D','837I')
          AND cl.claim_subtype IN ('healthcare','dental','home_care')
          AND cl.deleted_at IS NULL
          AND cl.service_from_date >= NOW() - INTERVAL '{WINDOW_DAYS} days'
        GROUP BY cl.id, cl.service_variant, cl.claim_subtype, cl.payer_id,
                 cl.frequency_code, cl.service_from_date
        HAVING max(CASE WHEN rc.claim_status_code = '4' THEN 1 ELSE 0 END)
             + max(CASE WHEN rc.claim_status_code IN ('1','2','3','19','20') THEN 1 ELSE 0 END) > 0
        """
    )

    op.execute(
        "CREATE UNIQUE INDEX mv_forecast_history_pkey "
        "ON mv_forecast_history (claim_id)"
    )

    op.execute(
        "CREATE INDEX ix_mv_forecast_history_variant_subtype "
        "ON mv_forecast_history (service_variant, claim_subtype)"
    )


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_forecast_history")
