"""init_core: domains A (ingestion), B (claims), D (remittance), E (lifecycle), F (reference).

Partitioned tables (raw_segments, parse_events) are created PARTITIONED
from the start with a default partition + current/next-month partitions.

Revision ID: 0001_init_core
Revises:
Create Date: 2026-06-05
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001_init_core"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ----------------------------------------------------------------------------
# Helpers for monthly partition creation
# ----------------------------------------------------------------------------
def _month_bounds(anchor: datetime) -> tuple[str, str, str]:
    """Return (suffix, start_ts, end_ts) for the month containing anchor."""
    start = anchor.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    suffix = f"{start:%Y_%m}"
    return suffix, start.isoformat(), end.isoformat()


def _ensure_monthly_partitions(parent_table: str, partition_col: str, months_ahead: int = 3) -> None:
    """Create a default partition + current + N future-month partitions."""
    # Default catch-all
    op.execute(
        f"CREATE TABLE IF NOT EXISTS {parent_table}_default PARTITION OF {parent_table} DEFAULT"
    )
    now = datetime.now(timezone.utc)
    for m in range(months_ahead + 1):
        anchor = now + timedelta(days=31 * m)
        suffix, start_ts, end_ts = _month_bounds(anchor)
        op.execute(
            f"CREATE TABLE IF NOT EXISTS {parent_table}_p{suffix} "
            f"PARTITION OF {parent_table} "
            f"FOR VALUES FROM ('{start_ts}') TO ('{end_ts}')"
        )


# ----------------------------------------------------------------------------
# Upgrade
# ----------------------------------------------------------------------------
def upgrade() -> None:
    # ====================== ENUM TYPES ======================================
    op.execute("CREATE TYPE file_type AS ENUM ('edi_837','edi_835','edi_277','edi_999')")
    op.execute("CREATE TYPE parse_status AS ENUM ('pending','parsing','parsed','failed','partial')")
    op.execute(
        "CREATE TYPE handler_status AS ENUM "
        "('handled','skipped_unhandled','parse_error','validator_dropped')"
    )
    op.execute(
        "CREATE TYPE parse_event_type AS ENUM "
        "('segment_handled','segment_skipped','validator_warning','validator_error',"
        "'claim_dropped','placeholder_created','parse_error')"
    )
    op.execute(
        "CREATE TYPE claim_status AS ENUM "
        "('submitted','paid','denied','partially_paid','void','pending')"
    )
    op.execute(
        "CREATE TYPE provider_type AS ENUM "
        "('billing','rendering','referring','supervising','ordering','pay_to','facility')"
    )
    op.execute(
        "CREATE TYPE lifecycle_relationship AS ENUM "
        "('replacement','resubmission','void','correction','appeal')"
    )
    op.execute(
        "CREATE TYPE appeal_level AS ENUM ('internal','external','alj','dab')"
    )
    op.execute(
        "CREATE TYPE appeal_status AS ENUM "
        "('draft','submitted','accepted','denied','withdrawn')"
    )
    op.execute("CREATE TYPE code_system AS ENUM ('cpt','hcpcs','cdt','hipps','ndc')")
    op.execute("CREATE TYPE dx_code_system AS ENUM ('icd10cm','icd10pcs','icd9cm')")
    op.execute(
        "CREATE TYPE policy_type AS ENUM "
        "('coverage','prior_auth','frequency_limit','modifier_required','age_limit',"
        "'appeal_process','timely_filing','referral_required')"
    )
    op.execute(
        "CREATE TYPE cms_doc_type AS ENUM "
        "('lcd','ncd','manual_chapter','ncci_edit','mue','claims_processing')"
    )

    # ====================== REFERENCE TABLES (no FKs to claims) =============
    op.create_table(
        "code_masters",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("code_type", sa.String(20), nullable=False),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("description", sa.String(2000), nullable=True),
        sa.Column("deactivated_at", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("code_type", "code", name="uq_code_masters_type_code"),
    )

    op.create_table(
        "payers",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("canonical_name", sa.String(255), nullable=False, unique=True),
        sa.Column("sender_id", sa.String(50), nullable=True),
        sa.Column("receiver_id", sa.String(50), nullable=True),
        sa.Column("payer_taxonomy", sa.String(50), nullable=True),
        sa.Column("aliases", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("contact_info", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_payers_aliases_gin", "payers", ["aliases"], postgresql_using="gin")

    op.create_table(
        "procedure_codes",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("code_system", postgresql.ENUM(name="code_system", create_type=False), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("deprecated_date", sa.Date(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("code", "code_system", name="uq_procedure_codes_code_system"),
    )
    op.create_index("ix_procedure_codes_category", "procedure_codes", ["category"])

    op.create_table(
        "diagnosis_codes",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(20), nullable=False, unique=True),
        sa.Column("code_system", postgresql.ENUM(name="dx_code_system", create_type=False), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("chapter", sa.String(100), nullable=True),
        sa.Column("category", sa.String(20), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "payer_policies",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("payer_id", sa.BigInteger(), sa.ForeignKey("payers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("policy_type", postgresql.ENUM(name="policy_type", create_type=False), nullable=False),
        sa.Column("applies_to_codes", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("service_variant", sa.String(10), nullable=True),
        sa.Column("claim_subtype", sa.String(20), nullable=True),
        sa.Column("policy_text", sa.Text(), nullable=True),
        sa.Column("structured_rule", postgresql.JSONB(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("source", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_payer_policies_payer_type", "payer_policies", ["payer_id", "policy_type"])
    op.create_index("ix_payer_policies_codes_gin", "payer_policies", ["applies_to_codes"], postgresql_using="gin")
    op.create_index("ix_payer_policies_rule_gin", "payer_policies", ["structured_rule"], postgresql_using="gin")

    op.create_table(
        "cms_knowledge",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("document_type", postgresql.ENUM(name="cms_doc_type", create_type=False), nullable=False),
        sa.Column("document_id", sa.String(50), nullable=True),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("state", sa.String(2), nullable=True),
        sa.Column("contractor", sa.String(20), nullable=True),
        sa.Column("applies_to_codes", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("revision_date", sa.Date(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_cms_knowledge_type_state", "cms_knowledge", ["document_type", "state"])
    op.create_index("ix_cms_knowledge_codes_gin", "cms_knowledge", ["applies_to_codes"], postgresql_using="gin")

    # ====================== PEOPLE / PROVIDERS ==============================
    op.create_table(
        "patients",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("member_id", sa.String(80), nullable=False, unique=True),
        sa.Column("first_name", sa.String(100), nullable=True),
        sa.Column("last_name", sa.String(100), nullable=True),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("gender", sa.String(1), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_patients_name_dob",
        "patients",
        ["last_name", "first_name", "date_of_birth"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "providers",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("npi", sa.String(20), nullable=False, unique=True),
        sa.Column("provider_type", postgresql.ENUM(name="provider_type", create_type=False), nullable=True),
        sa.Column("organization_name", sa.String(255), nullable=True),
        sa.Column("last_name", sa.String(100), nullable=True),
        sa.Column("first_name", sa.String(100), nullable=True),
        sa.Column("taxonomy_code", sa.String(20), nullable=True),
        sa.Column("state", sa.String(2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_providers_taxonomy", "providers", ["taxonomy_code"])
    op.create_index("ix_providers_state", "providers", ["state"])

    op.create_table(
        "subscribers",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("member_id", sa.String(80), nullable=False),
        sa.Column("patient_id", sa.BigInteger(), sa.ForeignKey("patients.id", ondelete="SET NULL"), nullable=True),
        sa.Column("payer_id", sa.BigInteger(), sa.ForeignKey("payers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("relationship_code", sa.String(2), nullable=True),
        sa.Column("group_number", sa.String(50), nullable=True),
        sa.Column("policy_number", sa.String(50), nullable=True),
        sa.Column("coordination_of_benefits", sa.String(1), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_subscribers_member_id", "subscribers", ["member_id"])
    op.create_index("ix_subscribers_patient_payer", "subscribers", ["patient_id", "payer_id"])

    # ====================== INGESTION =======================================
    # edi_files.uploaded_by_user_id FK to users is added in 0004_add_operations
    op.create_table(
        "edi_files",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("file_type", postgresql.ENUM(name="file_type", create_type=False), nullable=False),
        sa.Column("implementation_guide", sa.String(20), nullable=True),
        sa.Column("sender_id", sa.String(50), nullable=True),
        sa.Column("receiver_id", sa.String(50), nullable=True),
        sa.Column("interchange_control_no", sa.String(20), nullable=True),
        sa.Column("functional_group_control_no", sa.String(20), nullable=True),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("parser_version", sa.String(20), nullable=False),
        sa.Column("service_variant_detected", sa.String(10), nullable=True),
        sa.Column("claim_subtype_detected", sa.String(20), nullable=True),
        sa.Column(
            "parse_status",
            postgresql.ENUM(name="parse_status", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("parse_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("parse_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("parse_summary", postgresql.JSONB(), nullable=True),
        sa.Column("uploaded_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_edi_files_content_hash_alive",
        "edi_files",
        ["content_hash"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_edi_files_type_created", "edi_files", ["file_type", "created_at"])
    op.create_index(
        "ix_edi_files_parse_status_pending",
        "edi_files",
        ["parse_status", "created_at"],
        postgresql_where=sa.text("parse_status IN ('pending','parsing','failed')"),
    )
    op.create_index("ix_edi_files_uploaded_by", "edi_files", ["uploaded_by_user_id", "created_at"])

    # ====================== CLAIMS CORE =====================================
    op.create_table(
        "claims",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("edi_file_id", sa.BigInteger(), sa.ForeignKey("edi_files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("service_variant", sa.String(10), nullable=False),
        sa.Column("claim_subtype", sa.String(20), nullable=False),
        sa.Column("claim_number", sa.String(50), nullable=False),
        sa.Column("payer_id", sa.BigInteger(), sa.ForeignKey("payers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("patient_id", sa.BigInteger(), sa.ForeignKey("patients.id", ondelete="SET NULL"), nullable=True),
        sa.Column("subscriber_id", sa.BigInteger(), sa.ForeignKey("subscribers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("billing_provider_id", sa.BigInteger(), sa.ForeignKey("providers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("rendering_provider_id", sa.BigInteger(), sa.ForeignKey("providers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("referring_provider_id", sa.BigInteger(), sa.ForeignKey("providers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("total_charge_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("facility_type_code", sa.String(10), nullable=True),
        sa.Column("frequency_code", sa.String(5), nullable=True),
        sa.Column("claim_status", postgresql.ENUM(name="claim_status", create_type=False), nullable=False),
        sa.Column("service_from_date", sa.Date(), nullable=True),
        sa.Column("service_to_date", sa.Date(), nullable=True),
        sa.Column("submission_date", sa.Date(), nullable=False),
        sa.Column("authorization_number", sa.String(50), nullable=True),
        sa.Column("referral_number", sa.String(50), nullable=True),
        sa.Column("previous_payer_claim_control_no", sa.String(50), nullable=True),
        sa.Column("variant_data", postgresql.JSONB(), nullable=True),
        sa.Column("raw_claim_segment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("edi_file_id", "claim_number", name="uq_claims_edi_file_claim_number"),
    )
    op.create_index("ix_claims_claim_number", "claims", ["claim_number"])
    op.create_index(
        "ix_claims_payer_variant_svc_date",
        "claims",
        ["payer_id", "service_variant", "service_from_date"],
        postgresql_where=sa.text("service_from_date IS NOT NULL"),
    )
    op.create_index("ix_claims_variant_subtype", "claims", ["service_variant", "claim_subtype"])
    op.create_index("ix_claims_billing_provider_svc", "claims", ["billing_provider_id", "service_from_date"])
    op.create_index("ix_claims_rendering_provider_svc", "claims", ["rendering_provider_id", "service_from_date"])
    op.create_index("ix_claims_status_svc_date", "claims", ["claim_status", "service_from_date"])
    op.create_index(
        "ix_claims_svc_from_date",
        "claims",
        ["service_from_date"],
        postgresql_where=sa.text("service_from_date IS NOT NULL"),
    )
    op.create_index(
        "ix_claims_patient_svc_date",
        "claims",
        ["patient_id", "service_from_date"],
        postgresql_where=sa.text("patient_id IS NOT NULL"),
    )
    op.create_index("ix_claims_variant_data_gin", "claims", ["variant_data"], postgresql_using="gin")

    op.create_table(
        "claim_lines",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("claim_id", sa.BigInteger(), sa.ForeignKey("claims.id", ondelete="CASCADE"), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("procedure_code", sa.String(20), nullable=True),
        sa.Column("procedure_code_qualifier", sa.String(5), nullable=True),
        sa.Column("modifier1", sa.String(5), nullable=True),
        sa.Column("modifier2", sa.String(5), nullable=True),
        sa.Column("modifier3", sa.String(5), nullable=True),
        sa.Column("modifier4", sa.String(5), nullable=True),
        sa.Column("billed_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("units", sa.Numeric(12, 4), nullable=True),
        sa.Column("units_basis", sa.String(5), nullable=True),
        sa.Column("place_of_service", sa.String(10), nullable=True),
        sa.Column("service_date", sa.Date(), nullable=True),
        sa.Column("diagnosis_pointers", postgresql.ARRAY(sa.Integer()), nullable=True),
        sa.Column("revenue_code", sa.String(4), nullable=True),
        sa.Column("hipps_code", sa.String(5), nullable=True),
        sa.Column("tooth_number", sa.String(2), nullable=True),
        sa.Column("tooth_surfaces", sa.String(10), nullable=True),
        sa.Column("ndc_drug_code", sa.String(20), nullable=True),
        sa.Column("line_data", postgresql.JSONB(), nullable=True),
        sa.Column("raw_sv_segment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("claim_id", "line_number", name="uq_claim_lines_claim_line"),
    )
    op.create_index("ix_claim_lines_proc_pos", "claim_lines", ["procedure_code", "place_of_service"])
    op.create_index("ix_claim_lines_revenue_code", "claim_lines", ["revenue_code"], postgresql_where=sa.text("revenue_code IS NOT NULL"))
    op.create_index("ix_claim_lines_hipps", "claim_lines", ["hipps_code"], postgresql_where=sa.text("hipps_code IS NOT NULL"))
    op.create_index("ix_claim_lines_tooth", "claim_lines", ["tooth_number"], postgresql_where=sa.text("tooth_number IS NOT NULL"))
    op.create_index("ix_claim_lines_procedure", "claim_lines", ["procedure_code"])

    op.create_table(
        "diagnoses",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("claim_id", sa.BigInteger(), sa.ForeignKey("claims.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("diagnosis_code", sa.String(20), nullable=False),
        sa.Column("diagnosis_type", sa.String(10), nullable=False),
        sa.Column("diagnosis_qualifier", sa.String(5), nullable=True),
        sa.Column("present_on_admission", sa.String(1), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("claim_id", "sequence_number", name="uq_diagnoses_claim_seq"),
    )
    op.create_index("ix_diagnoses_code", "diagnoses", ["diagnosis_code"])
    op.create_index("ix_diagnoses_claim_type", "diagnoses", ["claim_id", "diagnosis_type"])

    # ====================== REMITTANCE ======================================
    op.create_table(
        "remittance_claims",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("claim_id", sa.BigInteger(), sa.ForeignKey("claims.id", ondelete="CASCADE"), nullable=False),
        sa.Column("edi_file_id", sa.BigInteger(), sa.ForeignKey("edi_files.id", ondelete="SET NULL"), nullable=True),
        sa.Column("claim_status_code", sa.String(10), nullable=False),
        sa.Column("billed_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("paid_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("patient_responsibility_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("payer_claim_control_number", sa.String(50), nullable=True),
        sa.Column("remittance_date", sa.Date(), nullable=True),
        sa.Column("payer_paid_date", sa.Date(), nullable=True),
        sa.Column("raw_clp_segment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_remittance_claims_claim", "remittance_claims", ["claim_id"])
    op.create_index("ix_remittance_claims_status", "remittance_claims", ["claim_status_code"])
    op.create_index("ix_remittance_claims_edi_file", "remittance_claims", ["edi_file_id"])
    op.create_index(
        "ix_remittance_claims_remit_date",
        "remittance_claims",
        ["remittance_date"],
        postgresql_where=sa.text("remittance_date IS NOT NULL"),
    )

    op.create_table(
        "adjustments",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("remittance_claim_id", sa.BigInteger(), sa.ForeignKey("remittance_claims.id", ondelete="CASCADE"), nullable=False),
        sa.Column("service_line_id", sa.BigInteger(), sa.ForeignKey("claim_lines.id", ondelete="SET NULL"), nullable=True),
        sa.Column("adjustment_group_code", sa.String(5), nullable=False),
        sa.Column("adjustment_reason_code", sa.String(10), nullable=False),
        sa.Column("adjustment_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 5), nullable=True),
        sa.Column("raw_cas_segment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_adjustments_remit", "adjustments", ["remittance_claim_id"])
    op.create_index("ix_adjustments_carc_group", "adjustments", ["adjustment_reason_code", "adjustment_group_code"])
    op.create_index(
        "ix_adjustments_service_line",
        "adjustments",
        ["service_line_id"],
        postgresql_where=sa.text("service_line_id IS NOT NULL"),
    )

    op.create_table(
        "remark_codes",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("remittance_claim_id", sa.BigInteger(), sa.ForeignKey("remittance_claims.id", ondelete="CASCADE"), nullable=False),
        sa.Column("service_line_id", sa.BigInteger(), sa.ForeignKey("claim_lines.id", ondelete="SET NULL"), nullable=True),
        sa.Column("remark_code", sa.String(10), nullable=False),
        sa.Column("raw_lq_segment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_remark_codes_remit", "remark_codes", ["remittance_claim_id"])
    op.create_index("ix_remark_codes_code", "remark_codes", ["remark_code"])

    # ====================== LIFECYCLE =======================================
    op.create_table(
        "claim_lifecycles",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("original_claim_id", sa.BigInteger(), sa.ForeignKey("claims.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_claim_id", sa.BigInteger(), sa.ForeignKey("claims.id", ondelete="CASCADE"), nullable=False),
        sa.Column("child_claim_id", sa.BigInteger(), sa.ForeignKey("claims.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relationship_type", postgresql.ENUM(name="lifecycle_relationship", create_type=False), nullable=False),
        sa.Column("iteration_number", sa.Integer(), nullable=False),
        sa.Column("days_to_resolution", sa.Integer(), nullable=True),
        sa.Column("diff_summary", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_claim_lifecycles_original", "claim_lifecycles", ["original_claim_id"])
    op.create_index("ix_claim_lifecycles_child", "claim_lifecycles", ["child_claim_id"])
    op.create_index("ix_claim_lifecycles_parent_rel", "claim_lifecycles", ["parent_claim_id", "relationship_type"])

    # appeals.created_by_user_id FK to users added in 0004_add_operations
    op.create_table(
        "appeals",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("claim_id", sa.BigInteger(), sa.ForeignKey("claims.id", ondelete="CASCADE"), nullable=False),
        sa.Column("appeal_level", postgresql.ENUM(name="appeal_level", create_type=False), nullable=True),
        sa.Column("status", postgresql.ENUM(name="appeal_status", create_type=False), nullable=False),
        sa.Column("submitted_date", sa.Date(), nullable=True),
        sa.Column("decision_date", sa.Date(), nullable=True),
        sa.Column("outcome", sa.String(50), nullable=True),
        sa.Column("generated_letter", sa.Text(), nullable=True),
        sa.Column("human_edits", sa.Text(), nullable=True),
        sa.Column("final_letter", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_appeals_claim_status", "appeals", ["claim_id", "status"])

    # ====================== PARTITIONED INGESTION TABLES ====================
    # raw_segments (partitioned monthly by created_at)
    op.execute(
        """
        CREATE TABLE raw_segments (
            id                  BIGSERIAL,
            created_at          TIMESTAMPTZ NOT NULL,
            edi_file_id         BIGINT NOT NULL REFERENCES edi_files(id) ON DELETE CASCADE,
            claim_id            BIGINT REFERENCES claims(id) ON DELETE SET NULL,
            remittance_claim_id BIGINT REFERENCES remittance_claims(id) ON DELETE SET NULL,
            segment_name        VARCHAR(10) NOT NULL,
            segment_position    INTEGER NOT NULL,
            raw_segment_text    TEXT NOT NULL,
            parse_error         TEXT,
            handler_status      handler_status NOT NULL,
            CONSTRAINT pk_raw_segments PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
        """
    )
    op.create_index("ix_raw_segments_edi_file_pos", "raw_segments", ["edi_file_id", "segment_position"])
    op.create_index(
        "ix_raw_segments_claim_id",
        "raw_segments",
        ["claim_id"],
        postgresql_where=sa.text("claim_id IS NOT NULL"),
    )
    op.create_index(
        "ix_raw_segments_remittance_claim_id",
        "raw_segments",
        ["remittance_claim_id"],
        postgresql_where=sa.text("remittance_claim_id IS NOT NULL"),
    )
    op.create_index(
        "ix_raw_segments_unhandled",
        "raw_segments",
        ["handler_status", "segment_name"],
        postgresql_where=sa.text("handler_status <> 'handled'"),
    )
    _ensure_monthly_partitions("raw_segments", "created_at")

    # parse_events
    op.execute(
        """
        CREATE TABLE parse_events (
            id                  BIGSERIAL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            edi_file_id         BIGINT NOT NULL REFERENCES edi_files(id) ON DELETE CASCADE,
            event_type          parse_event_type NOT NULL,
            segment_name        VARCHAR(10),
            segment_position    INTEGER,
            claim_number        VARCHAR(50),
            details             JSONB,
            CONSTRAINT pk_parse_events PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
        """
    )
    op.create_index("ix_parse_events_edi_file_type", "parse_events", ["edi_file_id", "event_type"])
    op.create_index(
        "ix_parse_events_type_created",
        "parse_events",
        ["event_type", "created_at"],
        postgresql_where=sa.text("event_type IN ('segment_skipped','parse_error')"),
    )
    op.create_index("ix_parse_events_details_gin", "parse_events", ["details"], postgresql_using="gin")
    _ensure_monthly_partitions("parse_events", "created_at")


def downgrade() -> None:
    # Drop in reverse dependency order
    op.execute("DROP TABLE IF EXISTS parse_events CASCADE")
    op.execute("DROP TABLE IF EXISTS raw_segments CASCADE")
    op.drop_table("appeals")
    op.drop_table("claim_lifecycles")
    op.drop_table("remark_codes")
    op.drop_table("adjustments")
    op.drop_table("remittance_claims")
    op.drop_table("diagnoses")
    op.drop_table("claim_lines")
    op.drop_table("claims")
    op.drop_table("edi_files")
    op.drop_table("subscribers")
    op.drop_table("providers")
    op.drop_table("patients")
    op.drop_table("cms_knowledge")
    op.drop_table("payer_policies")
    op.drop_table("diagnosis_codes")
    op.drop_table("procedure_codes")
    op.drop_table("payers")
    op.drop_table("code_masters")

    for enum in (
        "cms_doc_type",
        "policy_type",
        "dx_code_system",
        "code_system",
        "appeal_status",
        "appeal_level",
        "lifecycle_relationship",
        "provider_type",
        "claim_status",
        "parse_event_type",
        "handler_status",
        "parse_status",
        "file_type",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum}")
