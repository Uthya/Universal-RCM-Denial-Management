"""Domain C — variant extensions: certifications, amounts, attachments,
home_care episodes, transport certifications.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from rcm.core.database import Base
from rcm.core.enums import CertificationType
from rcm.models._mixins import CreatedAtMixin, TimestampMixin


class ClaimCertification(Base, TimestampMixin):
    __tablename__ = "claim_certifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    claim_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    certification_type: Mapped[CertificationType] = mapped_column(
        Enum(CertificationType, name="certification_type", create_type=False, native_enum=True),
        nullable=False,
    )
    raw_segment: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (Index("ix_claim_certs_claim_type", "claim_id", "certification_type"),)


class ClaimAmount(Base, CreatedAtMixin):
    __tablename__ = "claim_amounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    claim_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    amount_qualifier: Mapped[str] = mapped_column(String(3), nullable=False)  # F5/A8/...
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    __table_args__ = (Index("ix_claim_amounts_claim_qual", "claim_id", "amount_qualifier"),)


class ClaimAttachment(Base, CreatedAtMixin):
    __tablename__ = "claim_attachments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    claim_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    report_type_code: Mapped[str] = mapped_column(String(2), nullable=False)
    transmission_code: Mapped[str] = mapped_column(String(2), nullable=False)
    attachment_control_no: Mapped[str | None] = mapped_column(String(80), nullable=True)

    __table_args__ = (
        Index("ix_claim_attachments_claim", "claim_id"),
        Index(
            "ix_claim_attachments_control_no",
            "attachment_control_no",
            postgresql_where="attachment_control_no IS NOT NULL",
        ),
    )


class HomeCareEpisode(Base, TimestampMixin):
    __tablename__ = "home_care_episodes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    claim_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    episode_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    episode_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    hipps_code: Mapped[str | None] = mapped_column(String(5), nullable=True)
    oasis_assessment_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    visit_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # {"PT": 3, "OT": 2, "SN": 8, "ST": 1, "HHA": 5}
    discipline_mix: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_lupa: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    homebound_certified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    plan_of_care_signed_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    __table_args__ = (
        Index("ix_home_care_episodes_claim", "claim_id"),
        Index(
            "ix_home_care_episodes_hipps",
            "hipps_code",
            postgresql_where="hipps_code IS NOT NULL",
        ),
        Index("ix_home_care_episodes_start", "episode_start_date"),
    )


class TransportCertification(Base, TimestampMixin):
    __tablename__ = "transport_certifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    claim_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    transport_miles: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    patient_weight_lbs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transport_reason_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    round_trip: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    emergent: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    origin_address: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    destination_address: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    level_of_service: Mapped[str | None] = mapped_column(String(5), nullable=True)  # ALS/BLS/SCT

    __table_args__ = (Index("ix_transport_certs_claim", "claim_id"),)
