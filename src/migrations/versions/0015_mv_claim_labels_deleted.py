"""filter_mv_claim_labels_deleted (CR-056): exclude soft-deleted claims
from the labelled training corpus.

The existing mv_claim_labels body (migration 0010) does not filter on
``c.deleted_at``. The soft-delete investigation (read-only report, 2026-06-10)
identified 260 claims from named test/QA dataset batches (QX500, FV6, TR15,
P10) that were retracted along with their source 837 EDI files but still
enter the labelled set — including 99 from payer 7 (WELLCARE) and 99 from
payer 13 (AMBETTER), payers with zero active claims.

Every other consumer of `claims` in the codebase already filters
``deleted_at IS NULL`` (simple_pipeline, predictions, recommendations, the
pair-check, the claim-status propagation, every router). This migration
restores `mv_claim_labels` to that convention.

Scope:
  - DROP MATERIALIZED VIEW mv_claim_labels CASCADE  (drops 9 dependents)
  - Recreate mv_claim_labels with the corrected WHERE clause
  - Recreate the 9 dependent MVs (definitions byte-identical to 0010)
  - Recreate all 12 indexes (10 unique + 2 secondary on mv_claim_labels)

Out of scope (per the approved AIR):
  - mv_patient_claim_history — same defect, separate AIR
  - mv_lifecycle_outcomes / mv_drift_baselines refresh — those belong to R0

Revision ID: 0015_mv_claim_labels_deleted
Revises: 0014_drop_audit_log
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0015_mv_claim_labels_deleted"
down_revision: str | Sequence[str] | None = "0014_drop_audit_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# --- shared SQL fragments ---------------------------------------------------

_CORRECTED_MV_CLAIM_LABELS = """
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
WHERE c.deleted_at IS NULL                                  -- CR-056 correction
  AND (c.frequency_code IS NULL OR c.frequency_code = '1')
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

_ORIGINAL_MV_CLAIM_LABELS = """
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

# Dependent MV bodies — IDENTICAL to migration 0010. They reference
# mv_claim_labels by name, so they inherit the corrected (filtered) content
# automatically as soon as mv_claim_labels is recreated.

_DEPENDENT_MVS_SQL = [
    ("mv_payer_denial_rates", """
        CREATE MATERIALIZED VIEW mv_payer_denial_rates AS
        SELECT
            payer_id, service_variant, claim_subtype,
            count(*)                                      AS volume,
            avg(denied::float)                            AS denial_rate,
            sum(denied)                                   AS denied_count
        FROM mv_claim_labels
        WHERE payer_id IS NOT NULL
        GROUP BY payer_id, service_variant, claim_subtype
    """),
    ("mv_payer_cpt_denial_rate", """
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
    """),
    ("mv_payer_dx_denial_rate", """
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
    """),
    ("mv_payer_pos_denial_rate", """
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
    """),
    ("mv_cpt_dx_denial_rate", """
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
    """),
    ("mv_provider_denial_profiles", """
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
    """),
    ("mv_provider_payer_denial_rate", """
        CREATE MATERIALIZED VIEW mv_provider_payer_denial_rate AS
        SELECT
            billing_provider_id, payer_id,
            count(*)            AS volume,
            avg(denied::float)  AS denial_rate
        FROM mv_claim_labels
        WHERE billing_provider_id IS NOT NULL AND payer_id IS NOT NULL
        GROUP BY billing_provider_id, payer_id
    """),
    ("mv_provider_cpt_denial_rate", """
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
    """),
    ("mv_drift_baselines", """
        CREATE MATERIALIZED VIEW mv_drift_baselines AS
        SELECT
            service_variant, claim_subtype,
            count(*)                                 AS volume,
            avg(denied::float)                       AS prevalence,
            now()                                    AS snapshot_at
        FROM mv_claim_labels
        GROUP BY service_variant, claim_subtype
    """),
]

# Indexes — 10 unique (one per MV) + 1 secondary on mv_claim_labels.
_INDEXES_SQL = [
    "CREATE UNIQUE INDEX mv_claim_labels_pkey ON mv_claim_labels (claim_id)",
    "CREATE INDEX mv_claim_labels_variant_date "
    "ON mv_claim_labels (service_variant, claim_subtype, service_from_date)",
    "CREATE UNIQUE INDEX mv_payer_denial_rates_pkey "
    "ON mv_payer_denial_rates (payer_id, service_variant, claim_subtype)",
    "CREATE UNIQUE INDEX mv_payer_cpt_denial_rate_pkey "
    "ON mv_payer_cpt_denial_rate (payer_id, service_variant, cpt)",
    "CREATE UNIQUE INDEX mv_payer_dx_denial_rate_pkey "
    "ON mv_payer_dx_denial_rate (payer_id, service_variant, dx)",
    "CREATE UNIQUE INDEX mv_payer_pos_denial_rate_pkey "
    "ON mv_payer_pos_denial_rate (payer_id, service_variant, pos)",
    "CREATE UNIQUE INDEX mv_cpt_dx_denial_rate_pkey "
    "ON mv_cpt_dx_denial_rate (cpt, dx)",
    "CREATE UNIQUE INDEX mv_provider_denial_profiles_pkey "
    "ON mv_provider_denial_profiles (billing_provider_id)",
    "CREATE UNIQUE INDEX mv_provider_payer_denial_rate_pkey "
    "ON mv_provider_payer_denial_rate (billing_provider_id, payer_id)",
    "CREATE UNIQUE INDEX mv_provider_cpt_denial_rate_pkey "
    "ON mv_provider_cpt_denial_rate (billing_provider_id, cpt)",
    "CREATE UNIQUE INDEX mv_drift_baselines_pkey "
    "ON mv_drift_baselines (service_variant, claim_subtype)",
]


def upgrade() -> None:
    # Cascade drops mv_claim_labels + all 9 dependents + their indexes.
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_claim_labels CASCADE")

    # Recreate mv_claim_labels with the corrected WHERE.
    op.execute(_CORRECTED_MV_CLAIM_LABELS)

    # Recreate the 9 dependents (definitions byte-identical to 0010).
    for _name, sql in _DEPENDENT_MVS_SQL:
        op.execute(sql)

    # Indexes.
    for sql in _INDEXES_SQL:
        op.execute(sql)


def downgrade() -> None:
    # Symmetric reversal: drop CASCADE, recreate with the ORIGINAL body that
    # had no deleted_at filter (i.e., restore migration-0010 state exactly).
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_claim_labels CASCADE")
    op.execute(_ORIGINAL_MV_CLAIM_LABELS)
    for _name, sql in _DEPENDENT_MVS_SQL:
        op.execute(sql)
    for sql in _INDEXES_SQL:
        op.execute(sql)
