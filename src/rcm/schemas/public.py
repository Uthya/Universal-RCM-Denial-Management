"""Response schemas for `/api/*` (v1-frontend-facing endpoints).

These match the response shapes the legacy v1 frontend's `services/api.js`
expects — DO NOT renegotiate without coordinating with the frontend.

Distinct from `schemas.dev` which serves the developer console at `/api/dev/*`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# /edi/upload + /edi/files
# ---------------------------------------------------------------------------

class EdiValidationError(BaseModel):
    segment: str
    field: str
    message: str
    severity: str | None = None
    claim_identifier: str | None = None


class EdiUploadResponse(BaseModel):
    success: bool
    edi_file_id: int | None
    file_type: str | None
    claims_count: int
    claim_lines_count: int
    diagnoses_count: int
    remittance_claims_count: int
    adjustments_count: int
    remark_codes_count: int
    raw_segments_count: int
    validation_errors: list[EdiValidationError] = Field(default_factory=list)
    parser_version: str | None = None
    error: str | None = None
    # Duplicate-file detection (content_hash already in DB)
    is_duplicate: bool = False
    duplicate_of_file_id: int | None = None
    # Pair-completeness check (original ↔ replacement; 837 ↔ 835)
    pair_status: str | None = Field(
        None,
        description="paired | original_no_replacement | replacement_no_original | "
                    "remit_no_837 | none",
    )
    pair_message: str | None = None


class EdiFileListItem(BaseModel):
    id: int
    file_name: str
    file_type: str
    parse_status: str
    claims_count: int
    uploaded_at: str


class EdiFilesListResponse(BaseModel):
    items: list[EdiFileListItem]
    total: int


# ---------------------------------------------------------------------------
# /claims
# ---------------------------------------------------------------------------

class ClaimListItem(BaseModel):
    id: int
    claim_number: str
    payer_name: str | None
    total_charge_amount: float
    claim_status: str
    service_from_date: str | None
    created_at: str | None
    patient_member_id: str | None


class ClaimsListResponse(BaseModel):
    items: list[ClaimListItem]
    total: int


class ClaimLineItem(BaseModel):
    id: int
    line_number: int
    procedure_code: str | None
    modifier1: str | None
    modifier2: str | None
    billed_amount: float | None
    units: float | None
    service_date: str | None
    place_of_service: str | None


class DiagnosisItem(BaseModel):
    id: int
    sequence_number: int
    diagnosis_code: str
    diagnosis_type: str


class AdjustmentItem(BaseModel):
    id: int
    adjustment_group_code: str
    adjustment_reason_code: str
    adjustment_amount: float


class RemarkCodeItem(BaseModel):
    id: int
    remark_code: str


class RemittanceClaimItem(BaseModel):
    id: int
    claim_status_code: str
    billed_amount: float
    paid_amount: float
    remittance_date: str | None
    payer_claim_control_number: str | None
    adjustments: list[AdjustmentItem]
    remark_codes: list[RemarkCodeItem]


class ClaimDetailResponse(BaseModel):
    id: int
    claim_number: str
    payer_name: str | None
    patient_member_id: str | None
    total_charge_amount: float
    claim_status: str
    service_from_date: str | None
    service_to_date: str | None
    facility_type_code: str | None
    previous_payer_claim_control_no: str | None
    claim_lines: list[ClaimLineItem]
    diagnoses: list[DiagnosisItem]
    remittance_claims: list[RemittanceClaimItem]


# ---------------------------------------------------------------------------
# /predictions
# ---------------------------------------------------------------------------

class DatasetStatsResponse(BaseModel):
    total: int
    denied: int
    paid: int
    denial_rate: float


class TrainMetrics(BaseModel):
    """Metrics surface — populated from the HELD-OUT slice post-CR-075 so the
    numbers represent realistic production performance. Validation + OOF
    diagnostics are available under `evaluation` below."""
    accuracy: float | None = None
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None
    roc_auc: float | None = None
    pr_auc: float | None = None
    brier: float | None = None


class TrainSplit(BaseModel):
    """CR-075: corpus is split into three stratified slices.

    - train_samples       : rows used to fit FB + final model
    - validation_samples  : rows used for calibrator + threshold selection
    - held_out_samples    : rows used for the reported `metrics` (never seen
                            during training, calibration, or threshold pick)
    - test_samples        : alias for held_out_samples (preserved for back-
                            compatibility with the v1 frontend's expected
                            field name)
    """
    train_samples: int
    validation_samples: int = 0
    held_out_samples: int = 0
    test_samples: int  # kept for v1 frontend compatibility; equals held_out_samples


class TrainEvaluation(BaseModel):
    """Diagnostic surfaces: OOF + validation alongside the held-out metrics."""
    oof_metrics: TrainMetrics | None = None
    validation_metrics: TrainMetrics | None = None
    held_out_metrics: TrainMetrics | None = None
    decision_threshold: float | None = None


class TrainModelResponse(BaseModel):
    status: str
    split: TrainSplit
    metrics: TrainMetrics
    training_time_seconds: float
    model_version: str | None = None
    evaluation: TrainEvaluation | None = None
    # CR-080: in-memory grouping handle that lets the frontend match the
    # immediate response to the just-created history row(s). Not persisted.
    training_run_id: str | None = None


class RiskFactorItem(BaseModel):
    feature: str
    direction: str
    impact: str


class UnseenIndicators(BaseModel):
    payer: bool = False
    cpt: bool = False
    dx: bool = False


class PredictClaimResponse(BaseModel):
    risk_level: str
    risk_score: float
    prediction_timestamp: str
    top_risk_factors: list[RiskFactorItem]
    unseen_indicators: UnseenIndicators
    model_version: str


class DenialReasonItem(BaseModel):
    feature: str
    label: str
    reason: str | None = None       # full sentence explaining why
    fix: str | None = None          # actionable next step
    impact: float
    direction: str = "increases denial risk"


class HighRiskClaimItem(BaseModel):
    claim_id: int
    claim_number: str
    payer_name: str | None = None
    service_variant: str | None = None
    claim_subtype: str | None = None
    risk_score: float
    risk_level: str
    top_denial_reasons: list[DenialReasonItem] = Field(default_factory=list)


class PredictFileResponse(BaseModel):
    edi_file_id: int
    predicted_claims: int
    risk_summary: dict[str, int]
    high_risk_claims: list[HighRiskClaimItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# /ml/training-history
# ---------------------------------------------------------------------------

class TrainingHistoryItem(BaseModel):
    """Legacy per-variant row shape — preserved for any external caller that
    still wants the flat surface (e.g. the /latest-training endpoint's old
    consumers). CR-080 added TrainingRunItem as the primary grouped shape."""
    training_id: str
    training_timestamp: str
    model_version: str | None
    total_claims_used: int
    training_samples: int
    test_samples: int
    accuracy: float | None
    f1_score: float | None
    precision: float | None
    recall: float | None
    roc_auc: float | None
    denial_rate: float | None
    training_time_seconds: float | None


class TrainingVariantMetrics(BaseModel):
    """Per-variant block inside a grouped training run (CR-080)."""
    training_id: str
    service_variant: str                  # "837P" / "837D" / "837I"
    claim_subtype: str | None             # "healthcare" / "dental" / "home_care"
    model_version: str | None
    training_timestamp: str
    decision_threshold: float | None
    total_claims_used: int
    training_samples: int
    validation_samples: int
    accuracy: float | None
    f1_score: float | None
    precision: float | None
    recall: float | None
    roc_auc: float | None
    pr_auc: float | None
    denial_rate: float | None
    training_time_seconds: float | None


class TrainingRunItem(BaseModel):
    """One logical training invocation, with one entry per variant trained
    in that invocation. CR-080 presentation-layer grouping (no schema change).

    ``training_run_id`` is synthetic — a deterministic hash of the earliest
    member row's id. ``variants`` is keyed by ``claim_subtype`` so the UI can
    look up healthcare / dental / home_care directly.
    """
    training_run_id: str
    started_at: str                       # earliest training_timestamp in the run
    ended_at: str                         # latest training_timestamp in the run
    training_time_seconds: float | None   # sum of variant durations (None if missing)
    model_version_group: str | None       # common prefix across variants when present
    status: str                           # "success" if all variants succeeded
    variant_count: int                    # 1..3 in practice
    variants: dict[str, TrainingVariantMetrics] = Field(default_factory=dict)


class TrainingHistoryResponse(BaseModel):
    items: list[TrainingRunItem]
    total: int                            # total runs (not rows)


# ---------------------------------------------------------------------------
# /recommendations/by-file
# ---------------------------------------------------------------------------

class RecommendationItem(BaseModel):
    source: str = Field(..., description="parser | carc | model")
    location: str | None = None
    reason: str
    fix: str


class RecommendedClaim(BaseModel):
    claim_id: int
    claim_number: str
    payer_name: str | None
    risk_score: float | None = None
    status_badge: str = Field(..., description="Denied | High Risk | Resolved")
    resolved: bool = False
    recommendations: list[RecommendationItem]


class RecommendationsResponse(BaseModel):
    edi_file_id: int
    total_claims_in_file: int
    flagged_claims: int
    claims: list[RecommendedClaim]


class RecommendationRequest(BaseModel):
    validation_errors: list[Any] = Field(default_factory=list)
