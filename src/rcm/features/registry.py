"""Feature registry — single source of truth for what each feature is, where
it comes from, when it's computable, and what its leakage risk is.

Train/predict parity contract:
    1. FEATURE_COLUMNS_<variant> is the ORDERED list of features the model
       sees. XGBoost mis-attributes SHAP silently if column ORDER drifts,
       so we enforce strict equality at predict time (M1).
    2. Every feature has a FeatureSpec entry documenting its metadata.
    3. `reference_data_completeness` is itself a feature — never hidden.
    4. Schema dimensionality is stable across ref-data state — features
       that depend on missing data emit safe defaults, not absences.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------------------
# Enums for FeatureSpec metadata fields
# ---------------------------------------------------------------------------

class FeatureCategory(str, Enum):
    """Denial-cause-driven categories from spec §4.2."""
    COVERAGE = "A_coverage"
    AUTHORIZATION = "B_authorization"
    CLINICAL = "C_clinical"
    CODING = "D_coding"
    TIMELY = "E_timely"
    DOCUMENTATION = "F_documentation"
    PATIENT_HISTORY = "G_patient_history"
    PROVIDER = "H_provider"
    JOINT = "I_joint_encoders"
    BASE = "J_base"
    ENCODED_CATEGORICAL = "K_encoded_categorical"
    RARITY = "L_rarity_unseen"
    VARIANT = "M_variant_specific"
    AVAILABILITY = "Z_availability"


class FeatureSource(str, Enum):
    """Where the feature's data ultimately originates."""
    CLAIM = "claim"                       # claims/claim_lines/diagnoses
    PATIENT = "patient"
    PROVIDER = "provider"
    PAYER = "payer"
    SUBSCRIBER = "subscriber"
    REFERENCE_DATA = "reference_data"     # procedure_codes / ncci_edits / lcd
    PAYER_POLICY = "payer_policy"
    MATERIALIZED_VIEW = "materialized_view"
    DERIVED = "derived"                   # composed from other features


class PredictionAvailability(str, Enum):
    """Is this feature computable at PREDICT time (before remittance is back)?"""
    ALWAYS = "always"                     # claim fields, deterministic
    REQUIRES_REF_DATA = "requires_ref_data"  # falls back to safe default if missing
    REQUIRES_HISTORY = "requires_history"    # window over prior claims
    PREDICT_TIME_INVALID = "predict_time_invalid"  # uses outcome — leakage!


class LeakageRisk(str, Enum):
    NONE = "none"                         # safe — pre-adjudication data only
    LOW = "low"                           # uses aggregate stats fit on training only
    MEDIUM = "medium"                     # needs careful CV (target encoders)
    HIGH = "high"                         # potential outcome leak — must verify


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    """Metadata for one feature. Used by:
       - the builder (knows what computes it)
       - the registry-enforcement check (M1)
       - the monitoring / drift surface
       - the prediction service (which features need ref-data fallback)
    """
    name: str
    category: FeatureCategory
    source: FeatureSource
    dtype: str                              # "float" | "int" | "bool"
    description: str
    availability: PredictionAvailability = PredictionAvailability.ALWAYS
    leakage_risk: LeakageRisk = LeakageRisk.NONE
    default_value: float | int | bool = 0
    requires_ref_table: str | None = None   # e.g. "ncci_edits"
    notes: str = ""


# ---------------------------------------------------------------------------
# Feature definitions — categories J/L universally first, then A–I, K, M
# Order within each variant's column list matters; see FEATURE_COLUMNS_*.
# ---------------------------------------------------------------------------

_F = FeatureSpec
_CAT = FeatureCategory
_SRC = FeatureSource
_AVL = PredictionAvailability
_RISK = LeakageRisk


# Category J — base claim features (11 features; CR-104 retired is_single_day_service)
_BASE = (
    _F("total_charge_amount",       _CAT.BASE, _SRC.CLAIM,   "float", "CLM02 total claim charge"),
    _F("total_billed_amount",       _CAT.BASE, _SRC.CLAIM,   "float", "sum of line.billed_amount"),
    _F("charge_to_billed_ratio",    _CAT.BASE, _SRC.DERIVED, "float", "total_charge_amount / max(total_billed_amount,0.01)"),
    _F("line_count",                _CAT.BASE, _SRC.CLAIM,   "int",   "count of claim_lines"),
    _F("diagnosis_count",           _CAT.BASE, _SRC.CLAIM,   "int",   "count of diagnoses"),
    _F("total_units",               _CAT.BASE, _SRC.CLAIM,   "float", "sum of line.units"),
    _F("units_per_line",            _CAT.BASE, _SRC.DERIVED, "float", "total_units / max(line_count,1)"),
    _F("service_month",             _CAT.BASE, _SRC.CLAIM,   "int",   "1-12; 0 if service_from_date null"),
    _F("service_day_of_week",       _CAT.BASE, _SRC.CLAIM,   "int",   "0=Mon..6=Sun; 0 if null"),
    _F("weekend_service",           _CAT.BASE, _SRC.DERIVED, "bool",  "service_day_of_week>=5"),
    _F("service_duration_days",     _CAT.BASE, _SRC.CLAIM,   "int",   "(service_to-service_from).days; 0 if either null"),
)


