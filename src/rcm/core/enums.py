"""Domain enumerations.

These are mirrored into PostgreSQL ENUM types by the alembic migrations.
The string value is the canonical DB value; the Python identifier is for
in-code use. NEVER rename the value strings without a migration.
"""

from __future__ import annotations

import enum


# ============================================================================
# Ingestion
# ============================================================================
class FileType(str, enum.Enum):
    edi_837 = "edi_837"
    edi_835 = "edi_835"
    edi_277 = "edi_277"
    edi_999 = "edi_999"


class ParseStatus(str, enum.Enum):
    pending = "pending"
    parsing = "parsing"
    parsed = "parsed"
    failed = "failed"
    partial = "partial"


class HandlerStatus(str, enum.Enum):
    handled = "handled"
    skipped_unhandled = "skipped_unhandled"
    parse_error = "parse_error"
    validator_dropped = "validator_dropped"


class ParseEventType(str, enum.Enum):
    segment_handled = "segment_handled"
    segment_skipped = "segment_skipped"
    validator_warning = "validator_warning"
    validator_error = "validator_error"
    claim_dropped = "claim_dropped"
    placeholder_created = "placeholder_created"
    parse_error = "parse_error"


# ============================================================================
# Variant routing
# ============================================================================
class ServiceVariant(str, enum.Enum):
    """X12 transaction set — set at parse time from GS08."""

    p_837 = "837P"
    i_837 = "837I"
    d_837 = "837D"


class ClaimSubtype(str, enum.Enum):
    """Business flow — derived from variant + content."""

    healthcare = "healthcare"
    home_care = "home_care"
    dental = "dental"
    therapy = "therapy"
    transport = "transport"
    specialty = "specialty"
    inpatient = "inpatient"  # 837I institutional rolled up under specialty model
    hospice = "hospice"
    institutional_other = "institutional_other"


# ============================================================================
# Claims
# ============================================================================
class ClaimStatus(str, enum.Enum):
    submitted = "submitted"
    paid = "paid"
    denied = "denied"
    partially_paid = "partially_paid"
    void = "void"
    pending = "pending"


class ProviderType(str, enum.Enum):
    billing = "billing"
    rendering = "rendering"
    referring = "referring"
    supervising = "supervising"
    ordering = "ordering"
    pay_to = "pay_to"
    facility = "facility"


# ============================================================================
# Variant extensions
# ============================================================================
class CertificationType(str, enum.Enum):
    ambulance = "ambulance"
    homebound = "homebound"
    dme = "dme"
    orthodontic = "orthodontic"
    mammography = "mammography"
    epsdt = "epsdt"
    hospice_election = "hospice_election"
    plan_of_care = "plan_of_care"


# ============================================================================
# Lifecycle
# ============================================================================
class LifecycleRelationship(str, enum.Enum):
    replacement = "replacement"
    resubmission = "resubmission"
    void = "void"
    correction = "correction"
    appeal = "appeal"


class AppealLevel(str, enum.Enum):
    internal = "internal"
    external = "external"
    alj = "alj"
    dab = "dab"


class AppealStatus(str, enum.Enum):
    draft = "draft"
    submitted = "submitted"
    accepted = "accepted"
    denied = "denied"
    withdrawn = "withdrawn"


# ============================================================================
# Reference
# ============================================================================
class CodeSystem(str, enum.Enum):
    cpt = "cpt"
    hcpcs = "hcpcs"
    cdt = "cdt"
    hipps = "hipps"
    ndc = "ndc"


class DxCodeSystem(str, enum.Enum):
    icd10cm = "icd10cm"
    icd10pcs = "icd10pcs"
    icd9cm = "icd9cm"


class PolicyType(str, enum.Enum):
    coverage = "coverage"
    prior_auth = "prior_auth"
    frequency_limit = "frequency_limit"
    modifier_required = "modifier_required"
    age_limit = "age_limit"
    appeal_process = "appeal_process"
    timely_filing = "timely_filing"
    referral_required = "referral_required"


class CmsDocType(str, enum.Enum):
    lcd = "lcd"
    ncd = "ncd"
    manual_chapter = "manual_chapter"
    ncci_edit = "ncci_edit"
    mue = "mue"
    claims_processing = "claims_processing"


class NcciEditType(str, enum.Enum):
    ptp = "ptp"  # procedure-to-procedure
    mue = "mue"  # medically unlikely edits


# ============================================================================
# RAG
# ============================================================================
class KnowledgeSourceType(str, enum.Enum):
    carc_description = "carc_description"
    rarc_description = "rarc_description"
    cms_lcd = "cms_lcd"
    cms_ncd = "cms_ncd"
    payer_policy = "payer_policy"
    internal_runbook = "internal_runbook"
    historical_correction = "historical_correction"
    claim_narrative = "claim_narrative"


class CorrectionOutcome(str, enum.Enum):
    paid = "paid"
    partially_paid = "partially_paid"
    still_denied = "still_denied"
    abandoned = "abandoned"


class GenerationType(str, enum.Enum):
    why_denied = "why_denied"
    corrective_action = "corrective_action"
    appeal_letter = "appeal_letter"
    similar_claims = "similar_claims"
    risk_reasoning = "risk_reasoning"


class UserFeedback(str, enum.Enum):
    positive = "positive"
    negative = "negative"
    neutral = "neutral"
    edited = "edited"


# ============================================================================
# Operations
# ============================================================================
class UserRole(str, enum.Enum):
    viewer = "viewer"
    biller = "biller"
    billing_admin = "billing_admin"
    system_admin = "system_admin"


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class TenantIsolation(str, enum.Enum):
    shared_schema = "shared_schema"
    schema_per_tenant = "schema_per_tenant"


# ============================================================================
# ML
# ============================================================================
class ArtifactType(str, enum.Enum):
    xgboost_model = "xgboost_model"
    calibrator = "calibrator"
    feature_encoder = "feature_encoder"
    feature_schema = "feature_schema"
    distributions = "distributions"


class RiskLevel(str, enum.Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# ============================================================================
# Helpers
# ============================================================================
def variant_subtype_routing_key(variant: ServiceVariant | str, subtype: ClaimSubtype | str) -> str:
    """Stable key for routing to per-variant model artifacts."""
    v = variant.value if isinstance(variant, ServiceVariant) else variant
    s = subtype.value if isinstance(subtype, ClaimSubtype) else subtype
    return f"{v}__{s}"
