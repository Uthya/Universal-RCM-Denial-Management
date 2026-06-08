"""Re-export every mapped class so that ``from rcm.models import *`` registers
all mappers with the shared ``Base.metadata``. ``src/migrations/env.py`` relies
on this for autogenerate/diff to work.
"""

from rcm.models.claims import (
    Claim,
    ClaimLine,
    Diagnosis,
    Patient,
    Provider,
    Subscriber,
)
from rcm.models.ingestion import EdiFile, ParseEvent, RawSegment
from rcm.models.lifecycle import Appeal, ClaimLifecycle
from rcm.models.ml_pipeline import (
    FeatureSnapshot,
    ModelArtifact,
    ModelTrainingMetric,
    PredictionLog,
)
from rcm.models.operations import AuditLog, BackgroundJob, RequestLog, Tenant, User
from rcm.models.rag import (
    ClaimEmbedding,
    CorrectionExample,
    KnowledgeChunk,
    KnowledgeDocument,
    RagGeneration,
)
from rcm.models.reference import (
    CmsKnowledge,
    CmsLcdCoverage,
    CodeMaster,
    DiagnosisCode,
    NcciEdit,
    Payer,
    PayerPolicy,
    ProcedureCode,
)
from rcm.models.remittance import Adjustment, RemarkCode, RemittanceClaim
from rcm.models.variant_extensions import (
    ClaimAmount,
    ClaimAttachment,
    ClaimCertification,
    HomeCareEpisode,
    TransportCertification,
)

__all__ = [
    "Adjustment",
    "Appeal",
    "AuditLog",
    "BackgroundJob",
    "Claim",
    "ClaimAmount",
    "ClaimAttachment",
    "ClaimCertification",
    "ClaimEmbedding",
    "ClaimLifecycle",
    "ClaimLine",
    "CmsKnowledge",
    "CmsLcdCoverage",
    "CodeMaster",
    "CorrectionExample",
    "Diagnosis",
    "DiagnosisCode",
    "EdiFile",
    "FeatureSnapshot",
    "HomeCareEpisode",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "ModelArtifact",
    "ModelTrainingMetric",
    "NcciEdit",
    "ParseEvent",
    "Patient",
    "Payer",
    "PayerPolicy",
    "PredictionLog",
    "ProcedureCode",
    "Provider",
    "RagGeneration",
    "RawSegment",
    "RemarkCode",
    "RemittanceClaim",
    "RequestLog",
    "Subscriber",
    "Tenant",
    "TransportCertification",
    "User",
]
