"""add_domain_functions: the small set of PL/pgSQL helpers the spec mandates.

Most logic stays in app code. These functions only exist for things that are
either (a) repeated SQL patterns where a function reads cleaner, or (b)
recursive walks that are awkward to express in app code.

  derive_service_variant(edi_file_id) -> text
  is_denied(claim_id) -> bool
  claim_lifecycle_resolved(claim_id) -> bool
  find_similar_claims(emb vector, limit int, variant text) -> setof bigint

Revision ID: 0009_add_domain_functions
Revises: 0008_add_audit_triggers
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from rcm.core.config import settings

revision: str = "0009_add_domain_functions"
down_revision: str | Sequence[str] | None = "0008_add_audit_triggers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DIM = settings.PGVECTOR_DIMENSIONS


def upgrade() -> None:
    # derive_service_variant: read GS08 out of raw_segments for the file
    op.execute(
        """
        CREATE OR REPLACE FUNCTION derive_service_variant(p_edi_file_id BIGINT)
        RETURNS TEXT AS $$
        DECLARE
            gs08 TEXT;
        BEGIN
            SELECT split_part(raw_segment_text, E'*', 9)
              INTO gs08
              FROM raw_segments
             WHERE edi_file_id = p_edi_file_id
               AND segment_name = 'GS'
             LIMIT 1;
            RETURN CASE
                WHEN gs08 LIKE '005010X222%' THEN '837P'
                WHEN gs08 LIKE '005010X223%' THEN '837I'
                WHEN gs08 LIKE '005010X224%' THEN '837D'
                WHEN gs08 LIKE '005010X221%' THEN '835'
                ELSE NULL
            END;
        END$$ LANGUAGE plpgsql STABLE;
        """
    )

    # is_denied: any remittance row with CLP02 = 4
    op.execute(
        """
        CREATE OR REPLACE FUNCTION is_denied(p_claim_id BIGINT) RETURNS BOOLEAN AS $$
        BEGIN
            RETURN EXISTS (
                SELECT 1 FROM remittance_claims
                 WHERE claim_id = p_claim_id
                   AND claim_status_code = '4'
            );
        END$$ LANGUAGE plpgsql STABLE;
        """
    )

    # claim_lifecycle_resolved: walks the chain to see if any descendant is paid
    op.execute(
        """
        CREATE OR REPLACE FUNCTION claim_lifecycle_resolved(p_claim_id BIGINT)
        RETURNS BOOLEAN AS $$
        DECLARE
            v_found BOOLEAN;
        BEGIN
            WITH RECURSIVE chain AS (
                SELECT id, claim_status FROM claims WHERE id = p_claim_id
                UNION ALL
                SELECT c.id, c.claim_status
                  FROM claim_lifecycles cl
                  JOIN claims c ON c.id = cl.child_claim_id
                  JOIN chain ch ON ch.id = cl.parent_claim_id
            )
            SELECT EXISTS (
                SELECT 1 FROM chain WHERE claim_status IN ('paid', 'partially_paid')
            ) INTO v_found;
            RETURN COALESCE(v_found, FALSE);
        END$$ LANGUAGE plpgsql STABLE;
        """
    )

    # find_similar_claims: HNSW-backed nearest-neighbor lookup wrapper
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION find_similar_claims(
            p_embedding vector({_DIM}),
            p_limit     INTEGER DEFAULT 10,
            p_variant   TEXT    DEFAULT NULL,
            p_subtype   TEXT    DEFAULT NULL
        ) RETURNS TABLE (claim_id BIGINT, distance REAL) AS $$
        BEGIN
            RETURN QUERY
            SELECT ce.claim_id, (ce.embedding <=> p_embedding) AS distance
              FROM claim_embeddings ce
             WHERE (p_variant IS NULL OR ce.service_variant = p_variant)
               AND (p_subtype IS NULL OR ce.claim_subtype = p_subtype)
             ORDER BY ce.embedding <=> p_embedding
             LIMIT p_limit;
        END$$ LANGUAGE plpgsql STABLE;
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS find_similar_claims(vector, integer, text, text)")
    op.execute("DROP FUNCTION IF EXISTS claim_lifecycle_resolved(bigint)")
    op.execute("DROP FUNCTION IF EXISTS is_denied(bigint)")
    op.execute("DROP FUNCTION IF EXISTS derive_service_variant(bigint)")
