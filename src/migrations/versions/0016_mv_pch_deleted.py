"""mv_patient_claim_history_deleted (CR-057): exclude soft-deleted claims
from the patient-history corpus that FeatureBuilder Category G reads from.

Same defect class as CR-056 (mv_claim_labels), different MV. Read-only
investigation surfaced 326 soft-deleted claims — all traced to the same
test families (QX500, P10, FV6, TR15) whose source 837 files are also
soft-deleted. 250 patients touched; 200 fully-deleted (no impact); 50
MIXED active+deleted patients (every Cat-G feature derived from their
history is currently wrong because patient_seq window numbering shifts
when deleted claims occupy seq positions).

This MV has NO dependents (verified via pg_depend), so no CASCADE needed.

Out of scope (per the approved AIR + user instruction):
  - mv_lifecycle_outcomes (will be handled by R0)
  - any other MV

Revision ID: 0016_mv_pch_deleted
Revises: 0015_mv_claim_labels_deleted
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0016_mv_pch_deleted"
down_revision: str | Sequence[str] | None = "0015_mv_claim_labels_deleted"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CORRECTED_MV = """
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
WHERE c.deleted_at IS NULL                          -- CR-057 correction
  AND c.patient_id IS NOT NULL
  AND c.service_from_date IS NOT NULL
"""

_ORIGINAL_MV = """
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

_INDEXES = [
    "CREATE UNIQUE INDEX mv_patient_claim_history_pkey "
    "ON mv_patient_claim_history (claim_id)",
    "CREATE INDEX mv_patient_claim_history_patient_date "
    "ON mv_patient_claim_history (patient_id, service_from_date)",
    "CREATE INDEX mv_patient_claim_history_patient_payer_date "
    "ON mv_patient_claim_history (patient_id, payer_id, service_from_date)",
]


def upgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_patient_claim_history")
    op.execute(_CORRECTED_MV)
    for sql in _INDEXES:
        op.execute(sql)


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_patient_claim_history")
    op.execute(_ORIGINAL_MV)
    for sql in _INDEXES:
        op.execute(sql)