# Category A — coverage / eligibility (10 features)
_COVERAGE = (
    _F("patient_age_at_service",        _CAT.COVERAGE, _SRC.PATIENT, "int",  "years between DOB and service_from_date; 0 if either null"),
    _F("patient_age_band",              _CAT.COVERAGE, _SRC.DERIVED, "int",  "0:<18 1:18-39 2:40-64 3:65+"),
    _F("patient_age_vs_procedure_valid",_CAT.COVERAGE, _SRC.REFERENCE_DATA, "bool", "age in procedure_codes.metadata age window; default 1",
        availability=_AVL.REQUIRES_REF_DATA, default_value=1, requires_ref_table="procedure_codes"),
    _F("patient_gender_vs_procedure_valid",_CAT.COVERAGE, _SRC.REFERENCE_DATA, "bool", "gender matches procedure_codes.metadata restriction; default 1",
        availability=_AVL.REQUIRES_REF_DATA, default_value=1, requires_ref_table="procedure_codes"),
    _F("cob_position_encoded",          _CAT.COVERAGE, _SRC.SUBSCRIBER, "int", "0:none 1:P 2:S 3:T"),
    _F("has_secondary_payer",           _CAT.COVERAGE, _SRC.SUBSCRIBER, "bool", "exists subscriber with COB S or T"),
    _F("payer_overall_denial_rate",     _CAT.COVERAGE, _SRC.MATERIALIZED_VIEW, "float", "mv_payer_denial_rates smoothed",
        leakage_risk=_RISK.MEDIUM),
    _F("payer_taxonomy_encoded",        _CAT.COVERAGE, _SRC.PAYER,   "float", "target-encoded payer.payer_taxonomy",
        leakage_risk=_RISK.MEDIUM),
)


# Category B — authorization / referral (7 features)
_AUTHORIZATION = (
    _F("has_prior_authorization",        _CAT.AUTHORIZATION, _SRC.CLAIM, "bool", "claim.authorization_number not null"),
    _F("has_referral",                   _CAT.AUTHORIZATION, _SRC.CLAIM, "bool", "claim.referral_number not null"),
    _F("auth_required_for_cpt_payer",    _CAT.AUTHORIZATION, _SRC.PAYER_POLICY, "bool", "payer_policies prior_auth lookup; default 0",
        availability=_AVL.REQUIRES_REF_DATA, default_value=0, requires_ref_table="payer_policies"),
    _F("auth_missing_when_required",     _CAT.AUTHORIZATION, _SRC.DERIVED, "bool", "auth_required AND NOT has_prior_authorization"),
    _F("referral_required_for_specialty",_CAT.AUTHORIZATION, _SRC.PAYER_POLICY, "bool", "payer_policies referral_required lookup; default 0",
        availability=_AVL.REQUIRES_REF_DATA, default_value=0, requires_ref_table="payer_policies"),
    _F("referral_missing_when_required", _CAT.AUTHORIZATION, _SRC.DERIVED, "bool", "referral_required AND NOT has_referral"),
    _F("auth_number_format_valid",       _CAT.AUTHORIZATION, _SRC.PAYER_POLICY, "bool", "regex match vs payer's auth format; default 1",
        availability=_AVL.REQUIRES_REF_DATA, default_value=1, requires_ref_table="payer_policies"),
)


# Category C — medical necessity / clinical alignment (8 features)
_CLINICAL = (
    _F("cpt_dx_alignment_score",        _CAT.CLINICAL, _SRC.MATERIALIZED_VIEW, "float", "mv_cpt_dx_denial_rate (inverted) for primary CPT × DX",
        leakage_risk=_RISK.MEDIUM),
    _F("has_unspecified_diagnosis",     _CAT.CLINICAL, _SRC.CLAIM, "bool", "primary dx ends in .9 OR starts with Z"),
    _F("primary_dx_chapter_encoded",    _CAT.CLINICAL, _SRC.REFERENCE_DATA, "float", "target-encoded diagnosis_codes.chapter; default 0",
        availability=_AVL.REQUIRES_REF_DATA, leakage_risk=_RISK.MEDIUM, requires_ref_table="diagnosis_codes"),
    _F("dx_severity_score",             _CAT.CLINICAL, _SRC.REFERENCE_DATA, "float", "diagnosis_codes.metadata->>'severity_score'; default 0",
        availability=_AVL.REQUIRES_REF_DATA, requires_ref_table="diagnosis_codes"),
    _F("acute_vs_chronic_indicator",    _CAT.CLINICAL, _SRC.DERIVED, "int", "0:unknown 1:acute 2:chronic 3:mixed"),
    _F("cpt_category_encoded",          _CAT.CLINICAL, _SRC.REFERENCE_DATA, "float", "target-encoded procedure_codes.category; default 0",
        availability=_AVL.REQUIRES_REF_DATA, leakage_risk=_RISK.MEDIUM, requires_ref_table="procedure_codes"),
    _F("is_high_complexity_em",         _CAT.CLINICAL, _SRC.DERIVED, "bool", "primary CPT in {99204,99205,99214,99215,99244,99245}"),
    _F("principal_dx_supports_procedure",_CAT.CLINICAL, _SRC.REFERENCE_DATA, "bool", "cms_lcd_coverage CPT×DX lookup; default 1",
        availability=_AVL.REQUIRES_REF_DATA, default_value=1, requires_ref_table="cms_lcd_coverage"),
)


