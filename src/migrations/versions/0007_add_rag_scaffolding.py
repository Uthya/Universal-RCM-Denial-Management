"""add_rag_scaffolding: Domain H — knowledge_documents, knowledge_chunks
(with HNSW vector index), claim_embeddings, correction_examples, rag_generations.

Vector dimension is read from settings.PGVECTOR_DIMENSIONS at migration time.
If you change the dim later, a separate migration is required (HNSW reindex).

Revision ID: 0007_add_rag_scaffolding
Revises: 0006_enable_extensions
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from rcm.core.config import settings

revision: str = "0007_add_rag_scaffolding"
down_revision: str | Sequence[str] | None = "0006_enable_extensions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_DIM = settings.PGVECTOR_DIMENSIONS


def upgrade() -> None:
    op.execute(
        "CREATE TYPE knowledge_source_type AS ENUM "
        "('carc_description','rarc_description','cms_lcd','cms_ncd','payer_policy',"
        "'internal_runbook','historical_correction','claim_narrative')"
    )
    op.execute(
        "CREATE TYPE correction_outcome AS ENUM "
        "('paid','partially_paid','still_denied','abandoned')"
    )
    op.execute(
        "CREATE TYPE rag_generation_type AS ENUM "
        "('why_denied','corrective_action','appeal_letter','similar_claims','risk_reasoning')"
    )
    op.execute(
        "CREATE TYPE user_feedback AS ENUM "
        "('positive','negative','neutral','edited')"
    )

    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source_type", postgresql.ENUM(name="knowledge_source_type", create_type=False), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=True),
        sa.Column("source_table", sa.String(50), nullable=True),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("service_variant", sa.String(10), nullable=True),
        sa.Column("claim_subtype", sa.String(20), nullable=True),
        sa.Column("payer_id", sa.BigInteger(), sa.ForeignKey("payers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_knowledge_documents_src_tbl_id",
        "knowledge_documents",
        ["source_type", "source_table", "source_id"],
    )
    op.create_index(
        "ix_knowledge_documents_variant_subtype",
        "knowledge_documents",
        ["service_variant", "claim_subtype"],
    )
    op.create_index(
        "ix_knowledge_documents_metadata_gin",
        "knowledge_documents",
        ["metadata"],
        postgresql_using="gin",
    )

    # ---- knowledge_chunks ----
    op.execute(
        f"""
        CREATE TABLE knowledge_chunks (
            id                  BIGSERIAL PRIMARY KEY,
            document_id         BIGINT NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
            chunk_index         INTEGER NOT NULL,
            content             TEXT NOT NULL,
            embedding           vector({_DIM}),
            embedding_model     VARCHAR(50) NOT NULL,
            tokens              INTEGER,
            metadata            JSONB,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_knowledge_chunks_doc_idx UNIQUE (document_id, chunk_index)
        )
        """
    )
    # HNSW index — built with safer params; tune for prod later
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_embedding_hnsw "
        "ON knowledge_chunks USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )
    op.create_index("ix_knowledge_chunks_metadata_gin", "knowledge_chunks", ["metadata"], postgresql_using="gin")

    # ---- claim_embeddings ----
    op.execute(
        f"""
        CREATE TABLE claim_embeddings (
            id              BIGSERIAL PRIMARY KEY,
            claim_id        BIGINT NOT NULL UNIQUE REFERENCES claims(id) ON DELETE CASCADE,
            service_variant VARCHAR(10) NOT NULL,
            claim_subtype   VARCHAR(20),
            embedding       vector({_DIM}) NOT NULL,
            embedding_model VARCHAR(50) NOT NULL,
            generated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_claim_embeddings_embedding_hnsw "
        "ON claim_embeddings USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )
    op.create_index(
        "ix_claim_embeddings_variant_subtype",
        "claim_embeddings",
        ["service_variant", "claim_subtype"],
    )

    # ---- correction_examples ----
    op.execute(
        f"""
        CREATE TABLE correction_examples (
            id                              BIGSERIAL PRIMARY KEY,
            lifecycle_id                    BIGINT REFERENCES claim_lifecycles(id) ON DELETE CASCADE,
            original_claim_id               BIGINT REFERENCES claims(id) ON DELETE CASCADE,
            corrected_claim_id              BIGINT REFERENCES claims(id) ON DELETE CASCADE,
            denial_carc                     VARCHAR(10),
            denial_rarc                     VARCHAR(10),
            payer_id                        BIGINT REFERENCES payers(id) ON DELETE SET NULL,
            service_variant                 VARCHAR(10),
            claim_subtype                   VARCHAR(20),
            original_features_snapshot      JSONB,
            corrected_features_snapshot     JSONB,
            structural_diff                 JSONB,
            narrative                       TEXT,
            outcome                         correction_outcome NOT NULL,
            days_to_resolution              INTEGER,
            embedding                       vector({_DIM}),
            created_at                      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_correction_examples_embedding_hnsw "
        "ON correction_examples USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64) "
        "WHERE embedding IS NOT NULL"
    )
    op.create_index(
        "ix_correction_examples_carc_payer_variant",
        "correction_examples",
        ["denial_carc", "payer_id", "service_variant"],
    )
    op.create_index("ix_correction_examples_outcome", "correction_examples", ["outcome"])

    # ---- rag_generations ----
    op.create_table(
        "rag_generations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("prediction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("claim_id", sa.BigInteger(), sa.ForeignKey("claims.id", ondelete="SET NULL"), nullable=True),
        sa.Column("generation_type", postgresql.ENUM(name="rag_generation_type", create_type=False), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("retrieved_chunk_ids", postgresql.ARRAY(sa.BigInteger()), nullable=True),
        sa.Column("model_name", sa.String(50), nullable=True),
        sa.Column("generated_text", sa.Text(), nullable=True),
        sa.Column("user_feedback", postgresql.ENUM(name="user_feedback", create_type=False), nullable=True),
        sa.Column("user_edits", sa.Text(), nullable=True),
        sa.Column("tokens_input", sa.Integer(), nullable=True),
        sa.Column("tokens_output", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("cost_cents", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_rag_generations_claim_type", "rag_generations", ["claim_id", "generation_type"])
    op.create_index("ix_rag_generations_prediction", "rag_generations", ["prediction_id"])
    op.create_index(
        "ix_rag_generations_feedback",
        "rag_generations",
        ["user_feedback", "created_at"],
        postgresql_where=sa.text("user_feedback IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("rag_generations")
    op.execute("DROP TABLE IF EXISTS correction_examples")
    op.execute("DROP TABLE IF EXISTS claim_embeddings")
    op.execute("DROP TABLE IF EXISTS knowledge_chunks")
    op.drop_table("knowledge_documents")
    for enum in ("user_feedback", "rag_generation_type", "correction_outcome", "knowledge_source_type"):
        op.execute(f"DROP TYPE IF EXISTS {enum}")
