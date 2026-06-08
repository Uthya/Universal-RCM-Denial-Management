"""add_operations: Domain I — tenants, users, audit_log (partitioned),
request_log (partitioned), background_jobs.

Also wires deferred FKs:
  edi_files.uploaded_by_user_id -> users.id
  appeals.created_by_user_id    -> users.id

Revision ID: 0004_add_operations
Revises: 0003_add_ml_pipeline
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0004_add_operations"
down_revision: str | Sequence[str] | None = "0003_add_ml_pipeline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _month_bounds(anchor: datetime) -> tuple[str, str, str]:
    start = anchor.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 \
        else start.replace(month=start.month + 1)
    return f"{start:%Y_%m}", start.isoformat(), end.isoformat()


def _ensure_monthly_partitions(parent: str, months_ahead: int = 3) -> None:
    op.execute(f"CREATE TABLE IF NOT EXISTS {parent}_default PARTITION OF {parent} DEFAULT")
    now = datetime.now(timezone.utc)
    for m in range(months_ahead + 1):
        anchor = now + timedelta(days=31 * m)
        suffix, s, e = _month_bounds(anchor)
        op.execute(
            f"CREATE TABLE IF NOT EXISTS {parent}_p{suffix} "
            f"PARTITION OF {parent} FOR VALUES FROM ('{s}') TO ('{e}')"
        )


def upgrade() -> None:
    op.execute(
        "CREATE TYPE user_role AS ENUM "
        "('viewer','biller','billing_admin','system_admin')"
    )
    op.execute(
        "CREATE TYPE job_status AS ENUM "
        "('queued','running','succeeded','failed','cancelled')"
    )
    op.execute(
        "CREATE TYPE tenant_isolation AS ENUM "
        "('shared_schema','schema_per_tenant')"
    )

    op.create_table(
        "tenants",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column(
            "data_isolation_strategy",
            postgresql.ENUM(name="tenant_isolation", create_type=False),
            nullable=False,
            server_default="shared_schema",
        ),
        sa.Column("settings", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column(
            "role",
            postgresql.ENUM(name="user_role", create_type=False),
            nullable=False,
            server_default="viewer",
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_email_alive", "users", ["email"], postgresql_where=sa.text("deleted_at IS NULL"))
    op.create_index("ix_users_tenant", "users", ["tenant_id"], postgresql_where=sa.text("tenant_id IS NOT NULL"))

    # ---- partitioned audit_log ----
    op.execute(
        """
        CREATE TABLE audit_log (
            id          BIGSERIAL,
            event_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            actor       VARCHAR(255),
            table_name  VARCHAR(50) NOT NULL,
            operation   CHAR(1) NOT NULL,
            row_id      BIGINT,
            diff        JSONB,
            ip_address  INET,
            request_id  UUID,
            CONSTRAINT pk_audit_log PRIMARY KEY (id, event_at)
        ) PARTITION BY RANGE (event_at)
        """
    )
    op.create_index("ix_audit_log_table_op_at", "audit_log", ["table_name", "operation", "event_at"])
    op.create_index("ix_audit_log_actor_at", "audit_log", ["actor", "event_at"], postgresql_where=sa.text("actor IS NOT NULL"))
    op.create_index("ix_audit_log_row_table", "audit_log", ["row_id", "table_name"])
    _ensure_monthly_partitions("audit_log")

    # ---- partitioned request_log ----
    op.execute(
        """
        CREATE TABLE request_log (
            id          BIGSERIAL,
            request_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            user_id     BIGINT REFERENCES users(id) ON DELETE SET NULL,
            request_id  UUID NOT NULL,
            method      VARCHAR(10) NOT NULL,
            path        VARCHAR(500) NOT NULL,
            status_code INTEGER NOT NULL,
            latency_ms  INTEGER NOT NULL,
            ip_address  INET,
            user_agent  TEXT,
            CONSTRAINT pk_request_log PRIMARY KEY (id, request_at)
        ) PARTITION BY RANGE (request_at)
        """
    )
    op.create_index("ix_request_log_user_at", "request_log", ["user_id", "request_at"])
    op.create_index(
        "ix_request_log_status_at",
        "request_log",
        ["status_code", "request_at"],
        postgresql_where=sa.text("status_code >= 400"),
    )
    op.create_index("ix_request_log_request_id", "request_log", ["request_id"])
    _ensure_monthly_partitions("request_log")

    op.create_table(
        "background_jobs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("job_type", sa.String(50), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="job_status", create_type=False),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("queued_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_background_jobs_active",
        "background_jobs",
        ["status", "queued_at"],
        postgresql_where=sa.text("status IN ('queued','running')"),
    )
    op.create_index(
        "ix_background_jobs_type_status_queued",
        "background_jobs",
        ["job_type", "status", "queued_at"],
    )

    # ---- wire up deferred FKs ----
    op.create_foreign_key(
        "fk_edi_files_uploaded_by_user_id_users",
        "edi_files",
        "users",
        ["uploaded_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_appeals_created_by_user_id_users",
        "appeals",
        "users",
        ["created_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_appeals_created_by_user_id_users", "appeals", type_="foreignkey")
    op.drop_constraint("fk_edi_files_uploaded_by_user_id_users", "edi_files", type_="foreignkey")
    op.drop_table("background_jobs")
    op.execute("DROP TABLE IF EXISTS request_log CASCADE")
    op.execute("DROP TABLE IF EXISTS audit_log CASCADE")
    op.drop_table("users")
    op.drop_table("tenants")
    for enum in ("tenant_isolation", "job_status", "user_role"):
        op.execute(f"DROP TYPE IF EXISTS {enum}")
