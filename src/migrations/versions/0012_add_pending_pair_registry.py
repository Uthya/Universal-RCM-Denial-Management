"""add_pending_pair_registry: silent tracking of original→replacement pairing.

When an original 837 is uploaded, the UI deliberately does NOT warn that its
replacement is missing — the user may simply not have it yet. Instead we
record the pending pair here and check on the NEXT original upload. If by
then the prior original still has no replacement, it gets marked stale.

Rows in this table are NEVER surfaced in the upload-card UI; they exist as
an audit trail for "originals that never got a replacement on file" so the
team can chase those down out-of-band.

Schema:
    claim_number               the CLM01 of the original
    original_edi_file_id       FK to edi_files.id (where the original landed)
    original_uploaded_at       timestamp of original upload
    replacement_edi_file_id    FK to edi_files.id (NULL until replacement arrives)
    replacement_uploaded_at    timestamp of replacement upload (NULL until then)
    marked_stale_at            set when a SUBSEQUENT original upload found this
                               row still pending; signals "we noticed, no reply"

Revision ID: 0012_add_pending_pair_registry
Revises: 0011_extend_code_masters
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "0012_add_pending_pair_registry"
down_revision: str | Sequence[str] | None = "0011_extend_code_masters"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS pending_pair_registry (
            id                      BIGSERIAL PRIMARY KEY,
            claim_number            VARCHAR(50)   NOT NULL,
            original_edi_file_id    BIGINT        NOT NULL REFERENCES edi_files(id) ON DELETE CASCADE,
            original_uploaded_at    TIMESTAMPTZ   NOT NULL DEFAULT now(),
            replacement_edi_file_id BIGINT        NULL     REFERENCES edi_files(id) ON DELETE SET NULL,
            replacement_uploaded_at TIMESTAMPTZ   NULL,
            marked_stale_at         TIMESTAMPTZ   NULL,
            created_at              TIMESTAMPTZ   NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pending_pair_registry_claim_number "
        "ON pending_pair_registry (claim_number)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pending_pair_registry_pending "
        "ON pending_pair_registry (claim_number) "
        "WHERE replacement_edi_file_id IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pending_pair_registry_stale "
        "ON pending_pair_registry (marked_stale_at) "
        "WHERE marked_stale_at IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS pending_pair_registry")