# Category D — coding integrity (10 features)
_CODING = (
    _F("has_modifier",                  _CAT.CODING, _SRC.CLAIM, "bool", "any modifier1-4 present on any line"),
    _F("modifier_count_total",          _CAT.CODING, _SRC.CLAIM, "int", "count non-null modifiers across lines"),
    _F("has_required_modifier_for_cpt", _CAT.CODING, _SRC.REFERENCE_DATA, "bool", "procedure_codes.metadata->>'requires_modifier'; default 0",
        availability=_AVL.REQUIRES_REF_DATA, requires_ref_table="procedure_codes"),
    _F("required_modifier_present",     _CAT.CODING, _SRC.DERIVED, "bool", "if not required default 1 else check the specific modifier is present",
        availability=_AVL.REQUIRES_REF_DATA, default_value=1, requires_ref_table="procedure_codes"),
    _F("is_likely_unbundled",           _CAT.CODING, _SRC.REFERENCE_DATA, "bool", "any pair in ncci_edits PTP w/o override modifier; default 0",
        availability=_AVL.REQUIRES_REF_DATA, requires_ref_table="ncci_edits"),
    _F("cpt_pos_alignment_score",       _CAT.CODING, _SRC.REFERENCE_DATA, "bool", "POS in procedure_codes.metadata.valid_pos_codes; default 1",
        availability=_AVL.REQUIRES_REF_DATA, default_value=1, requires_ref_table="procedure_codes"),
    _F("frequency_code_encoded",        _CAT.CODING, _SRC.PAYER, "float", "target-encoded frequency_code (1/6/7/8)",
        leakage_risk=_RISK.MEDIUM),
    _F("is_replacement_claim",          _CAT.CODING, _SRC.CLAIM, "bool", "frequency_code in (6,7)"),
    _F("cpt_frequency_for_patient_ytd", _CAT.CODING, _SRC.MATERIALIZED_VIEW, "int", "count of same CPT for patient YTD (strict-<)",
        availability=_AVL.REQUIRES_HISTORY),
    _F("cpt_frequency_exceeds_limit",   _CAT.CODING, _SRC.DERIVED, "bool", "cpt_frequency_for_patient_ytd > procedure_codes.metadata.annual_limit; default 0",
        availability=_AVL.REQUIRES_REF_DATA, requires_ref_table="procedure_codes"),
)


# Category E — timely filing (5 features)
_TIMELY = (
    _F("service_to_submission_days",   _CAT.TIMELY, _SRC.CLAIM, "int", "(submission_date - service_from_date).days clipped >=0"),
    _F("payer_timely_filing_days",     _CAT.TIMELY, _SRC.PAYER_POLICY, "int", "payer_policies timely_filing or 365 default",
        availability=_AVL.REQUIRES_REF_DATA, default_value=365, requires_ref_table="payer_policies"),
    _F("timely_filing_proximity_ratio",_CAT.TIMELY, _SRC.DERIVED, "float", "service_to_submission_days / payer_timely_filing_days"),
    _F("is_past_timely_filing",        _CAT.TIMELY, _SRC.DERIVED, "bool", "timely_filing_proximity_ratio >= 1.0"),
    _F("is_near_timely_filing",        _CAT.TIMELY, _SRC.DERIVED, "bool", "0.85 <= ratio < 1.0"),
)


# Category F — documentation (5 features)
_DOCUMENTATION = (
    _F("has_paperwork_attachment",        _CAT.DOCUMENTATION, _SRC.CLAIM, "bool", "any PWK segment on claim"),
    _F("paperwork_required_for_cpt",      _CAT.DOCUMENTATION, _SRC.REFERENCE_DATA, "bool", "procedure_codes.metadata.requires_pwk; default 0",
        availability=_AVL.REQUIRES_REF_DATA, requires_ref_table="procedure_codes"),
    _F("paperwork_missing_when_required", _CAT.DOCUMENTATION, _SRC.DERIVED, "bool", "paperwork_required AND NOT has_paperwork_attachment"),
    _F("has_certification_segment",       _CAT.DOCUMENTATION, _SRC.CLAIM, "bool", "any claim_certifications row"),
    _F("has_notes",                       _CAT.DOCUMENTATION, _SRC.CLAIM, "bool", "any NTE segment captured into variant_data"),
)


# Category G — patient history (10 features; SQL window over mv_patient_claim_history)
_HISTORY = (
    _F("claims_in_last_30d",              _CAT.PATIENT_HISTORY, _SRC.MATERIALIZED_VIEW, "int", "count per patient, 30d window, strict-<",
        availability=_AVL.REQUIRES_HISTORY),
    _F("claims_in_last_90d",              _CAT.PATIENT_HISTORY, _SRC.MATERIALIZED_VIEW, "int", "count per patient, 90d window",
        availability=_AVL.REQUIRES_HISTORY),
    _F("claims_in_last_365d",             _CAT.PATIENT_HISTORY, _SRC.MATERIALIZED_VIEW, "int", "count per patient, 365d window",
        availability=_AVL.REQUIRES_HISTORY),
    _F("prior_denials_with_payer",        _CAT.PATIENT_HISTORY, _SRC.MATERIALIZED_VIEW, "int", "denied claims for patient × payer, 365d window",
        availability=_AVL.REQUIRES_HISTORY, leakage_risk=_RISK.MEDIUM),
    _F("prior_denials_with_payer_and_cpt",_CAT.PATIENT_HISTORY, _SRC.MATERIALIZED_VIEW, "int", "same, filtered by primary CPT match",
        availability=_AVL.REQUIRES_HISTORY, leakage_risk=_RISK.MEDIUM),
    _F("prior_paid_with_payer",           _CAT.PATIENT_HISTORY, _SRC.MATERIALIZED_VIEW, "int", "paid claims, patient × payer, 365d",
        availability=_AVL.REQUIRES_HISTORY, leakage_risk=_RISK.MEDIUM),
    _F("days_since_last_claim",           _CAT.PATIENT_HISTORY, _SRC.MATERIALIZED_VIEW, "int", "days since prior claim for patient; 0 if none",
        availability=_AVL.REQUIRES_HISTORY),
    _F("is_new_patient_to_provider",      _CAT.PATIENT_HISTORY, _SRC.MATERIALIZED_VIEW, "bool", "no prior claim from this provider for patient",
        availability=_AVL.REQUIRES_HISTORY),
    _F("annual_charges_for_patient",      _CAT.PATIENT_HISTORY, _SRC.MATERIALIZED_VIEW, "float", "running sum of charges this calendar year (strict-<)",
        availability=_AVL.REQUIRES_HISTORY),
    _F("same_day_visits_for_patient",     _CAT.PATIENT_HISTORY, _SRC.MATERIALIZED_VIEW, "int", "count of patient claims with same service_from_date minus self",
        availability=_AVL.REQUIRES_HISTORY),
)


