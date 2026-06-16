"""drop_audit_log: remove audit triggers + audit_log table per user direction.

The audit infrastructure introduced in 0008_add_audit_triggers proved too
expensive in practice: a single `_propagate_remit_status_to_claims` UPDATE
(touching every adjudicated claim after each 835) inflated audit_log to 7M+
rows / 8 GB after only a few days of ingestion. The triggers were also
faithfully recording the bulk inserts from every 837 upload, multiplying
write volume without an active downstream consumer (no audit-viewer UI, no
compliance pipeline plugged in yet).

User decision (CR-051): drop the audit infrastructure entirely. Re-introduce
later in a more targeted form when there's an actual consumer of the audit
trail.

This migration drops:
    * 19 row-level audit triggers (one per audited table)
    * the shared audit_trigger_fn() PL/pgSQL function
    * the partitioned audit_log table + all monthly partitions
    * the audit_log_id_seq sequence

`downgrade()` restores the table + triggers from the contract documented in
0008_add_audit_triggers (idempotent — uses CREATE OR REPLACE / IF NOT EXISTS
everywhere). It does NOT back-fill audit rows; rolling back gets you a fresh
empty audit_log.

Revision ID: 0014_drop_audit_log
Revises: 0013_add_shadow_logging
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "0014_drop_audit_log"
down_revision: str | Sequence[str] | None = "0013_add_shadow_logging"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Tables that 0008 added triggers to — copy of the list from that migration
_AUDITED_TABLES = (
    "claims",
    "claim_lines",
    "diagnoses",
    "adjustments",
    "remark_codes",
    "remittance_claims",
    "claim_amounts",
    "claim_attachments",
    "claim_certifications",
    "claim_lifecycles",
    "edi_files",
    "home_care_episodes",
    "patients",
    "payer_policies",
    "providers",
    "subscribers",
    "transport_certifications",
    "users",
    "appeals",
)


def upgrade() -> None:
    # 1. Drop triggers (CASCADE not needed — trigger drop is local)
    for tbl in _AUDITED_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_audit_{tbl} ON {tbl}")

    # 2. Drop the shared trigger function
    op.execute("DROP FUNCTION IF EXISTS audit_trigger_fn() CASCADE")

    # 3. Drop the partitioned table and all its partitions in one CASCADE
    op.execute("DROP TABLE IF EXISTS audit_log CASCADE")
    op.execute("DROP SEQUENCE IF EXISTS audit_log_id_seq")


def downgrade() -> None:
    # Restore from the 0008 contract — partitioned table + 19 triggers.
    # NOTE: this only restores the schema; historical rows are gone forever.

    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id          BIGSERIAL,
            event_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            actor       VARCHAR(80),
            table_name  VARCHAR(60) NOT NULL,
            operation   CHAR(1)     NOT NULL CHECK (operation IN ('I','U','D')),
            row_id      BIGINT,
            diff        JSONB,
            ip_address  INET,
            request_id  UUID,
            PRIMARY KEY (id, event_at)
        ) PARTITION BY RANGE (event_at)
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_log_default
            PARTITION OF audit_log DEFAULT
    """)
    # Create one partition for the current month — operators add others as needed
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_log_p_current PARTITION OF audit_log
        FOR VALUES FROM (date_trunc('month', now())) TO (date_trunc('month', now()) + interval '1 month')
    """)

    # Trigger function: capture row diff + session-variable actor/request_id
    op.execute("""
        CREATE OR REPLACE FUNCTION audit_trigger_fn() RETURNS trigger AS $$
        DECLARE
            v_actor      text := current_setting('audit.user',        true);
            v_request_id uuid := NULLIF(current_setting('audit.request_id', true), '')::uuid;
            v_row_id     bigint;
            v_diff       jsonb;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                v_row_id := (row_to_json(NEW)->>'id')::bigint;
                v_diff   := jsonb_build_object('new', to_jsonb(NEW));
            ELSIF TG_OP = 'UPDATE' THEN
                v_row_id := (row_to_json(NEW)->>'id')::bigint;
                v_diff   := jsonb_build_object('old', to_jsonb(OLD), 'new', to_jsonb(NEW));
            ELSE
                v_row_id := (row_to_json(OLD)->>'id')::bigint;
                v_diff   := jsonb_build_object('old', to_jsonb(OLD));
            END IF;

            INSERT INTO audit_log (actor, table_name, operation, row_id, diff, request_id)
            VALUES (v_actor, TG_TABLE_NAME, LEFT(TG_OP, 1), v_row_id, v_diff, v_request_id);

            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END
        $$ LANGUAGE plpgsql
    """)

    for tbl in _AUDITED_TABLES:
        op.execute(f"""
            CREATE TRIGGER trg_audit_{tbl}
            AFTER INSERT OR UPDATE OR DELETE ON {tbl}
            FOR EACH ROW EXECUTE FUNCTION audit_trigger_fn()
        """)
