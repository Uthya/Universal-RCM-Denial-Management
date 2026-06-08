"""add_materialized_views: per spec Section 1.2 Domain J, plus the four
new MVs required by the revised denial-driven feature engineering.

  mv_claim_labels                  — pre-computed training labels (the keystone)
  mv_payer_denial_rates            — per payer × variant × subtype
  mv_payer_cpt_denial_rate         — joint encoder source
  mv_provider_denial_profiles      — provider-level features
  mv_lifecycle_outcomes            — fast correction retrieval
  mv_drift_baselines               — per variant snapshot for drift detection

  -- new (revised FE) --
  mv_payer_dx_denial_rate          — payer × primary_dx
  mv_payer_pos_denial_rate         — payer × place_of_service
  mv_cpt_dx_denial_rate            — clinical alignment
  mv_provider_payer_denial_rate    — provider × payer
  mv_provider_cpt_denial_rate      — provider × primary_cpt
  mv_patient_claim_history         — per-patient claim ordering (window-fn base)

All MVs use REFRESH MATERIALIZED VIEW CONCURRENTLY — requires a unique index,
which we add on every one.

Revision ID: 0010_add_materialized_views
Revises: 0009_add_domain_functions
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010_add_materialized_views"
down_revision: str | Sequence[str] | None = "0009_add_domain_functions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- mv_claim_labels: keystone training-set materialization ----
    op.execute(
        """
        CREATE MATERIALIZED VIEW mv_claim_labels AS
        SELECT
            c.id                AS claim_id,
            c.service_variant,
            c.claim_subtype,
            c.payer_id,
            c.billing_provider_id,
            c.rendering_provider_id,
            c.patient_id,
            c.service_from_date,
            CASE
                WHEN bool_or(rc.claim_status_code = '4') THEN 1
                WHEN bool_or(rc.claim_status_code IN ('1','2','3','19','20')) THEN 0
                ELSE NULL
            END AS denied
        FROM claims c
        LEFT JOIN remittance_claims rc ON rc.claim_id = c.id
        WHERE (c.frequency_code IS NULL OR c.frequency_code = '1')
          AND c.service_from_date IS NOT NULL
        GROUP BY
            c.id, c.service_variant, c.claim_subtype,
            c.payer_id, c.billing_provider_id, c.rendering_provider_id,
            c.patient_id, c.service_from_date
        HAVING (
            bool_or(rc.claim_status_code = '4') OR
            bool_or(rc.claim_status_code IN ('1','2','3','19','20'))
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX mv_claim_labels_pkey ON mv_claim_labels (claim_id)")
    op.execute(
        "CREATE INDEX mv_claim_labels_variant_date "
        "ON mv_claim_labels (service_variant, claim_subtype, service_from_date)"
    )

    # ---- mv_payer_denial_rates ----
    op.execute(
        """
        CREATE MATERIALIZED VIEW mv_payer_denial_rates AS
        SELECT
            payer_id, service_variant, claim_subtype,
            count(*)                                      AS volume,
            avg(denied::float)                            AS denial_rate,
            sum(denied)                                   AS denied_count
        FROM mv_claim_labels
        WHERE payer_id IS NOT NULL
        GROUP BY payer_id, service_variant, claim_subtype
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX mv_payer_denial_rates_pkey "
        "ON mv_payer_denial_rates (payer_id, service_variant, claim_subtype)"
    )

    # ---- mv_payer_cpt_denial_rate ----
    op.execute(
        """
        CREATE MATERIALIZED VIEW mv_payer_cpt_denial_rate AS
        WITH primary_cpt AS (
            SELECT DISTINCT ON (cl.claim_id)
                cl.claim_id, cl.procedure_code AS cpt
            FROM claim_lines cl
            WHERE cl.procedure_code IS NOT NULL
            ORDER BY cl.claim_id, cl.line_number
        )
        SELECT
            l.payer_id, l.service_variant, pc.cpt,
            count(*)            AS volume,
            avg(l.denied::float) AS denial_rate
        FROM mv_claim_labels l
        JOIN primary_cpt pc ON pc.claim_id = l.claim_id
        WHERE l.payer_id IS NOT NULL
        GROUP BY l.payer_id, l.service_variant, pc.cpt
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX mv_payer_cpt_denial_rate_pkey "
        "ON mv_payer_cpt_denial_rate (payer_id, service_variant, cpt)"
    )

    # ---- mv_payer_dx_denial_rate (revised FE) ----
    op.execute(
        """
        CREATE MATERIALIZED VIEW mv_payer_dx_denial_rate AS
        WITH primary_dx AS (
            SELECT DISTINCT ON (d.claim_id)
                d.claim_id, d.diagnosis_code AS dx
            FROM diagnoses d
            ORDER BY d.claim_id, d.sequence_number
        )
        SELECT
            l.payer_id, l.service_variant, p.dx,
            count(*)            AS volume,
            avg(l.denied::float) AS denial_rate
        FROM mv_claim_labels l
        JOIN primary_dx p ON p.claim_id = l.claim_id
        WHERE l.payer_id IS NOT NULL
        GROUP BY l.payer_id, l.service_variant, p.dx
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX mv_payer_dx_denial_rate_pkey "
        "ON mv_payer_dx_denial_rate (payer_id, service_variant, dx)"
    )

    # ---- mv_payer_pos_denial_rate ----
    op.execute(
        """
        CREATE MATERIALIZED VIEW mv_payer_pos_denial_rate AS
        WITH primary_pos AS (
            SELECT DISTINCT ON (cl.claim_id)
                cl.claim_id, cl.place_of_service AS pos
            FROM claim_lines cl
            WHERE cl.place_of_service IS NOT NULL
            ORDER BY cl.claim_id, cl.line_number
        )
        SELECT
            l.payer_id, l.service_variant, pp.pos,
            count(*)            AS volume,
            avg(l.denied::float) AS denial_rate
        FROM mv_claim_labels l
        JOIN primary_pos pp ON pp.claim_id = l.claim_id
        WHERE l.payer_id IS NOT NULL
        GROUP BY l.payer_id, l.service_variant, pp.pos
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX mv_payer_pos_denial_rate_pkey "
        "ON mv_payer_pos_denial_rate (payer_id, service_variant, pos)"
    )

    # ---- mv_cpt_dx_denial_rate (clinical alignment) ----
    op.execute(
        """
        CREATE MATERIALIZED VIEW mv_cpt_dx_denial_rate AS
        WITH primary_cpt AS (
            SELECT DISTINCT ON (cl.claim_id) cl.claim_id, cl.procedure_code AS cpt
            FROM claim_lines cl
            WHERE cl.procedure_code IS NOT NULL
            ORDER BY cl.claim_id, cl.line_number
        ),
        primary_dx AS (
            SELECT DISTINCT ON (d.claim_id) d.claim_id, d.diagnosis_code AS dx
            FROM diagnoses d
            ORDER BY d.claim_id, d.sequence_number
        )
        SELECT
            pc.cpt, pd.dx,
            count(*)            AS volume,
            avg(l.denied::float) AS denial_rate
        FROM mv_claim_labels l
        JOIN primary_cpt pc ON pc.claim_id = l.claim_id
        JOIN primary_dx  pd ON pd.claim_id = l.claim_id
        GROUP BY pc.cpt, pd.dx
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX mv_cpt_dx_denial_rate_pkey "
        "ON mv_cpt_dx_denial_rate (cpt, dx)"
    )

    # ---- mv_provider_denial_profiles ----
    op.execute(
        """
        CREATE MATERIALIZED VIEW mv_provider_denial_profiles AS
        SELECT
            billing_provider_id,
            count(*)                              AS total_claims,
            sum(denied)                           AS denied_claims,
            avg(denied::float)                    AS denial_rate,
            count(DISTINCT payer_id)              AS payer_diversity
        FROM mv_claim_labels
        WHERE billing_provider_id IS NOT NULL
        GROUP BY billing_provider_id
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX mv_provider_denial_profiles_pkey "
        "ON mv_provider_denial_profiles (billing_provider_id)"
    )

    # ---- mv_provider_payer_denial_rate ----
    op.execute(
        """
        CREATE MATERIALIZED VIEW mv_provider_payer_denial_rate AS
        SELECT
            billing_provider_id, payer_id,
            count(*)            AS volume,
            avg(denied::float)  AS denial_rate
        FROM mv_claim_labels
        WHERE billing_provider_id IS NOT NULL AND payer_id IS NOT NULL
        GROUP BY billing_provider_id, payer_id
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX mv_provider_payer_denial_rate_pkey "
        "ON mv_provider_payer_denial_rate (billing_provider_id, payer_id)"
    )

    # ---- mv_provider_cpt_denial_rate ----
    op.execute(
        """
        CREATE MATERIALIZED VIEW mv_provider_cpt_denial_rate AS
        WITH primary_cpt AS (
            SELECT DISTINCT ON (cl.claim_id) cl.claim_id, cl.procedure_code AS cpt
            FROM claim_lines cl
            WHERE cl.procedure_code IS NOT NULL
            ORDER BY cl.claim_id, cl.line_number
        )
        SELECT
            l.billing_provider_id, pc.cpt,
            count(*)            AS volume,
            avg(l.denied::float) AS denial_rate
        FROM mv_claim_labels l
        JOIN primary_cpt pc ON pc.claim_id = l.claim_id
        WHERE l.billing_provider_id IS NOT NULL
        GROUP BY l.billing_provider_id, pc.cpt
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX mv_provider_cpt_denial_rate_pkey "
        "ON mv_provider_cpt_denial_rate (billing_provider_id, cpt)"
    )

    # ---- mv_lifecycle_outcomes ----
    op.execute(
        """
        CREATE MATERIALIZED VIEW mv_lifecycle_outcomes AS
        SELECT
            cl.original_claim_id,
            count(*)                            AS chain_length,
            max(cl.iteration_number)            AS final_iteration,
            sum(cl.days_to_resolution)          AS total_days_to_resolution,
            bool_or(c_child.claim_status IN ('paid','partially_paid')) AS eventually_paid
        FROM claim_lifecycles cl
        JOIN claims c_child ON c_child.id = cl.child_claim_id
        GROUP BY cl.original_claim_id
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX mv_lifecycle_outcomes_pkey "
        "ON mv_lifecycle_outcomes (original_claim_id)"
    )

    # ---- mv_patient_claim_history (base for window-function features) ----
    op.execute(
        """
        CREATE MATERIALIZED VIEW mv_patient_claim_history AS
        WITH primary_cpt AS (
            SELECT DISTINCT ON (cl.claim_id) cl.claim_id, cl.procedure_code AS cpt
            FROM claim_lines cl
            WHERE cl.procedure_code IS NOT NULL
            ORDER BY cl.claim_id, cl.line_number
        )
        SELECT
            c.id              AS claim_id,
            c.patient_id,
            c.payer_id,
            c.billing_provider_id,
            c.service_from_date,
            c.total_charge_amount,
            c.claim_status,
            pc.cpt            AS primary_cpt,
            row_number() OVER (PARTITION BY c.patient_id ORDER BY c.service_from_date) AS patient_seq
        FROM claims c
        LEFT JOIN primary_cpt pc ON pc.claim_id = c.id
        WHERE c.patient_id IS NOT NULL AND c.service_from_date IS NOT NULL
        """
    )
    op.execute("CREATE UNIQUE INDEX mv_patient_claim_history_pkey ON mv_patient_claim_history (claim_id)")
    op.execute(
        "CREATE INDEX mv_patient_claim_history_patient_date "
        "ON mv_patient_claim_history (patient_id, service_from_date)"
    )
    op.execute(
        "CREATE INDEX mv_patient_claim_history_patient_payer_date "
        "ON mv_patient_claim_history (patient_id, payer_id, service_from_date)"
    )

    # ---- mv_drift_baselines: per-variant feature-distribution snapshot ----
    op.execute(
        """
        CREATE MATERIALIZED VIEW mv_drift_baselines AS
        SELECT
            service_variant, claim_subtype,
            count(*)                                 AS volume,
            avg(denied::float)                       AS prevalence,
            now()                                    AS snapshot_at
        FROM mv_claim_labels
        GROUP BY service_variant, claim_subtype
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX mv_drift_baselines_pkey "
        "ON mv_drift_baselines (service_variant, claim_subtype)"
    )


def downgrade() -> None:
    for mv in (
        "mv_drift_baselines",
        "mv_patient_claim_history",
        "mv_lifecycle_outcomes",
        "mv_provider_cpt_denial_rate",
        "mv_provider_payer_denial_rate",
        "mv_provider_denial_profiles",
        "mv_cpt_dx_denial_rate",
        "mv_payer_pos_denial_rate",
        "mv_payer_dx_denial_rate",
        "mv_payer_cpt_denial_rate",
        "mv_payer_denial_rates",
        "mv_claim_labels",
    ):
        op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {mv}")