# Category H — provider profile (10 features)
_PROVIDER = (
    _F("billing_provider_npi_encoded",       _CAT.PROVIDER, _SRC.PROVIDER, "float", "target-encoded billing NPI",
        leakage_risk=_RISK.MEDIUM),
    _F("rendering_provider_npi_encoded",     _CAT.PROVIDER, _SRC.PROVIDER, "float", "target-encoded rendering NPI",
        leakage_risk=_RISK.MEDIUM),
    _F("referring_provider_present",         _CAT.PROVIDER, _SRC.CLAIM, "bool", "referring_provider_id not null"),
    _F("provider_specialty_taxonomy_encoded",_CAT.PROVIDER, _SRC.PROVIDER, "float", "target-encoded billing provider taxonomy_code",
        leakage_risk=_RISK.MEDIUM),
    _F("provider_overall_denial_rate",       _CAT.PROVIDER, _SRC.MATERIALIZED_VIEW, "float", "mv_provider_denial_profiles",
        leakage_risk=_RISK.MEDIUM),
    _F("provider_payer_denial_rate",         _CAT.PROVIDER, _SRC.MATERIALIZED_VIEW, "float", "mv_provider_payer_denial_rate",
        leakage_risk=_RISK.MEDIUM),
    _F("provider_cpt_denial_rate",           _CAT.PROVIDER, _SRC.MATERIALIZED_VIEW, "float", "mv_provider_cpt_denial_rate",
        leakage_risk=_RISK.MEDIUM),
    _F("provider_volume_band",               _CAT.PROVIDER, _SRC.MATERIALIZED_VIEW, "int", "0:<100/yr 1:100-10k 2:>10k"),
    _F("provider_specialty_matches_cpt",     _CAT.PROVIDER, _SRC.REFERENCE_DATA, "bool", "taxonomy matches CPT category; default 1",
        availability=_AVL.REQUIRES_REF_DATA, default_value=1, requires_ref_table="procedure_codes"),
)


# Category I — joint encoders (6 features) — all from MVs
_JOINT = (
    _F("payer_cpt_denial_rate",        _CAT.JOINT, _SRC.MATERIALIZED_VIEW, "float", "mv_payer_cpt_denial_rate",
        leakage_risk=_RISK.MEDIUM),
    _F("payer_dx_denial_rate",         _CAT.JOINT, _SRC.MATERIALIZED_VIEW, "float", "mv_payer_dx_denial_rate",
        leakage_risk=_RISK.MEDIUM),
    _F("payer_pos_denial_rate",        _CAT.JOINT, _SRC.MATERIALIZED_VIEW, "float", "mv_payer_pos_denial_rate",
        leakage_risk=_RISK.MEDIUM),
    _F("cpt_dx_denial_rate",           _CAT.JOINT, _SRC.MATERIALIZED_VIEW, "float", "mv_cpt_dx_denial_rate (also feeds clinical alignment)",
        leakage_risk=_RISK.MEDIUM),
    _F("payer_provider_denial_rate",   _CAT.JOINT, _SRC.MATERIALIZED_VIEW, "float", "mv_provider_payer_denial_rate (dup name preserved for FE clarity)",
        leakage_risk=_RISK.MEDIUM),
    _F("provider_cpt_denial_rate_joint",_CAT.JOINT, _SRC.MATERIALIZED_VIEW, "float", "joint of mv_provider_cpt_denial_rate (suffix to avoid dup with Cat H)",
        leakage_risk=_RISK.MEDIUM),
)


# Category K — target-encoded categoricals (5 features)
_ENCODED = (
    _F("payer_name_encoded",         _CAT.ENCODED_CATEGORICAL, _SRC.PAYER, "float", "target-encoded payer canonical_name",
        leakage_risk=_RISK.MEDIUM),
    _F("primary_cpt_encoded",        _CAT.ENCODED_CATEGORICAL, _SRC.CLAIM, "float", "target-encoded first claim_line.procedure_code",
        leakage_risk=_RISK.MEDIUM),
    _F("primary_dx_encoded",         _CAT.ENCODED_CATEGORICAL, _SRC.CLAIM, "float", "target-encoded diagnoses (first by sequence)",
        leakage_risk=_RISK.MEDIUM),
    _F("place_of_service_encoded",   _CAT.ENCODED_CATEGORICAL, _SRC.CLAIM, "float", "target-encoded first line.place_of_service",
        leakage_risk=_RISK.MEDIUM),
    _F("facility_type_code_encoded", _CAT.ENCODED_CATEGORICAL, _SRC.CLAIM, "float", "target-encoded claims.facility_type_code",
        leakage_risk=_RISK.MEDIUM),
)


