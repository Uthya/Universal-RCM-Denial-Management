"""add_recency_denial_mvs: CR-126B Bayesian-smoothed recency denial rates.

Adds two new materialized views:
  mv_payer_denial_rates_recent_2k — most-recent-2,000 claims per payer
  mv_payer_denial_rates_90d       — claims in the last 90 days per payer

Both store raw ``volume`` + ``denied_count``. Bayesian smoothing
(α=25, prior=0.2772) is applied in Python at lookup time so the constants
can be tuned without a migration.

The 90-day window anchors to ``max(service_from_date) FROM mv_claim_labels``
rather than ``now()`` so behaviour matches the audit conventions
(CR-115 / CR-117 / CR-124).

Revision ID: 0018_add_recency_denial_mvs
Revises: 0017_mv_claim_labels_propagated
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "0018_add_recency_denial_mvs"
down_revision: str | Sequence[str] | None = "0017_mv_claim_labels_propagated"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE MATERIALIZED VIEW mv_payer_denial_rates_recent_2k AS
        WITH ranked AS (
            SELECT
                payer_id, service_variant, claim_subtype, denied,
                ROW_NUMBER() OVER (
                    PARTITION BY payer_id, service_variant, claim_subtype
                    ORDER BY service_from_date DESC NULLS LAST, claim_id DESC
                ) AS rn
            FROM mv_claim_labels
            WHERE payer_id IS NOT NULL
        )
        SELECT
            payer_id, service_variant, claim_subtype,
            count(*)::bigint    AS volume,
            sum(denied)::bigint AS denied_count
        FROM ranked
        WHERE rn <= 2000
        GROUP BY payer_id, service_variant, claim_subtype
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX mv_payer_denial_rates_recent_2k_pkey "
        "ON mv_payer_denial_rates_recent_2k (payer_id, service_variant, claim_subtype)"
    )

    op.execute(
        """
        CREATE MATERIALIZED VIEW mv_payer_denial_rates_90d AS
        WITH max_date AS (
            SELECT max(service_from_date) AS d FROM mv_claim_labels
        )
        SELECT
            l.payer_id, l.service_variant, l.claim_subtype,
            count(*)::bigint    AS volume,
            sum(l.denied)::bigint AS denied_count
        FROM mv_claim_labels l
        CROSS JOIN max_date m
        WHERE l.payer_id IS NOT NULL
          AND l.service_from_date >= m.d - INTERVAL '90 days'
        GROUP BY l.payer_id, l.service_variant, l.claim_subtype
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX mv_payer_denial_rates_90d_pkey "
        "ON mv_payer_denial_rates_90d (payer_id, service_variant, claim_subtype)"
    )


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_payer_denial_rates_90d")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_payer_denial_rates_recent_2k")
