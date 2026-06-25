"""add_forecast_calibration_mv: CR-131B empirical denial forecast calibration.

Adds ``mv_forecast_calibration`` — bucket-level empirical denial precision per
(service_variant, claim_subtype, score_bucket) over a 90-day window of
adjudicated predictions. Powers the predict-file forecast layer that answers
"of N HIGH claims today, how many will deny once 835s arrive?" without
relying on the model's self-reported risk_score.

Per CR-131A.1 backtest, bucket-level forecasting reduces MAE by 45-87% vs
variant-level on 837P/837I — the lift justifies the storage (~33 rows total).

Row types (encoded via NULL):
  - (variant, subtype, bucket_lo, bucket_hi) — bucket cell
  - (variant, subtype, NULL, NULL)            — variant-level fallback
  - (NULL,    NULL,    NULL, NULL)            — global fallback

Bayesian smoothing constants:
  alpha = kappa * variant_prior
  beta  = kappa * (1 - variant_prior)
  kappa = 10
The prior is the variant's own overall denial rate (or the global rate for
the global row); kappa=10 keeps sparse cells anchored without overwhelming
real signal.

90% CI: normal approximation on the posterior; precise enough for a forecast
layer (CR-131A.1 backtest passed cohort-bias ±2% at scale).

Revision ID: 0019_add_forecast_calibration_mv
Revises: 0018_add_recency_denial_mvs
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "0019_add_forecast_calibration_mv"
down_revision: str | Sequence[str] | None = "0018_add_recency_denial_mvs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Bayesian smoothing strength. Light enough to let real data dominate by N=50.
KAPPA = 10
WINDOW_DAYS = 90


def upgrade() -> None:
    # The MV joins prediction_log to per-claim adjudication outcome and
    # buckets predictions into deciles. We take DISTINCT ON the latest
    # production featurebuilder prediction per claim so duplicate scoring
    # runs (re-predicts, manual triggers) don't double-count.
    op.execute(
        f"""
        CREATE MATERIALIZED VIEW mv_forecast_calibration AS
        WITH adjud AS (
            SELECT DISTINCT ON (pl.claim_id)
                pl.claim_id,
                pl.service_variant,
                pl.claim_subtype,
                pl.predicted_risk::numeric AS predicted_risk,
                rc.denied
            FROM prediction_log pl
            JOIN (
                SELECT claim_id,
                       max(CASE WHEN claim_status_code = '4' THEN 1 ELSE 0 END) AS denied,
                       max(CASE WHEN claim_status_code IN ('1','2','3','19','20') THEN 1 ELSE 0 END) AS approved
                FROM remittance_claims
                GROUP BY claim_id
                HAVING max(CASE WHEN claim_status_code = '4' THEN 1 ELSE 0 END)
                     + max(CASE WHEN claim_status_code IN ('1','2','3','19','20') THEN 1 ELSE 0 END) > 0
            ) rc ON rc.claim_id = pl.claim_id
            WHERE pl.pipeline_name = 'featurebuilder'
              AND pl.prediction_type = 'production'
              AND pl.prediction_time >= NOW() - INTERVAL '{WINDOW_DAYS} days'
              AND pl.service_variant IS NOT NULL
              AND pl.claim_subtype IS NOT NULL
            ORDER BY pl.claim_id, pl.prediction_time DESC
        ),
        variant_prior AS (
            SELECT service_variant, claim_subtype,
                   sum(denied)::numeric / NULLIF(count(*), 0)::numeric AS base_rate
            FROM adjud
            GROUP BY service_variant, claim_subtype
        ),
        global_prior AS (
            SELECT sum(denied)::numeric / NULLIF(count(*), 0)::numeric AS base_rate
            FROM adjud
        ),
        bucketed AS (
            SELECT
                a.service_variant,
                a.claim_subtype,
                LEAST(FLOOR(a.predicted_risk * 10)::int, 9) AS bucket_idx,
                a.denied,
                vp.base_rate AS prior
            FROM adjud a
            JOIN variant_prior vp
                ON vp.service_variant = a.service_variant
                AND vp.claim_subtype = a.claim_subtype
        ),
        bucket_rows AS (
            SELECT
                service_variant,
                claim_subtype,
                (bucket_idx * 0.1)::numeric(3,2)                  AS bucket_lo,
                (LEAST((bucket_idx + 1) * 0.1, 1.0))::numeric(3,2) AS bucket_hi,
                count(*)::int     AS n_adjudicated,
                sum(denied)::int  AS n_denied,
                prior
            FROM bucketed
            GROUP BY service_variant, claim_subtype, bucket_idx, prior
        ),
        variant_rows AS (
            SELECT
                service_variant,
                claim_subtype,
                NULL::numeric(3,2) AS bucket_lo,
                NULL::numeric(3,2) AS bucket_hi,
                count(*)::int      AS n_adjudicated,
                sum(denied)::int   AS n_denied,
                base_rate          AS prior
            FROM adjud
            JOIN variant_prior USING (service_variant, claim_subtype)
            GROUP BY service_variant, claim_subtype, base_rate
        ),
        global_rows AS (
            SELECT
                NULL::varchar(10) AS service_variant,
                NULL::varchar(20) AS claim_subtype,
                NULL::numeric(3,2) AS bucket_lo,
                NULL::numeric(3,2) AS bucket_hi,
                count(*)::int     AS n_adjudicated,
                sum(denied)::int  AS n_denied,
                gp.base_rate      AS prior
            FROM adjud
            CROSS JOIN global_prior gp
            GROUP BY gp.base_rate
        ),
        unioned AS (
            SELECT * FROM bucket_rows
            UNION ALL SELECT * FROM variant_rows
            UNION ALL SELECT * FROM global_rows
        )
        SELECT
            service_variant,
            claim_subtype,
            bucket_lo,
            bucket_hi,
            {WINDOW_DAYS} AS window_days,
            n_adjudicated,
            n_denied,
            -- Bayesian smoothed precision:  (D + kappa*prior) / (N + kappa)
            ((n_denied::numeric + {KAPPA} * COALESCE(prior, 0))
             / (n_adjudicated::numeric + {KAPPA}))::numeric(6,4) AS p_hat,
            -- 90% normal-approximation CI on the posterior.  Var ~ p(1-p)/(N+kappa+1).
            GREATEST(0::numeric,
              ((n_denied::numeric + {KAPPA} * COALESCE(prior, 0)) / (n_adjudicated::numeric + {KAPPA}))
              - 1.645 * sqrt(
                  ((n_denied::numeric + {KAPPA} * COALESCE(prior, 0)) / (n_adjudicated::numeric + {KAPPA}))
                  * (1 - ((n_denied::numeric + {KAPPA} * COALESCE(prior, 0)) / (n_adjudicated::numeric + {KAPPA})))
                  / (n_adjudicated::numeric + {KAPPA} + 1)
              )
            )::numeric(6,4) AS p_hat_lo,
            LEAST(1::numeric,
              ((n_denied::numeric + {KAPPA} * COALESCE(prior, 0)) / (n_adjudicated::numeric + {KAPPA}))
              + 1.645 * sqrt(
                  ((n_denied::numeric + {KAPPA} * COALESCE(prior, 0)) / (n_adjudicated::numeric + {KAPPA}))
                  * (1 - ((n_denied::numeric + {KAPPA} * COALESCE(prior, 0)) / (n_adjudicated::numeric + {KAPPA})))
                  / (n_adjudicated::numeric + {KAPPA} + 1)
              )
            )::numeric(6,4) AS p_hat_hi,
            now() AS last_refreshed_at
        FROM unioned
        """
    )

    # Unique index supports CONCURRENT refresh + sub-ms lookups.  NULL parts
    # of the key are normalised via COALESCE so the three row classes
    # (bucket / variant / global) coexist with a clean PK.
    op.execute(
        """
        CREATE UNIQUE INDEX mv_forecast_calibration_pkey
        ON mv_forecast_calibration (
            COALESCE(service_variant, ''),
            COALESCE(claim_subtype,   ''),
            COALESCE(bucket_lo, -1::numeric),
            window_days
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_forecast_calibration")