# Category L — rarity / unseen / missing (14 features)
_RARITY = (
    _F("is_rare_payer",              _CAT.RARITY, _SRC.DERIVED, "bool", "payer_volume in (0, threshold)"),
    _F("is_rare_dx",                 _CAT.RARITY, _SRC.DERIVED, "bool", "dx_volume in (0, threshold)"),
    _F("unseen_payer",               _CAT.RARITY, _SRC.DERIVED, "bool", "value NOT in training vocabulary"),
    _F("unseen_cpt",                 _CAT.RARITY, _SRC.DERIVED, "bool", "value NOT in training vocabulary"),
    _F("unseen_dx",                  _CAT.RARITY, _SRC.DERIVED, "bool", "value NOT in training vocabulary"),
    _F("unseen_rendering_provider",  _CAT.RARITY, _SRC.DERIVED, "bool", "value NOT in training vocabulary"),
    _F("unseen_any",                 _CAT.RARITY, _SRC.DERIVED, "bool", "OR of unseen_payer/cpt/dx/rendering_provider"),
    _F("missing_payer",              _CAT.RARITY, _SRC.DERIVED, "bool", "payer is null/blank"),
    _F("missing_diagnosis",          _CAT.RARITY, _SRC.DERIVED, "bool", "no diagnoses present"),
    _F("missing_procedure",          _CAT.RARITY, _SRC.DERIVED, "bool", "no procedure_code on any line"),
    _F("missing_pos",                _CAT.RARITY, _SRC.DERIVED, "bool", "place_of_service null"),
    _F("missing_count",              _CAT.RARITY, _SRC.DERIVED, "int",  "sum of all missing_* flags"),
)


# Category Z — availability / completeness (5 features)
_AVAILABILITY = (
    _F("avail_procedure_codes_metadata", _CAT.AVAILABILITY, _SRC.REFERENCE_DATA, "bool", "1 if procedure_codes.metadata for primary CPT was populated",
        availability=_AVL.REQUIRES_REF_DATA, requires_ref_table="procedure_codes"),
    _F("avail_payer_policies",           _CAT.AVAILABILITY, _SRC.REFERENCE_DATA, "bool", "1 if any payer_policies row exists for the payer",
        availability=_AVL.REQUIRES_REF_DATA, requires_ref_table="payer_policies"),
    _F("avail_ncci_edits",               _CAT.AVAILABILITY, _SRC.REFERENCE_DATA, "bool", "1 if ncci_edits has any rows",
        availability=_AVL.REQUIRES_REF_DATA, requires_ref_table="ncci_edits"),
    _F("avail_lcd_coverage",             _CAT.AVAILABILITY, _SRC.REFERENCE_DATA, "bool", "1 if cms_lcd_coverage has any rows",
        availability=_AVL.REQUIRES_REF_DATA, requires_ref_table="cms_lcd_coverage"),
    _F("reference_data_completeness",    _CAT.AVAILABILITY, _SRC.DERIVED, "float", "mean of avail_* flags; 0..1",
        availability=_AVL.REQUIRES_REF_DATA),
)


# Category M — variant-specific blocks. One tuple per (variant, claim_subtype).

# 837P / healthcare (4 features; CR-104 retired surgery_global_period_active + cob_indicator)
_HEALTHCARE_VARIANT = (
    _F("e_and_m_level",               _CAT.VARIANT, _SRC.CLAIM, "int", "1-5 from E&M CPT 99201-99499 suffix; 0 if not E&M"),
    _F("is_telehealth",               _CAT.VARIANT, _SRC.CLAIM, "bool", "POS in {02,10} OR modifier in {95,GT,G0}"),
    _F("is_preventive_visit",         _CAT.VARIANT, _SRC.CLAIM, "bool", "CPT in 99381-99397"),
    _F("is_consultation",             _CAT.VARIANT, _SRC.CLAIM, "bool", "CPT in 99241-99245 or 99251-99255"),
)

# 837P / therapy (9 features)
_THERAPY_VARIANT = (
    _F("discipline_modifier_encoded", _CAT.VARIANT, _SRC.CLAIM, "float", "target-encoded discipline modifier {GP,GO,GN,KH}; placeholder int mapping when encoder not yet fit",
        leakage_risk=_RISK.MEDIUM),
    _F("kx_modifier_present",         _CAT.VARIANT, _SRC.CLAIM, "bool", "modifier KX present (threshold exception attestation)"),
    _F("cap_proximity",               _CAT.VARIANT, _SRC.MATERIALIZED_VIEW, "float", "YTD therapy charges / Medicare cap (2330); clipped to 5.0",
        availability=_AVL.REQUIRES_HISTORY),
    _F("plan_of_care_present",        _CAT.VARIANT, _SRC.CLAIM, "bool", "PWK with type CT or PN present"),
    _F("plan_of_care_recent",         _CAT.VARIANT, _SRC.CLAIM, "bool", "DTP*090 plan-of-care date within 30 days of service_from_date"),
    _F("kh_modifier",                 _CAT.VARIANT, _SRC.CLAIM, "bool", "modifier KH present (maintenance therapy)"),
    _F("evaluation_vs_treatment",     _CAT.VARIANT, _SRC.CLAIM, "bool", "primary CPT in PT/OT/ST evaluation code set"),
    _F("is_maintenance_therapy",      _CAT.VARIANT, _SRC.DERIVED, "bool", "alias of kh_modifier for variant-aware modeling"),
    _F("therapy_sessions_ytd",        _CAT.VARIANT, _SRC.MATERIALIZED_VIEW, "int", "proxy: patient's claims_in_last_365d when therapy-specific MV unavailable",
        availability=_AVL.REQUIRES_HISTORY),
)

