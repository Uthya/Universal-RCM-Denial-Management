"""Domain I — operations: users, audit_log, request_log, background_jobs, tenants.

``audit_log`` and ``request_log`` are partitioned monthly. Audit is written
by DB triggers (not app code) so it cannot be bypassed.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from rcm.core.database import Base
from rcm.core.enums import JobStatus, TenantIsolation, UserRole
from rcm.models._mixins import SoftDeleteMixin, TimestampMixin


class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    data_isolation_strategy: Mapped[TenantIsolation] = mapped_column(
        Enum(TenantIsolation, name="tenant_isolation", create_type=False, native_enum=True),
        nullable=False,
        default=TenantIsolation.shared_schema,
        server_default=TenantIsolation.shared_schema.value,
    )
    settings: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class User(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", create_type=False, native_enum=True),
        nullable=False,
        default=UserRole.viewer,
        server_default=UserRole.viewer.value,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    tenant_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_users_email_alive", "email", postgresql_where="deleted_at IS NULL"),
        Index("ix_users_tenant", "tenant_id", postgresql_where="tenant_id IS NOT NULL"),
    )


class AuditLog(Base):
    """Partitioned monthly by event_at. Written by audit_trigger_fn() on every
    PHI table — never write to this table directly from app code."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, nullable=False)
    event_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )  # partition key
    actor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    table_name: Mapped[str] = mapped_column(String(50), nullable=False)
    operation: Mapped[str] = mapped_column(CHAR(1), nullable=False)  # I/U/D
    row_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    diff: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("id", "event_at", name="pk_audit_log"),
        Index("ix_audit_log_table_op_at", "table_name", "operation", "event_at"),
        Index("ix_audit_log_actor_at", "actor", "event_at", postgresql_where="actor IS NOT NULL"),
        Index("ix_audit_log_row_table", "row_id", "table_name"),
        {"postgresql_partition_by": "RANGE (event_at)"},
    )


class RequestLog(Base):
    """Partitioned monthly by request_at."""

    __tablename__ = "request_log"

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, nullable=False)
    request_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )  # partition key
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("id", "request_at", name="pk_request_log"),
        Index("ix_request_log_user_at", "user_id", "request_at"),
        Index(
            "ix_request_log_status_at",
            "status_code",
            "request_at",
            postgresql_where="status_code >= 400",
        ),
        Index("ix_request_log_request_id", "request_id"),
        {"postgresql_partition_by": "RANGE (request_at)"},
    )


class BackgroundJob(Base):
    __tablename__ = "background_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", create_type=False, native_enum=True),
        nullable=False,
        default=JobStatus.queued,
        server_default=JobStatus.queued.value,
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")

    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "ix_background_jobs_active",
            "status",
            "queued_at",
            postgresql_where="status IN ('queued', 'running')",
        ),
        Index("ix_background_jobs_type_status_queued", "job_type", "status", "queued_at"),
    )
