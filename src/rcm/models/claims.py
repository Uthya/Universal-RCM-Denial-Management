"""Domain B — claims core: claims, lines, diagnoses, patients, providers, subscribers.

Lesson C3: ``service_from_date`` is NULLABLE. Never write ``date.today()`` as a
placeholder; let the validator drop the claim if the date is genuinely missing.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from rcm.core.database import Base
from rcm.core.enums import ClaimStatus, ProviderType
from rcm.models._mixins import SoftDeleteMixin, TimestampMixin


class Patient(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    member_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(1), nullable=True)

    __table_args__ = (
        Index(
            "ix_patients_name_dob",
            "last_name",
            "first_name",
            "date_of_birth",
            postgresql_where="deleted_at IS NULL",
        ),
    )


class Provider(Base, TimestampMixin):
    __tablename__ = "providers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    npi: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    provider_type: Mapped[ProviderType | None] = mapped_column(
        Enum(ProviderType, name="provider_type", create_type=False, native_enum=True),
        nullable=True,
    )
    organization_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    taxonomy_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    state: Mapped[str | None] = mapped_column(String(2), nullable=True)

    __table_args__ = (
        Index("ix_providers_taxonomy", "taxonomy_code"),
        Index("ix_providers_state", "state"),
    )


class Subscriber(Base, TimestampMixin):
    __tablename__ = "subscribers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    member_id: Mapped[str] = mapped_column(String(80), nullable=False)
    patient_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("patients.id", ondelete="SET NULL"), nullable=True
    )
    payer_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("payers.id", ondelete="SET NULL"), nullable=True
    )
    relationship_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    group_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    policy_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    coordination_of_benefits: Mapped[str | None] = mapped_column(String(1), nullable=True)

    __table_args__ = (
        Index("ix_subscribers_member_id", "member_id"),
        Index("ix_subscribers_patient_payer", "patient_id", "payer_id"),
    )


class Claim(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    edi_file_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("edi_files.id", ondelete="CASCADE"), nullable=False
    )

    # Variant routing — set at parse time, used everywhere downstream
    service_variant: Mapped[str] = mapped_column(String(10), nullable=False)  # 837P/I/D
    claim_subtype: Mapped[str] = mapped_column(String(20), nullable=False)

    claim_number: Mapped[str] = mapped_column(String(50), nullable=False)

    payer_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("payers.id", ondelete="SET NULL"), nullable=True
    )
    patient_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("patients.id", ondelete="SET NULL"), nullable=True
    )
    subscriber_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("subscribers.id", ondelete="SET NULL"), nullable=True
    )
    billing_provider_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("providers.id", ondelete="SET NULL"), nullable=True
    )
    rendering_provider_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("providers.id", ondelete="SET NULL"), nullable=True
    )
    referring_provider_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("providers.id", ondelete="SET NULL"), nullable=True
    )

    total_charge_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    facility_type_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    frequency_code: Mapped[str | None] = mapped_column(String(5), nullable=True)

    claim_status: Mapped[ClaimStatus] = mapped_column(
        Enum(ClaimStatus, name="claim_status", create_type=False, native_enum=True),
        nullable=False,
    )

    # Lesson C3: NULLABLE — do not substitute date.today()
    service_from_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    service_to_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Materialized at parse time so training queries don't need to derive it
    submission_date: Mapped[date] = mapped_column(Date, nullable=False)

    authorization_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    referral_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    previous_payer_claim_control_no: Mapped[str | None] = mapped_column(String(50), nullable=True)

    variant_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    raw_claim_segment: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("edi_file_id", "claim_number", name="uq_claims_edi_file_claim_number"),
        Index("ix_claims_claim_number", "claim_number"),
        Index(
            "ix_claims_payer_variant_svc_date",
            "payer_id",
            "service_variant",
            "service_from_date",
            postgresql_where="service_from_date IS NOT NULL",
        ),
        Index("ix_claims_variant_subtype", "service_variant", "claim_subtype"),
        Index("ix_claims_billing_provider_svc", "billing_provider_id", "service_from_date"),
        Index("ix_claims_rendering_provider_svc", "rendering_provider_id", "service_from_date"),
        Index("ix_claims_status_svc_date", "claim_status", "service_from_date"),
        Index(
            "ix_claims_svc_from_date",
            "service_from_date",
            postgresql_where="service_from_date IS NOT NULL",
        ),
        Index(
            "ix_claims_patient_svc_date",
            "patient_id",
            "service_from_date",
            postgresql_where="patient_id IS NOT NULL",
        ),
        Index("ix_claims_variant_data_gin", "variant_data", postgresql_using="gin"),
    )


class ClaimLine(Base, TimestampMixin):
    __tablename__ = "claim_lines"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    claim_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)

    procedure_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    procedure_code_qualifier: Mapped[str | None] = mapped_column(String(5), nullable=True)

    # All four modifier slots are preserved — v1 dropped modifier2-4
    modifier1: Mapped[str | None] = mapped_column(String(5), nullable=True)
    modifier2: Mapped[str | None] = mapped_column(String(5), nullable=True)
    modifier3: Mapped[str | None] = mapped_column(String(5), nullable=True)
    modifier4: Mapped[str | None] = mapped_column(String(5), nullable=True)

    billed_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    units: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    units_basis: Mapped[str | None] = mapped_column(String(5), nullable=True)

    place_of_service: Mapped[str | None] = mapped_column(String(10), nullable=True)
    service_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    diagnosis_pointers: Mapped[list[int] | None] = mapped_column(ARRAY(Integer), nullable=True)

    # 837I / institutional
    revenue_code: Mapped[str | None] = mapped_column(String(4), nullable=True)
    hipps_code: Mapped[str | None] = mapped_column(String(5), nullable=True)

    # 837D / dental
    tooth_number: Mapped[str | None] = mapped_column(String(2), nullable=True)
    tooth_surfaces: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # Oncology / pharmacy
    ndc_drug_code: Mapped[str | None] = mapped_column(String(20), nullable=True)

    line_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    raw_sv_segment: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("claim_id", "line_number", name="uq_claim_lines_claim_line"),
        Index("ix_claim_lines_proc_pos", "procedure_code", "place_of_service"),
        Index("ix_claim_lines_revenue_code", "revenue_code", postgresql_where="revenue_code IS NOT NULL"),
        Index("ix_claim_lines_hipps", "hipps_code", postgresql_where="hipps_code IS NOT NULL"),
        Index("ix_claim_lines_tooth", "tooth_number", postgresql_where="tooth_number IS NOT NULL"),
        Index("ix_claim_lines_procedure", "procedure_code"),
    )


class Diagnosis(Base, TimestampMixin):
    __tablename__ = "diagnoses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    claim_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    diagnosis_code: Mapped[str] = mapped_column(String(20), nullable=False)
    diagnosis_type: Mapped[str] = mapped_column(String(10), nullable=False)  # ABK/ABF/BJ/...
    diagnosis_qualifier: Mapped[str | None] = mapped_column(String(5), nullable=True)
    present_on_admission: Mapped[str | None] = mapped_column(String(1), nullable=True)

    __table_args__ = (
        UniqueConstraint("claim_id", "sequence_number", name="uq_diagnoses_claim_seq"),
        Index("ix_diagnoses_code", "diagnosis_code"),
        Index("ix_diagnoses_claim_type", "claim_id", "diagnosis_type"),
    )