# 837P / transport (8 features)
_TRANSPORT_VARIANT = (
    _F("ambulance_cert_present",      _CAT.VARIANT, _SRC.CLAIM, "bool", "transport_certifications row exists for claim"),
    _F("transport_miles",             _CAT.VARIANT, _SRC.CLAIM, "float", "CR1*06 transport distance in miles"),
    _F("patient_weight_lbs",          _CAT.VARIANT, _SRC.CLAIM, "int", "CR1*02 patient weight in pounds"),
    _F("transport_reason_code_encoded",_CAT.VARIANT, _SRC.CLAIM, "float", "target-encoded CR1*04 reason code {A,B,C,D,E}; placeholder int mapping when encoder not fit",
        leakage_risk=_RISK.MEDIUM),
    _F("round_trip_indicator",        _CAT.VARIANT, _SRC.CLAIM, "bool", "round trip flag from transport_certifications"),
    _F("emergent_indicator",          _CAT.VARIANT, _SRC.CLAIM, "bool", "CR1*03 emergent code in {E, EM}"),
    _F("origin_dest_specified",       _CAT.VARIANT, _SRC.CLAIM, "bool", "both origin_address and destination_address present"),
    _F("los_modifier_encoded",        _CAT.VARIANT, _SRC.CLAIM, "float", "target-encoded level-of-service modifier {ALS, BLS, SCT}; placeholder int mapping",
        leakage_risk=_RISK.MEDIUM),
)

# 837I / home_care (11 features)
_HOME_CARE_VARIANT = (
    _F("hipps_code_encoded",          _CAT.VARIANT, _SRC.CLAIM, "float", "target-encoded HIPPS payment-grouper code from claim_lines",
        leakage_risk=_RISK.MEDIUM),
    _F("episode_length_days",         _CAT.VARIANT, _SRC.CLAIM, "int", "home_care_episodes.episode_end_date - episode_start_date"),
    _F("revenue_code_count_skilled",  _CAT.VARIANT, _SRC.CLAIM, "int", "count of lines with revenue_code in {0551,0552,0571,0572}"),
    _F("visit_count_in_episode",      _CAT.VARIANT, _SRC.CLAIM, "int", "home_care_episodes.visit_count"),
    _F("homebound_certification_present",_CAT.VARIANT, _SRC.CLAIM, "bool", "CRC*75 homebound certification captured"),
    _F("oasis_within_5_days",         _CAT.VARIANT, _SRC.CLAIM, "bool", "OASIS assessment within 5 days of episode start"),
    _F("face_to_face_encounter_present",_CAT.VARIANT, _SRC.CLAIM, "bool", "REF*9F captured (F2F encounter documented)"),
    _F("physician_certification_present",_CAT.VARIANT, _SRC.CLAIM, "bool", "REF*EW OR plan_of_care_signed_date present"),
    _F("is_lupa",                     _CAT.VARIANT, _SRC.DERIVED, "bool", "visit_count > 0 AND visit_count < 5 (Low Utilization Payment Adjustment)"),
    _F("discipline_count_total",      _CAT.VARIANT, _SRC.CLAIM, "int", "sum of PT/OT/ST/SN/HHA visit counts from discipline_mix JSONB"),
    _F("is_recertification_episode",  _CAT.VARIANT, _SRC.MATERIALIZED_VIEW, "bool", "patient has a prior claim with same provider (proxy)",
        availability=_AVL.REQUIRES_HISTORY),
)

# 837I / institutional_other (5 features) — covers inpatient + hospice + other
_INSTITUTIONAL_OTHER_VARIANT = (
    _F("inpatient_revenue_code_present",_CAT.VARIANT, _SRC.CLAIM, "bool", "any revenue_code in 0100-0219 (room/board)"),
    _F("hospice_revenue_code_present",_CAT.VARIANT, _SRC.CLAIM, "bool", "any revenue_code in 0820-0859"),
    _F("drg_assigned",                _CAT.VARIANT, _SRC.CLAIM, "bool", "DRG code or amount captured into variant_data (from MIA)"),
    _F("admission_date_present",      _CAT.VARIANT, _SRC.CLAIM, "bool", "DTP*435 admission date captured"),
    _F("statement_period_days",       _CAT.VARIANT, _SRC.CLAIM, "int", "DTP*434 statement period length in days"),
)

# 837D / dental (8 features)
_DENTAL_VARIANT = (
    _F("tooth_number_specified",      _CAT.VARIANT, _SRC.CLAIM, "bool", "any claim_lines.tooth_number not null"),
    _F("tooth_surface_count",         _CAT.VARIANT, _SRC.CLAIM, "float", "count of distinct tooth_numbers as surface-count proxy"),
    _F("cdt_category_encoded",        _CAT.VARIANT, _SRC.CLAIM, "float", "target-encoded first digit of primary CDT (D0=diag, D1=prev, D2=rest, …); placeholder int mapping",
        leakage_risk=_RISK.MEDIUM),
    _F("predetermination_filed",      _CAT.VARIANT, _SRC.CLAIM, "bool", "REF*F8 predetermination reference present"),
    _F("orthodontia_indicator",       _CAT.VARIANT, _SRC.CLAIM, "bool", "DN1 orthodontic segment captured into certifications"),
    _F("is_preventive_service",       _CAT.VARIANT, _SRC.CLAIM, "bool", "primary CDT in D0xxx-D1xxx range"),
    _F("service_age_in_months_for_tooth",_CAT.VARIANT, _SRC.MATERIALIZED_VIEW, "int", "proxy: days_since_last_claim / 30 (will be tooth-specific in Phase 4)",
        availability=_AVL.REQUIRES_HISTORY),
    _F("radiograph_within_year",      _CAT.VARIANT, _SRC.CLAIM, "bool", "primary CDT in D0210-D0274 (radiograph) range; needs dedicated patient-radiograph MV for full accuracy"),
)

