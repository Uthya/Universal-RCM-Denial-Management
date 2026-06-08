"""add_variant_extensions: Domain C — claim_certifications, claim_amounts,
claim_attachments, home_care_episodes, transport_certifications.

Revision ID: 0002_add_variant_extensions
Revises: 0001_init_core
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002_add_variant_extensions"
down_revision: str | Sequence[str] | None = "0001_init_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE certification_type AS ENUM "
        "('ambulance','homebound','dme','orthodontic','mammography','epsdt',"
        "'hospice_election','plan_of_care')"
    )

    op.create_table(
        "claim_certifications",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("claim_id", sa.BigInteger(), sa.ForeignKey("claims.id", ondelete="CASCADE"), nullable=False),
        sa.Column("certification_type", postgresql.ENUM(name="certification_type", create_type=False), nullable=False),
        sa.Column("raw_segment", sa.Text(), nullable=True),
        sa.Column("structured_data", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_claim_certs_claim_type", "claim_certifications", ["claim_id", "certification_type"])

    op.create_table(
        "claim_amounts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("claim_id", sa.BigInteger(), sa.ForeignKey("claims.id", ondelete="CASCADE"), nullable=False),
        sa.Column("amount_qualifier", sa.String(3), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_claim_amounts_claim_qual", "claim_amounts", ["claim_id", "amount_qualifier"])

    op.create_table(
        "claim_attachments",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("claim_id", sa.BigInteger(), sa.ForeignKey("claims.id", ondelete="CASCADE"), nullable=False),
        sa.Column("report_type_code", sa.String(2), nullable=False),
        sa.Column("transmission_code", sa.String(2), nullable=False),
        sa.Column("attachment_control_no", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_claim_attachments_claim", "claim_attachments", ["claim_id"])
    op.create_index(
        "ix_claim_attachments_control_no",
        "claim_attachments",
        ["attachment_control_no"],
        postgresql_where=sa.text("attachment_control_no IS NOT NULL"),
    )

    op.create_table(
        "home_care_episodes",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("claim_id", sa.BigInteger(), sa.ForeignKey("claims.id", ondelete="CASCADE"), nullable=False),
        sa.Column("episode_start_date", sa.Date(), nullable=False),
        sa.Column("episode_end_date", sa.Date(), nullable=True),
        sa.Column("hipps_code", sa.String(5), nullable=True),
        sa.Column("oasis_assessment_date", sa.Date(), nullable=True),
        sa.Column("visit_count", sa.Integer(), nullable=True),
        sa.Column("discipline_mix", postgresql.JSONB(), nullable=True),
        sa.Column("is_lupa", sa.Boolean(), nullable=True),
        sa.Column("homebound_certified", sa.Boolean(), nullable=True),
        sa.Column("plan_of_care_signed_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_home_care_episodes_claim", "home_care_episodes", ["claim_id"])
    op.create_index(
        "ix_home_care_episodes_hipps",
        "home_care_episodes",
        ["hipps_code"],
        postgresql_where=sa.text("hipps_code IS NOT NULL"),
    )
    op.create_index("ix_home_care_episodes_start", "home_care_episodes", ["episode_start_date"])

    op.create_table(
        "transport_certifications",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("claim_id", sa.BigInteger(), sa.ForeignKey("claims.id", ondelete="CASCADE"), nullable=False),
        sa.Column("transport_miles", sa.Numeric(8, 2), nullable=True),
        sa.Column("patient_weight_lbs", sa.Integer(), nullable=True),
        sa.Column("transport_reason_code", sa.String(3), nullable=True),
        sa.Column("round_trip", sa.Boolean(), nullable=True),
        sa.Column("emergent", sa.Boolean(), nullable=True),
        sa.Column("origin_address", postgresql.JSONB(), nullable=True),
        sa.Column("destination_address", postgresql.JSONB(), nullable=True),
        sa.Column("level_of_service", sa.String(5), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_transport_certs_claim", "transport_certifications", ["claim_id"])


def downgrade() -> None:
    op.drop_table("transport_certifications")
    op.drop_table("home_care_episodes")
    op.drop_table("claim_attachments")
    op.drop_table("claim_amounts")
    op.drop_table("claim_certifications")
    op.execute("DROP TYPE IF EXISTS certification_type")
