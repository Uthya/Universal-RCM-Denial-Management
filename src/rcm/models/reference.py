"""Domain F — reference / master data.

Includes the two new tables that the revised feature engineering needs:
``ncci_edits`` (procedure-to-procedure edits + medically-unlikely-edits)
and ``cms_lcd_coverage`` (CPT × Dx × state coverage lookups).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from rcm.core.database import Base
from rcm.core.enums import CmsDocType, CodeSystem, DxCodeSystem, NcciEditType, PolicyType
from rcm.models._mixins import TimestampMixin


class CodeMaster(Base, TimestampMixin):
    """CARC, RARC, POS, claim_status, etc. — loaded from WPC quarterly."""

    __tablename__ = "code_masters"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code_type: Mapped[str] = mapped_column(String(20), nullable=False)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    deactivated_at: Mapped[date | None] = mapped_column(Date, nullable=True)

    __table_args__ = (UniqueConstraint("code_type", "code", name="uq_code_masters_type_code"),)


class Payer(Base, TimestampMixin):
    __tablename__ = "payers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    sender_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    receiver_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    payer_taxonomy: Mapped[str | None] = mapped_column(String(50), nullable=True)
    aliases: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    contact_info: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (Index("ix_payers_aliases_gin", "aliases", postgresql_using="gin"),)


class ProcedureCode(Base, TimestampMixin):
    """CPT / HCPCS / CDT / HIPPS / NDC catalog.

    The ``metadata`` JSONB holds the keys consumed by feature engineering:
        - ``annual_limit`` (int): frequency cap per calendar year
        - ``requires_pwk`` (bool): attachment required
        - ``requires_modifier`` (str | None): expected modifier (e.g. 'KX')
        - ``age_min`` / ``age_max`` (int): coverage age window
        - ``gender_restriction`` ('M' | 'F' | None)
        - ``valid_pos_codes`` (list[str])
        - ``category`` (str): 'E&M' / 'surgery' / 'preventive' / ...
        - ``global_period_days`` (int): 0/10/90 typical
    """

    __tablename__ = "procedure_codes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    code_system: Mapped[CodeSystem] = mapped_column(
        Enum(CodeSystem, name="code_system", create_type=False, native_enum=True),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    deprecated_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # The JSONB column is named ``code_metadata`` in Python to avoid clashing with
    # SQLAlchemy's ``Base.metadata`` attribute. Stored as ``metadata`` in PG.
    code_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("code", "code_system", name="uq_procedure_codes_code_system"),
        Index("ix_procedure_codes_category", "category"),
    )


class DiagnosisCode(Base, TimestampMixin):
    __tablename__ = "diagnosis_codes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    code_system: Mapped[DxCodeSystem] = mapped_column(
        Enum(DxCodeSystem, name="dx_code_system", create_type=False, native_enum=True),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    chapter: Mapped[str | None] = mapped_column(String(100), nullable=True)
    category: Mapped[str | None] = mapped_column(String(20), nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    code_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)


class PayerPolicy(Base, TimestampMixin):
    __tablename__ = "payer_policies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    payer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("payers.id", ondelete="CASCADE"), nullable=False
    )
    policy_type: Mapped[PolicyType] = mapped_column(
        Enum(PolicyType, name="policy_type", create_type=False, native_enum=True),
        nullable=False,
    )
    applies_to_codes: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    service_variant: Mapped[str | None] = mapped_column(String(10), nullable=True)
    claim_subtype: Mapped[str | None] = mapped_column(String(20), nullable=True)
    policy_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_rule: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        Index("ix_payer_policies_payer_type", "payer_id", "policy_type"),
        Index("ix_payer_policies_codes_gin", "applies_to_codes", postgresql_using="gin"),
        Index("ix_payer_policies_rule_gin", "structured_rule", postgresql_using="gin"),
    )


class CmsKnowledge(Base, TimestampMixin):
    __tablename__ = "cms_knowledge"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_type: Mapped[CmsDocType] = mapped_column(
        Enum(CmsDocType, name="cms_doc_type", create_type=False, native_enum=True),
        nullable=False,
    )
    document_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str | None] = mapped_column(String(2), nullable=True)
    contractor: Mapped[str | None] = mapped_column(String(20), nullable=True)
    applies_to_codes: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    revision_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    code_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)

    __table_args__ = (
        Index("ix_cms_knowledge_type_state", "document_type", "state"),
        Index("ix_cms_knowledge_codes_gin", "applies_to_codes", postgresql_using="gin"),
    )


class NcciEdit(Base):
    """National Correct Coding Initiative — procedure-to-procedure + MUE.

    Loaded quarterly from CMS. Used by the ``is_likely_unbundled`` feature
    and the Tier-4 unbundling validator.
    """

    __tablename__ = "ncci_edits"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    column1_code: Mapped[str] = mapped_column(String(20), nullable=False)  # comprehensive
    column2_code: Mapped[str] = mapped_column(String(20), nullable=False)  # component
    edit_type: Mapped[NcciEditType] = mapped_column(
        Enum(NcciEditType, name="ncci_edit_type", create_type=False, native_enum=True),
        nullable=False,
    )
    # 0 = cannot override; 1 = can override with 59/X{EPSU}; 9 = N/A
    modifier_indicator: Mapped[int | None] = mapped_column(Integer, nullable=True)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    deletion_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    rationale: Mapped[str | None] = mapped_column(String(500), nullable=True)
    code_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "column1_code",
            "column2_code",
            "edit_type",
            "effective_date",
            name="uq_ncci_edits_pair_type_eff",
        ),
        Index(
            "ix_ncci_edits_lookup",
            "column1_code",
            "column2_code",
            postgresql_where="deletion_date IS NULL",
        ),
    )


class CmsLcdCoverage(Base):
    """LCD coverage indications (CPT × Dx × state)."""

    __tablename__ = "cms_lcd_coverage"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    lcd_id: Mapped[str] = mapped_column(String(20), nullable=False)
    state: Mapped[str | None] = mapped_column(String(2), nullable=True)
    contractor: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cpt_codes: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    covered_dx_codes: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    excluded_dx_codes: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    revision_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    document_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_cms_lcd_cpt_gin", "cpt_codes", postgresql_using="gin"),
        Index("ix_cms_lcd_state", "state"),
    )
