"""add_audit_triggers: trigger-based audit logging on every PHI-bearing table.

App-side audit was a v1 mistake — exceptions and raw SQL bypassed it. Triggers
cannot be bypassed.

The trigger reads ``audit.user`` and ``audit.request_id`` from session GUCs.
The app sets them via ``SET LOCAL audit.user = ...; SET LOCAL audit.request_id = ...``
at request entry. If unset (background jobs), actor is NULL.

Revision ID: 0008_add_audit_triggers
Revises: 0007_add_rag_scaffolding
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008_add_audit_triggers"
down_revision: str | Sequence[str] | None = "0007_add_rag_scaffolding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Tables that hold PHI or are otherwise audit-relevant.
_AUDITED_TABLES = (
    "claims",
    "claim_lines",
    "diagnoses",
    "patients",
    "providers",
    "subscribers",
    "remittance_claims",
    "adjustments",
    "remark_codes",
    "claim_certifications",
    "claim_amounts",
    "claim_attachments",
    "home_care_episodes",
    "transport_certifications",
    "claim_lifecycles",
    "appeals",
    "edi_files",
    "payer_policies",
    "users",
)


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_trigger_fn() RETURNS TRIGGER AS $$
        DECLARE
            v_actor      TEXT;
            v_request_id UUID;
            v_diff       JSONB;
            v_row_id     BIGINT;
        BEGIN
            -- Read session GUCs (safe defaults if unset)
            BEGIN
                v_actor := NULLIF(current_setting('audit.user', true), '');
            EXCEPTION WHEN OTHERS THEN
                v_actor := NULL;
            END;
            BEGIN
                v_request_id := NULLIF(current_setting('audit.request_id', true), '')::uuid;
            EXCEPTION WHEN OTHERS THEN
                v_request_id := NULL;
            END;

            IF TG_OP = 'INSERT' THEN
                v_diff   := jsonb_build_object('new', to_jsonb(NEW));
                v_row_id := (to_jsonb(NEW) ->> 'id')::bigint;
                INSERT INTO audit_log (event_at, actor, table_name, operation, row_id, diff, request_id)
                VALUES (now(), v_actor, TG_TABLE_NAME, 'I', v_row_id, v_diff, v_request_id);
                RETURN NEW;
            ELSIF TG_OP = 'UPDATE' THEN
                v_diff   := jsonb_build_object('old', to_jsonb(OLD), 'new', to_jsonb(NEW));
                v_row_id := (to_jsonb(NEW) ->> 'id')::bigint;
                INSERT INTO audit_log (event_at, actor, table_name, operation, row_id, diff, request_id)
                VALUES (now(), v_actor, TG_TABLE_NAME, 'U', v_row_id, v_diff, v_request_id);
                RETURN NEW;
            ELSIF TG_OP = 'DELETE' THEN
                v_diff   := jsonb_build_object('old', to_jsonb(OLD));
                v_row_id := (to_jsonb(OLD) ->> 'id')::bigint;
                INSERT INTO audit_log (event_at, actor, table_name, operation, row_id, diff, request_id)
                VALUES (now(), v_actor, TG_TABLE_NAME, 'D', v_row_id, v_diff, v_request_id);
                RETURN OLD;
            END IF;
            RETURN NULL;
        END$$ LANGUAGE plpgsql;
        """
    )

    # updated_at touch trigger (idempotent — replace if exists)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION touch_updated_at_fn() RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at := now();
            RETURN NEW;
        END$$ LANGUAGE plpgsql;
        """
    )

    for tbl in _AUDITED_TABLES:
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_audit_{tbl} ON {tbl}"
        )
        op.execute(
            f"CREATE TRIGGER trg_audit_{tbl} "
            f"AFTER INSERT OR UPDATE OR DELETE ON {tbl} "
            f"FOR EACH ROW EXECUTE FUNCTION audit_trigger_fn()"
        )

    # Apply touch trigger to every table that has an updated_at column
    for tbl in (
        "patients", "providers", "subscribers", "edi_files", "claims", "claim_lines",
        "diagnoses", "remittance_claims", "adjustments", "remark_codes",
        "claim_certifications", "home_care_episodes", "transport_certifications",
        "claim_lifecycles", "appeals", "code_masters", "payers", "procedure_codes",
        "diagnosis_codes", "payer_policies", "cms_knowledge", "tenants", "users",
        "knowledge_documents",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_touch_{tbl} ON {tbl}")
        op.execute(
            f"CREATE TRIGGER trg_touch_{tbl} "
            f"BEFORE UPDATE ON {tbl} "
            f"FOR EACH ROW EXECUTE FUNCTION touch_updated_at_fn()"
        )


def downgrade() -> None:
    for tbl in _AUDITED_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_audit_{tbl} ON {tbl}")
    for tbl in (
        "patients", "providers", "subscribers", "edi_files", "claims", "claim_lines",
        "diagnoses", "remittance_claims", "adjustments", "remark_codes",
        "claim_certifications", "home_care_episodes", "transport_certifications",
        "claim_lifecycles", "appeals", "code_masters", "payers", "procedure_codes",
        "diagnosis_codes", "payer_policies", "cms_knowledge", "tenants", "users",
        "knowledge_documents",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_touch_{tbl} ON {tbl}")
    op.execute("DROP FUNCTION IF EXISTS touch_updated_at_fn()")
    op.execute("DROP FUNCTION IF EXISTS audit_trigger_fn()")
