"""Domain A — ingestion: edi_files, raw_segments, parse_events.

``raw_segments`` and ``parse_events`` are partitioned monthly by created_at.
PG requires the partition key to be part of every unique constraint including
the primary key, so we use composite ``(id, created_at)`` PKs on those tables.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from rcm.core.database import Base
from rcm.core.enums import (
    FileType,
    HandlerStatus,
    ParseEventType,
    ParseStatus,
)
from rcm.models._mixins import CreatedAtMixin, SoftDeleteMixin, TimestampMixin


class EdiFile(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "edi_files"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    file_type: Mapped[FileType] = mapped_column(
        Enum(FileType, name="file_type", create_type=False, native_enum=True),
        nullable=False,
    )
    implementation_guide: Mapped[str | None] = mapped_column(String(20), nullable=True)
    sender_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    receiver_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    interchange_control_no: Mapped[str | None] = mapped_column(String(20), nullable=True)
    functional_group_control_no: Mapped[str | None] = mapped_column(String(20), nullable=True)

    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    parser_version: Mapped[str] = mapped_column(String(20), nullable=False)

    service_variant_detected: Mapped[str | None] = mapped_column(String(10), nullable=True)
    claim_subtype_detected: Mapped[str | None] = mapped_column(String(20), nullable=True)

    parse_status: Mapped[ParseStatus] = mapped_column(
        Enum(ParseStatus, name="parse_status", create_type=False, native_enum=True),
        nullable=False,
        default=ParseStatus.pending,
        server_default=ParseStatus.pending.value,
    )
    parse_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    parse_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    parse_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    uploaded_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        Index(
            "uq_edi_files_content_hash_alive",
            "content_hash",
            unique=True,
            postgresql_where="deleted_at IS NULL",
        ),
        Index("ix_edi_files_type_created", "file_type", "created_at"),
        Index(
            "ix_edi_files_parse_status_pending",
            "parse_status",
            "created_at",
            postgresql_where="parse_status IN ('pending', 'parsing', 'failed')",
        ),
        Index("ix_edi_files_uploaded_by", "uploaded_by_user_id", "created_at"),
    )


class RawSegment(Base):
    """Partitioned monthly by created_at."""

    __tablename__ = "raw_segments"

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )  # partition key

    edi_file_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("edi_files.id", ondelete="CASCADE"),
        nullable=False,
    )
    claim_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("claims.id", ondelete="SET NULL"),
        nullable=True,
    )
    remittance_claim_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("remittance_claims.id", ondelete="SET NULL"),
        nullable=True,
    )

    segment_name: Mapped[str] = mapped_column(String(10), nullable=False)
    segment_position: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_segment_text: Mapped[str] = mapped_column(Text, nullable=False)
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    handler_status: Mapped[HandlerStatus] = mapped_column(
        Enum(HandlerStatus, name="handler_status", create_type=False, native_enum=True),
        nullable=False,
    )

    __table_args__ = (
        PrimaryKeyConstraint("id", "created_at", name="pk_raw_segments"),
        Index("ix_raw_segments_edi_file_pos", "edi_file_id", "segment_position"),
        Index(
            "ix_raw_segments_claim_id",
            "claim_id",
            postgresql_where="claim_id IS NOT NULL",
        ),
        Index(
            "ix_raw_segments_remittance_claim_id",
            "remittance_claim_id",
            postgresql_where="remittance_claim_id IS NOT NULL",
        ),
        Index(
            "ix_raw_segments_unhandled",
            "handler_status",
            "segment_name",
            postgresql_where="handler_status <> 'handled'",
        ),
        {"postgresql_partition_by": "RANGE (created_at)"},
    )


class ParseEvent(Base, CreatedAtMixin):
    """Partitioned monthly by created_at. Telemetry surface for every segment-level event."""

    __tablename__ = "parse_events"

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, nullable=False)

    edi_file_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("edi_files.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[ParseEventType] = mapped_column(
        Enum(ParseEventType, name="parse_event_type", create_type=False, native_enum=True),
        nullable=False,
    )
    segment_name: Mapped[str | None] = mapped_column(String(10), nullable=True)
    segment_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    claim_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("id", "created_at", name="pk_parse_events"),
        Index("ix_parse_events_edi_file_type", "edi_file_id", "event_type"),
        Index(
            "ix_parse_events_type_created",
            "event_type",
            "created_at",
            postgresql_where="event_type IN ('segment_skipped', 'parse_error')",
        ),
        Index("ix_parse_events_details_gin", "details", postgresql_using="gin"),
        {"postgresql_partition_by": "RANGE (created_at)"},
    )
