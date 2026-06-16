"""propagate denial labels from freq=7 replacements onto freq=1 originals.

CR-072: extends `mv_claim_labels` to admit freq=1 originals whose
`(claim_number, payer_id)`-matched freq=7 replacements carry a terminal
CLP02. The denial label is propagated using Strategy A1 ("any denial wins")
ratified by the Label Semantics Validation report.

Background (full chain in CHANGELOG):
  - CR-068A recovered 21 LR1K original 837s lost to the cold-start race.
  - CR-070 backfilled 154 orphan CLP remits.
  - CR-071 added a CTE in `load_training_corpus()` that flipped 1,928
    rows already in `mv_claim_labels` (837P/HC) from paid → denied. But
    9,539 freq=1 originals with NO OWN REMIT were still excluded by the
    MV's HAVING clause — predominantly the 837D and 837I home_care chains
    of pattern `O-- / Rpd`.
  - CR-072 extends the MV itself to admit those chains.

Effect on training corpus (verified read-only pre-migration):
  total rows  43,074 → 52,613   (+9,539)
  denied      1,946  → 11,485   (+9,539, all denied=1 from descendants)
  837P  rate  4.67 % → 21.13 %
  837D  rate  0.00 % → 37.21 % (TRAINABLE)
  837I  rate  0.21 % → 37.60 % (TRAINABLE)

Documented label-noise floor (Label Safety Review):
  Overall   ≈ 3.44 %  (proc-code zero-overlap between original and
                       descendant)
  837D      ≈ 10.55 %
  837P      ≈ 3.08 %
  837I HC   ≈ 0.35 %

Scope:
  - DROP MATERIALIZED VIEW mv_claim_labels CASCADE   (drops 9 dependents)
  - Recreate mv_claim_labels with descendant-aware HAVING + CASE
  - Recreate the 9 dependents (definitions byte-identical to 0010/0015)
  - Recreate all 11 indexes

Out of scope:
  - No new tables, MVs, indexes, or registries
  - `claim_lifecycles` stays empty (CR-068 deferred)
  - `load_training_corpus()` SQL unchanged — CR-071 CTE stays as
    defense-in-depth, redundant but harmless
  - No retraining, no benchmarking
  - No CR-067 cutover

Revision ID: 0017_mv_claim_labels_propagated
Revises: 0016_mv_pch_deleted
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0017_mv_claim_labels_propagated"
down_revision: str | Sequence[str] | None = "0016_mv_pch_deleted"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# --- new definition (with descendant propagation) ---------------------------

_PROPAGATED_MV_CLAIM_LABELS = """
CREATE MATERIALIZED VIEW mv_claim_labels AS
WITH descendant_signal AS (
    SELECT
        r.claim_number,
        r.payer_id,
        bool_or(rc.claim_status_code = '4') AS desc_has_denied,
        bool_or(rc.claim_status_code IN ('1','2','3','19','20')) AS desc_has_paid
    FROM claims r
    JOIN remittance_claims rc ON rc.claim_id = r.id
    WHERE r.frequency_code = '7'
      AND r.deleted_at IS NULL
    GROUP BY r.claim_number, r.payer_id
)
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
        WHEN bool_or(ds.desc_has_denied) THEN 1
        WHEN bool_or(ds.desc_has_paid)   THEN 0
        ELSE NULL
    END AS denied
FROM claims c
LEFT JOIN remittance_claims rc ON rc.claim_id = c.id
LEFT JOIN descendant_signal ds
       ON ds.claim_number = c.claim_number
      AND ds.payer_id IS NOT DISTINCT FROM c.payer_id
WHERE c.deleted_at IS NULL
  AND (c.frequency_code IS NULL OR c.frequency_code = '1')
  AND c.service_from_date IS NOT NULL
GROUP BY
    c.id, c.service_variant, c.claim_subtype,
    c.payer_id, c.billing_provider_id, c.rendering_provider_id,
    c.patient_id, c.service_from_date
HAVING (
    bool_or(rc.claim_status_code = '4') OR
    bool_or(rc.claim_status_code IN ('1','2','3','19','20')) OR
    bool_or(ds.desc_has_denied) OR
    bool_or(ds.desc_has_paid)
)
"""

# Previous definition (CR-056, post-0015) — what downgrade restores.
_POST_CR056_MV_CLAIM_LABELS = """
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
WHERE c.deleted_at IS NULL
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

# Dependent MV bodies — IDENTICAL to 0010 / 0015. They reference
# mv_claim_labels by name, so they inherit the propagated content
# automatically once mv_claim_labels is recreated and refreshed.
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

    # Recreate mv_claim_labels with descendant-aware propagation.
    op.execute(_PROPAGATED_MV_CLAIM_LABELS)

    # Recreate the 9 dependents (byte-identical to 0010 / 0015).
    for _name, sql in _DEPENDENT_MVS_SQL:
        op.execute(sql)

    # Indexes.
    for sql in _INDEXES_SQL:
        op.execute(sql)


def downgrade() -> None:
    # Symmetric reversal: drop CASCADE, recreate with the post-CR-056 body
    # (no descendant propagation) — i.e., restore the state after 0015/0016.
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_claim_labels CASCADE")
    op.execute(_POST_CR056_MV_CLAIM_LABELS)
    for _name, sql in _DEPENDENT_MVS_SQL:
        op.execute(sql)
    for sql in _INDEXES_SQL:
        op.execute(sql)