# 837P / specialty (10 features) — unified across oncology / DME / behavioral / lab-rad sub-specialties
_SPECIALTY_VARIANT = (
    _F("ndc_drug_present",            _CAT.VARIANT, _SRC.CLAIM, "bool", "any claim_lines.ndc_drug_code not null (oncology / pharmacy)"),
    _F("j_code_count",                _CAT.VARIANT, _SRC.CLAIM, "int", "count of procedure_codes starting with 'J' (oncology drugs)"),
    _F("is_high_cost_drug",           _CAT.VARIANT, _SRC.DERIVED, "bool", "total_charge > 5000 AND j_code_count > 0"),
    _F("cr3_certification_present",   _CAT.VARIANT, _SRC.CLAIM, "bool", "CR3 DME certification captured into claim_certifications"),
    _F("is_rental",                   _CAT.VARIANT, _SRC.CLAIM, "bool", "modifier RR present (DME rental)"),
    _F("is_purchase",                 _CAT.VARIANT, _SRC.CLAIM, "bool", "modifier NU present (DME new purchase)"),
    _F("is_h_code",                   _CAT.VARIANT, _SRC.CLAIM, "bool", "primary CPT starts with 'H' (behavioral health)"),
    _F("is_initial_assessment",       _CAT.VARIANT, _SRC.CLAIM, "bool", "primary CPT in {90791, 90792} (BH initial assessment)"),
    _F("is_group_therapy",            _CAT.VARIANT, _SRC.CLAIM, "bool", "primary CPT in {90849, 90853} (BH group therapy)"),
    _F("clia_number_present",         _CAT.VARIANT, _SRC.CLAIM, "bool", "REF*X4 CLIA number captured (lab)"),
)


# ---------------------------------------------------------------------------
# Assemble FEATURE_REGISTRY (name → spec)
# ---------------------------------------------------------------------------

_ALL_SPECS: tuple[FeatureSpec, ...] = (
    *_BASE,
    *_COVERAGE,
    *_AUTHORIZATION,
    *_CLINICAL,
    *_CODING,
    *_TIMELY,
    *_DOCUMENTATION,
    *_HISTORY,
    *_PROVIDER,
    *_JOINT,
    *_ENCODED,
    *_RARITY,
    *_AVAILABILITY,
    *_HEALTHCARE_VARIANT,
    *_THERAPY_VARIANT,
    *_TRANSPORT_VARIANT,
    *_HOME_CARE_VARIANT,
    *_INSTITUTIONAL_OTHER_VARIANT,
    *_DENTAL_VARIANT,
    *_SPECIALTY_VARIANT,
)

# Multiple variant blocks can share the same feature names (e.g. cob_indicator
# only lives in healthcare today, but if therapy adopts it later both should
# resolve to the same FeatureSpec). Dedup by name, last-write-wins; in practice
# variant feature names are disjoint by construction.
FEATURE_REGISTRY: dict[str, FeatureSpec] = {}
for _s in _ALL_SPECS:
    FEATURE_REGISTRY[_s.name] = _s


# ---------------------------------------------------------------------------
# Canonical column lists per variant
# Order matters — XGBoost SHAP mis-attributes silently if column order drifts.
# ---------------------------------------------------------------------------

# Universal columns shared by every variant (categories A–L + availability)
_UNIVERSAL_COLUMNS: tuple[str, ...] = tuple(s.name for s in (
    *_BASE,
    *_COVERAGE,
    *_AUTHORIZATION,
    *_CLINICAL,
    *_CODING,
    *_TIMELY,
    *_DOCUMENTATION,
    *_HISTORY,
    *_PROVIDER,
    *_JOINT,
    *_ENCODED,
    *_RARITY,
    *_AVAILABILITY,
))

FEATURE_COLUMNS_HEALTHCARE: tuple[str, ...] = _UNIVERSAL_COLUMNS + tuple(s.name for s in _HEALTHCARE_VARIANT)
FEATURE_COLUMNS_THERAPY: tuple[str, ...] = _UNIVERSAL_COLUMNS + tuple(s.name for s in _THERAPY_VARIANT)
FEATURE_COLUMNS_TRANSPORT: tuple[str, ...] = _UNIVERSAL_COLUMNS + tuple(s.name for s in _TRANSPORT_VARIANT)
FEATURE_COLUMNS_HOME_CARE: tuple[str, ...] = _UNIVERSAL_COLUMNS + tuple(s.name for s in _HOME_CARE_VARIANT)
FEATURE_COLUMNS_INSTITUTIONAL_OTHER: tuple[str, ...] = _UNIVERSAL_COLUMNS + tuple(s.name for s in _INSTITUTIONAL_OTHER_VARIANT)
FEATURE_COLUMNS_DENTAL: tuple[str, ...] = _UNIVERSAL_COLUMNS + tuple(s.name for s in _DENTAL_VARIANT)
FEATURE_COLUMNS_SPECIALTY: tuple[str, ...] = _UNIVERSAL_COLUMNS + tuple(s.name for s in _SPECIALTY_VARIANT)
# Global fallback: universal columns only — used when (variant, subtype) is unknown
FEATURE_COLUMNS_GLOBAL: tuple[str, ...] = _UNIVERSAL_COLUMNS


