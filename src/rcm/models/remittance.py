"""Domain D — remittance: remittance_claims, adjustments, remark_codes.

Lesson P1: ``remittance_date`` is NULLABLE. Never substitute date.today();
emit a Tier-2 ERROR when the source 835 lacks DTM*050 and DTM*405.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from rcm.core.database import Base
from rcm.models._mixins import TimestampMixin


class RemittanceClaim(Base, TimestampMixin):
    __tablename__ = "remittance_claims"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    claim_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    edi_file_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("edi_files.id", ondelete="SET NULL"), nullable=True
    )

    claim_status_code: Mapped[str] = mapped_column(String(10), nullable=False)  # CLP02
    billed_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    paid_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    patient_responsibility_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    payer_claim_control_number: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Lesson P1 — nullable
    remittance_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    payer_paid_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    raw_clp_segment: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_remittance_claims_claim", "claim_id"),
        Index("ix_remittance_claims_status", "claim_status_code"),
        Index("ix_remittance_claims_edi_file", "edi_file_id"),
        Index(
            "ix_remittance_claims_remit_date",
            "remittance_date",
            postgresql_where="remittance_date IS NOT NULL",
        ),
    )


class Adjustment(Base, TimestampMixin):
    """A single CAS triplet. ``quantity`` is widened from v1's (12,3) to (12,5)
    to handle rare unit-per-day claims (home health, ambulance mileage)."""

    __tablename__ = "adjustments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    remittance_claim_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("remittance_claims.id", ondelete="CASCADE"),
        nullable=False,
    )
    service_line_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("claim_lines.id", ondelete="SET NULL"), nullable=True
    )

    adjustment_group_code: Mapped[str] = mapped_column(String(5), nullable=False)  # CO/PR/OA/PI/CR
    adjustment_reason_code: Mapped[str] = mapped_column(String(10), nullable=False)  # CARC
    adjustment_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 5), nullable=True)

    raw_cas_segment: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_adjustments_remit", "remittance_claim_id"),
        Index("ix_adjustments_carc_group", "adjustment_reason_code", "adjustment_group_code"),
        Index(
            "ix_adjustments_service_line",
            "service_line_id",
            postgresql_where="service_line_id IS NOT NULL",
        ),
    )


class RemarkCode(Base, TimestampMixin):
    __tablename__ = "remark_codes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    remittance_claim_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("remittance_claims.id", ondelete="CASCADE"),
        nullable=False,
    )
    service_line_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("claim_lines.id", ondelete="SET NULL"), nullable=True
    )

    remark_code: Mapped[str] = mapped_column(String(10), nullable=False)  # RARC
    raw_lq_segment: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_remark_codes_remit", "remittance_claim_id"),
        Index("ix_remark_codes_code", "remark_code"),
    )
