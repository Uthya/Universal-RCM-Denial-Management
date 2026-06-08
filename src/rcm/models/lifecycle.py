"""Domain E — lifecycle: claim_lifecycles, appeals."""

from __future__ import annotations

from datetime import date

from sqlalchemy import (
    BigInteger,
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from rcm.core.database import Base
from rcm.core.enums import AppealLevel, AppealStatus, LifecycleRelationship
from rcm.models._mixins import TimestampMixin


class ClaimLifecycle(Base, TimestampMixin):
    __tablename__ = "claim_lifecycles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    original_claim_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    parent_claim_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    child_claim_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )

    relationship_type: Mapped[LifecycleRelationship] = mapped_column(
        Enum(
            LifecycleRelationship,
            name="lifecycle_relationship",
            create_type=False,
            native_enum=True,
        ),
        nullable=False,
    )
    iteration_number: Mapped[int] = mapped_column(Integer, nullable=False)
    days_to_resolution: Mapped[int | None] = mapped_column(Integer, nullable=True)
    diff_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index("ix_claim_lifecycles_original", "original_claim_id"),
        Index("ix_claim_lifecycles_child", "child_claim_id"),
        Index("ix_claim_lifecycles_parent_rel", "parent_claim_id", "relationship_type"),
    )


class Appeal(Base, TimestampMixin):
    __tablename__ = "appeals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    claim_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    appeal_level: Mapped[AppealLevel | None] = mapped_column(
        Enum(AppealLevel, name="appeal_level", create_type=False, native_enum=True),
        nullable=True,
    )
    status: Mapped[AppealStatus] = mapped_column(
        Enum(AppealStatus, name="appeal_status", create_type=False, native_enum=True),
        nullable=False,
    )
    submitted_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    decision_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(50), nullable=True)
    generated_letter: Mapped[str | None] = mapped_column(Text, nullable=True)
    human_edits: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_letter: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (Index("ix_appeals_claim_status", "claim_id", "status"),)
