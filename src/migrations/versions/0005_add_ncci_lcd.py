"""add_ncci_lcd: two reference tables needed by the revised denial-driven
feature engineering — ncci_edits (procedure-to-procedure + MUE) and
cms_lcd_coverage (CPT × Dx × state).

Revision ID: 0005_add_ncci_lcd
Revises: 0004_add_operations
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0005_add_ncci_lcd"
down_revision: str | Sequence[str] | None = "0004_add_operations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE TYPE ncci_edit_type AS ENUM ('ptp','mue')")

    op.create_table(
        "ncci_edits",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("column1_code", sa.String(20), nullable=False),
        sa.Column("column2_code", sa.String(20), nullable=False),
        sa.Column("edit_type", postgresql.ENUM(name="ncci_edit_type", create_type=False), nullable=False),
        sa.Column("modifier_indicator", sa.Integer(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("deletion_date", sa.Date(), nullable=True),
        sa.Column("rationale", sa.String(500), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.UniqueConstraint(
            "column1_code",
            "column2_code",
            "edit_type",
            "effective_date",
            name="uq_ncci_edits_pair_type_eff",
        ),
    )
    op.create_index(
        "ix_ncci_edits_lookup",
        "ncci_edits",
        ["column1_code", "column2_code"],
        postgresql_where=sa.text("deletion_date IS NULL"),
    )

    op.create_table(
        "cms_lcd_coverage",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("lcd_id", sa.String(20), nullable=False),
        sa.Column("state", sa.String(2), nullable=True),
        sa.Column("contractor", sa.String(20), nullable=True),
        sa.Column("cpt_codes", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("covered_dx_codes", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("excluded_dx_codes", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("revision_date", sa.Date(), nullable=True),
        sa.Column("document_url", sa.Text(), nullable=True),
    )
    op.create_index("ix_cms_lcd_cpt_gin", "cms_lcd_coverage", ["cpt_codes"], postgresql_using="gin")
    op.create_index("ix_cms_lcd_state", "cms_lcd_coverage", ["state"])


def downgrade() -> None:
    op.drop_table("cms_lcd_coverage")
    op.drop_table("ncci_edits")
    op.execute("DROP TYPE IF EXISTS ncci_edit_type")
