"""extend_code_masters: add enriched CARC/RARC reasoning fields.

The original code_masters table was a thin (code_type, code, description)
lookup. The full WPC CARC/RARC list ships with extra fields that the denial
reason engine wants to surface to the user:

  short_description           the spec short title
  denial_reason_plain         plain-English one-line denial reason
  patient_friendly_reason     non-technical variant (for member portals)
  recommended_action          terse next-step text
  category                    bucket: financial_adjustment / medical_necessity / ...
  action_category             accept_bill_patient / review_adjudication / ...
  severity                    low / medium / high
  is_billable_denial          true if the payer is rejecting payment
  is_patient_responsibility   true if it shifts cost to the patient
  requires_remark_code        true when CARC mandates a paired RARC
  start_date                  when the code became active
  last_modified_date          last spec revision
  stop_date                   when the code was deactivated

We use ALTER TABLE ADD COLUMN IF NOT EXISTS so re-running the migration is safe.

Revision ID: 0011_extend_code_masters
Revises: 0010_add_materialized_views
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "0011_extend_code_masters"
down_revision: str | Sequence[str] | None = "0010_add_materialized_views"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ADD_COLUMNS = [
    ("short_description",           "VARCHAR(500)"),
    ("denial_reason_plain",         "VARCHAR(1000)"),
    ("patient_friendly_reason",     "VARCHAR(1000)"),
    ("recommended_action",          "VARCHAR(1000)"),
    ("category",                    "VARCHAR(50)"),
    ("action_category",             "VARCHAR(50)"),
    ("severity",                    "VARCHAR(20)"),
    ("is_billable_denial",          "BOOLEAN"),
    ("is_patient_responsibility",   "BOOLEAN"),
    ("requires_remark_code",        "BOOLEAN"),
    ("start_date",                  "DATE"),
    ("last_modified_date",          "DATE"),
    ("stop_date",                   "DATE"),
]


def upgrade() -> None:
    for name, ddl in _ADD_COLUMNS:
        op.execute(f"ALTER TABLE code_masters ADD COLUMN IF NOT EXISTS {name} {ddl}")
    # Index frequently-filtered fields for the reason lookup
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_code_masters_severity "
        "ON code_masters (code_type, severity) WHERE severity IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_code_masters_active "
        "ON code_masters (code_type, code) WHERE stop_date IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_code_masters_active")
    op.execute("DROP INDEX IF EXISTS ix_code_masters_severity")
    for name, _ in _ADD_COLUMNS:
        op.execute(f"ALTER TABLE code_masters DROP COLUMN IF EXISTS {name}")
