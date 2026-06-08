"""Domain H — RAG infrastructure (scaffolded; tables created from day one,
provider implementation comes later).

Vector dim is sourced from ``settings.PGVECTOR_DIMENSIONS`` at import time.
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from rcm.core.config import settings
from rcm.core.database import Base
from rcm.core.enums import (
    CorrectionOutcome,
    GenerationType,
    KnowledgeSourceType,
    UserFeedback,
)
from rcm.models._mixins import CreatedAtMixin, TimestampMixin

_VECTOR_DIM = settings.PGVECTOR_DIMENSIONS


class KnowledgeDocument(Base, TimestampMixin):
    __tablename__ = "knowledge_documents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_type: Mapped[KnowledgeSourceType] = mapped_column(
        Enum(
            KnowledgeSourceType,
            name="knowledge_source_type",
            create_type=False,
            native_enum=True,
        ),
        nullable=False,
    )
    source_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_table: Mapped[str | None] = mapped_column(String(50), nullable=True)

    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    service_variant: Mapped[str | None] = mapped_column(String(10), nullable=True)
    claim_subtype: Mapped[str | None] = mapped_column(String(20), nullable=True)
    payer_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("payers.id", ondelete="SET NULL"), nullable=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    doc_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)

    __table_args__ = (
        Index(
            "ix_knowledge_documents_src_tbl_id",
            "source_type",
            "source_table",
            "source_id",
        ),
        Index("ix_knowledge_documents_variant_subtype", "service_variant", "claim_subtype"),
        # NOTE: index references the SQL column name "metadata", not the Python attr "doc_metadata"
        Index("ix_knowledge_documents_metadata_gin", "metadata", postgresql_using="gin"),
    )


class KnowledgeChunk(Base, CreatedAtMixin):
    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(_VECTOR_DIM), nullable=True)
    embedding_model: Mapped[str] = mapped_column(String(50), nullable=False)
    tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_knowledge_chunks_doc_idx"),
        # HNSW index is created in the migration (Vector index needs special opclass)
        # NOTE: index references the SQL column name "metadata", not the Python attr "chunk_metadata"
        Index("ix_knowledge_chunks_metadata_gin", "metadata", postgresql_using="gin"),
    )


class ClaimEmbedding(Base):
    __tablename__ = "claim_embeddings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    claim_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("claims.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    service_variant: Mapped[str] = mapped_column(String(10), nullable=False)
    claim_subtype: Mapped[str | None] = mapped_column(String(20), nullable=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(_VECTOR_DIM), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(50), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_claim_embeddings_variant_subtype", "service_variant", "claim_subtype"),
    )


class CorrectionExample(Base, CreatedAtMixin):
    __tablename__ = "correction_examples"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    lifecycle_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("claim_lifecycles.id", ondelete="CASCADE"),
        nullable=True,
    )
    original_claim_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("claims.id", ondelete="CASCADE"), nullable=True
    )
    corrected_claim_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("claims.id", ondelete="CASCADE"), nullable=True
    )

    denial_carc: Mapped[str | None] = mapped_column(String(10), nullable=True)
    denial_rarc: Mapped[str | None] = mapped_column(String(10), nullable=True)
    payer_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("payers.id", ondelete="SET NULL"), nullable=True
    )
    service_variant: Mapped[str | None] = mapped_column(String(10), nullable=True)
    claim_subtype: Mapped[str | None] = mapped_column(String(20), nullable=True)

    original_features_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    corrected_features_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    structural_diff: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    narrative: Mapped[str | None] = mapped_column(Text, nullable=True)

    outcome: Mapped[CorrectionOutcome] = mapped_column(
        Enum(CorrectionOutcome, name="correction_outcome", create_type=False, native_enum=True),
        nullable=False,
    )
    days_to_resolution: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(_VECTOR_DIM), nullable=True)

    __table_args__ = (
        Index(
            "ix_correction_examples_carc_payer_variant",
            "denial_carc",
            "payer_id",
            "service_variant",
        ),
        Index("ix_correction_examples_outcome", "outcome"),
    )


class RagGeneration(Base, CreatedAtMixin):
    __tablename__ = "rag_generations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    prediction_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    claim_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("claims.id", ondelete="SET NULL"), nullable=True
    )
    generation_type: Mapped[GenerationType] = mapped_column(
        Enum(GenerationType, name="rag_generation_type", create_type=False, native_enum=True),
        nullable=False,
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    retrieved_chunk_ids: Mapped[list[int] | None] = mapped_column(ARRAY(BigInteger), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    generated_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_feedback: Mapped[UserFeedback | None] = mapped_column(
        Enum(UserFeedback, name="user_feedback", create_type=False, native_enum=True),
        nullable=True,
    )
    user_edits: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens_input: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_output: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_rag_generations_claim_type", "claim_id", "generation_type"),
        Index("ix_rag_generations_prediction", "prediction_id"),
        Index(
            "ix_rag_generations_feedback",
            "user_feedback",
            "created_at",
            postgresql_where="user_feedback IS NOT NULL",
        ),
    )