# Registered (variant, subtype) → ordered column list. Inpatient and hospice
# subtypes route to the SAME institutional_other column list per spec §2.2
# routing rules (the 837I dispatcher folds them in).
_VARIANT_COLUMNS: dict[tuple[str, str], tuple[str, ...]] = {
    ("837P", "healthcare"):           FEATURE_COLUMNS_HEALTHCARE,
    ("837P", "therapy"):              FEATURE_COLUMNS_THERAPY,
    ("837P", "transport"):            FEATURE_COLUMNS_TRANSPORT,
    ("837P", "specialty"):            FEATURE_COLUMNS_SPECIALTY,
    ("837I", "home_care"):            FEATURE_COLUMNS_HOME_CARE,
    ("837I", "institutional_other"):  FEATURE_COLUMNS_INSTITUTIONAL_OTHER,
    ("837I", "inpatient"):            FEATURE_COLUMNS_INSTITUTIONAL_OTHER,   # alias
    ("837I", "hospice"):              FEATURE_COLUMNS_INSTITUTIONAL_OTHER,   # alias
    ("837I", "specialty"):            FEATURE_COLUMNS_INSTITUTIONAL_OTHER,   # 837I/specialty per parsing/routing.py
    ("837D", "dental"):               FEATURE_COLUMNS_DENTAL,
    ("_global", "_global"):           FEATURE_COLUMNS_GLOBAL,
}


def get_feature_columns(
    service_variant: str,
    claim_subtype: str,
    *,
    fall_back_to_global: bool = False,
) -> tuple[str, ...]:
    """Return the canonical ordered feature column list for (variant, subtype).

    If `fall_back_to_global=True`, unknown (variant, subtype) tuples resolve
    to FEATURE_COLUMNS_GLOBAL (universal-only). Otherwise raises KeyError.
    The builder always passes `fall_back_to_global=True` so the system can
    score ANY claim, even a variant that isn't yet specialized.
    """
    key = (service_variant, claim_subtype)
    if key in _VARIANT_COLUMNS:
        return _VARIANT_COLUMNS[key]
    if fall_back_to_global:
        return FEATURE_COLUMNS_GLOBAL
    raise KeyError(
        f"No FEATURE_COLUMNS registered for ({service_variant!r}, {claim_subtype!r}). "
        f"Known: {sorted(_VARIANT_COLUMNS)}. "
        f"Pass fall_back_to_global=True to use the universal-only column list."
    )


def is_registered_variant(service_variant: str, claim_subtype: str) -> bool:
    return (service_variant, claim_subtype) in _VARIANT_COLUMNS


def universal_columns() -> tuple[str, ...]:
    """Columns shared by every variant. Useful for the global fallback model."""
    return _UNIVERSAL_COLUMNS


def registered_variants() -> tuple[tuple[str, str], ...]:
    """Every (variant, subtype) tuple registered. Useful for tests and the
    dev console's model registry page."""
    return tuple(sorted(_VARIANT_COLUMNS.keys()))


# ---------------------------------------------------------------------------
# M1 strict validation
# ---------------------------------------------------------------------------

class FeatureSchemaError(ValueError):
    """Raised when a DataFrame doesn't match its variant's canonical column
    list at predict time. XGBoost would otherwise mis-attribute SHAP."""


def validate_feature_frame(
    df,
    service_variant: str,
    claim_subtype: str,
    *,
    allow_extra: bool = False,
    fall_back_to_global: bool = False,
) -> None:
    """Strict check that ``list(df.columns) == FEATURE_COLUMNS_<variant>``.

    Pass ``allow_extra=True`` only in dev. Production predict path must
    receive an exact match in the exact order.

    When `fall_back_to_global=True`, unknown variants are validated against
    FEATURE_COLUMNS_GLOBAL (the universal-only column list).
    """
    expected = list(get_feature_columns(
        service_variant, claim_subtype, fall_back_to_global=fall_back_to_global,
    ))
    actual = list(df.columns)

    missing = [c for c in expected if c not in actual]
    if missing:
        raise FeatureSchemaError(
            f"Feature frame missing required columns for ({service_variant},{claim_subtype}): {missing}"
        )

    extra = [c for c in actual if c not in expected]
    if extra and not allow_extra:
        raise FeatureSchemaError(
            f"Feature frame has unexpected extra columns: {extra}"
        )

    if not allow_extra and actual != expected:
        # Same set but different order — this is the silent SHAP bug
        raise FeatureSchemaError(
            f"Feature frame column order does not match registry.\n"
            f"Expected first 5: {expected[:5]}\n"
            f"Actual first 5:   {actual[:5]}"
        )


def feature_count(service_variant: str, claim_subtype: str) -> int:
    return len(get_feature_columns(service_variant, claim_subtype))


def feature_count_by_variant() -> dict[tuple[str, str], int]:
    """Map (variant, subtype) → feature count, for the model-registry page."""
    return {key: len(cols) for key, cols in _VARIANT_COLUMNS.items()}


def specs_by_category(category: FeatureCategory) -> list[FeatureSpec]:
    return [s for s in FEATURE_REGISTRY.values() if s.category == category]
