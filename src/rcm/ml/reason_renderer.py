"""Business-friendly mapping from FeatureBuilder feature names → user-facing
denial-reason text. CR-078 / CR-078A.

After the FeatureBuilder cutover (CR-067), `/predict-file` and `/predict-claim`
surfaced raw feature names like ``"FB feature contribution: auth_missing_when_required"``.
CR-078 restored claim-focused, review-oriented explanations. CR-078A raises
their usefulness by switching to slightly more specific, context-oriented
wording AND allowing per-variant overrides so the message reads naturally
for healthcare / dental / home_care / therapy / transport / specialty /
institutional claims.

The renderer is still intentionally vocabulary-poor (11 default sentences +
a small variant-override table). The UI never leaks SHAP / encoder / target-
encoding concepts. Many FB features collapse to the same sentence by design —
e.g. ``has_prior_authorization``, ``auth_missing_when_required`` and
``referral_required_for_specialty`` all land on the Authorization sentence.

Public surface:

    render_reason(feature_name, claim_subtype=None) -> (slug, title, sentence)
        Returns the bucket triple for one FB feature. ``claim_subtype`` lets
        the renderer pick a variant-specific override for buckets that have
        one (currently Documentation; others fall back to the default).
        Unknown features collapse to REASON_GENERAL so a raw FB name can
        never leak.

    render_risk_factors(factors, top_k=5, claim_subtype=None) -> list[dict]
        Convert a list of RiskFactor objects (or dicts) into the deduplicated
        list of business-language rows the API returns under
        ``top_denial_reasons``. Filters to positive-impact factors only.
"""

from __future__ import annotations

from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Canonical default sentences — ANY change here is user-facing.
# Variant-aware overrides live in _VARIANT_OVERRIDES below.
# ---------------------------------------------------------------------------

REASON_AUTHORIZATION = "Authorization requirements may not be fully satisfied for this claim."
REASON_COVERAGE      = "Coverage eligibility or member information should be reviewed."
REASON_PROCEDURE     = "The billed procedure information may increase denial risk."
REASON_DIAGNOSIS     = "Diagnosis details may require additional review before submission."
REASON_TIMELY        = "Submission timing should be reviewed against payer filing deadlines."
REASON_DOCUMENTATION = "Supporting documentation may be insufficient or incomplete."
REASON_HISTORY       = "Previous claim patterns indicate a higher denial risk."
REASON_PROVIDER      = "Provider-related claim information should be reviewed."
REASON_BILLING       = "Billing details may require verification before submission."
REASON_SIMILAR       = "Claims with similar characteristics have historically shown higher denial risk."
REASON_GENERAL       = "This claim contains factors commonly associated with denials."


# Each bucket = (slug, short title, business sentence). The slug + title are
# safe to send to the UI; the sentence is the displayed text. The user-facing
# surface NEVER carries the raw FB feature name.
_AUTH   = ("authorization", "Authorization",  REASON_AUTHORIZATION)
_COV    = ("coverage",      "Coverage",       REASON_COVERAGE)
_PROC   = ("procedure",     "Procedure",      REASON_PROCEDURE)
_DX     = ("diagnosis",     "Diagnosis",      REASON_DIAGNOSIS)
_TIMELY = ("timely_filing", "Timely Filing",  REASON_TIMELY)
_DOC    = ("documentation", "Documentation",  REASON_DOCUMENTATION)
_HIST   = ("history",       "Claim History",  REASON_HISTORY)
_PROV   = ("provider",      "Provider",       REASON_PROVIDER)
_BILL   = ("billing",       "Billing",        REASON_BILLING)
_SIM    = ("similar",       "Similar Claims", REASON_SIMILAR)
_GEN    = ("general",       "Claim Details",  REASON_GENERAL)


REASON_BUCKETS: tuple[tuple[str, str, str], ...] = (
    _AUTH, _COV, _PROC, _DX, _TIMELY, _DOC, _HIST, _PROV, _BILL, _SIM, _GEN,
)


# ---------------------------------------------------------------------------
# Variant-aware sentence overrides (CR-078A).
#
# Keyed on (bucket_slug, normalised_subtype). If a key is present the override
# text is used instead of the bucket's default sentence; if not, the default
# fires. Add overrides only where the variant-specific wording genuinely reads
# better than the default — restraint keeps the vocabulary small and avoids
# accidentally reintroducing model-internal phrasing.
# ---------------------------------------------------------------------------

# 837I aliases (inpatient/hospice and 837I/specialty per parsing/routing.py)
# normalise to ``institutional_other`` for override lookup, mirroring the
# feature registry's column aliasing.
_SUBTYPE_NORMALISE: dict[str, str] = {
    "inpatient":  "institutional_other",
    "hospice":    "institutional_other",
}

_VARIANT_OVERRIDES: dict[tuple[str, str], str] = {
    # --- Documentation reads quite differently per care setting ---
    ("documentation", "healthcare"):          "Clinical documentation may require review.",
    ("documentation", "dental"):              "Treatment documentation may require review.",
    ("documentation", "home_care"):           "Home health documentation requirements may need verification.",
    ("documentation", "therapy"):             "Therapy plan-of-care documentation may require review.",
    ("documentation", "transport"):           "Ambulance trip documentation may require review.",
    ("documentation", "institutional_other"): "Facility documentation may require review.",
    ("documentation", "specialty"):           "Specialty service documentation may require review.",

    # --- Procedure: dental + home_care + therapy benefit from category cues ---
    ("procedure", "dental"):    "The billed dental treatment information may increase denial risk.",
    ("procedure", "home_care"): "The billed home-health services may increase denial risk.",
    ("procedure", "therapy"):   "The billed therapy services may increase denial risk.",
    ("procedure", "transport"): "The billed transport service may increase denial risk.",

    # --- Coverage: home-health / facility claims have distinct coverage rules ---
    ("coverage", "home_care"):           "Home-health coverage eligibility or member information should be reviewed.",
    ("coverage", "institutional_other"): "Inpatient or facility coverage information should be reviewed.",
}


def _normalise_subtype(claim_subtype: str | None) -> str | None:
    if not claim_subtype:
        return None
    return _SUBTYPE_NORMALISE.get(claim_subtype, claim_subtype)


# ---------------------------------------------------------------------------
# Feature name → bucket. EVERY name in rcm.features.registry.FEATURE_REGISTRY
# must appear here; a coverage test fails the build if one slips through.
# Unknown names at runtime collapse to REASON_GENERAL — defence in depth so a
# raw name can never leak even if this dict drifts.
# ---------------------------------------------------------------------------

_FEATURE_TO_BUCKET: dict[str, tuple[str, str, str]] = {
    # --- Category J — base claim ---
    "total_charge_amount":       _BILL,
    "total_billed_amount":       _BILL,
    "charge_to_billed_ratio":    _BILL,
    "line_count":                _BILL,
    "diagnosis_count":           _DX,
    "total_units":               _BILL,
    "units_per_line":            _BILL,
    "service_month":             _BILL,
    "service_day_of_week":       _BILL,
    "weekend_service":           _BILL,
    "service_duration_days":     _BILL,
    "is_single_day_service":     _BILL,

    # --- Category A — coverage / eligibility ---
    "patient_age_at_service":             _COV,
    "patient_age_band":                   _COV,
    "patient_age_vs_procedure_valid":     _COV,
    "patient_gender_vs_procedure_valid":  _COV,
    "cob_position_encoded":               _COV,
    "is_secondary_claim":                 _COV,
    "has_secondary_payer":                _COV,
    "payer_overall_denial_rate":          _COV,
    "payer_taxonomy_encoded":             _COV,
    "cross_payer_count_for_patient":      _COV,

    # --- Category B — authorization ---
    "has_prior_authorization":          _AUTH,
    "has_referral":                     _AUTH,
    "auth_required_for_cpt_payer":      _AUTH,
    "auth_missing_when_required":       _AUTH,
    "referral_required_for_specialty":  _AUTH,
    "referral_missing_when_required":   _AUTH,
    "auth_number_format_valid":         _AUTH,

    # --- Category C — clinical / medical necessity ---
    "cpt_dx_alignment_score":           _PROC,
    "has_unspecified_diagnosis":        _DX,
    "primary_dx_chapter_encoded":       _DX,
    "dx_severity_score":                _DX,
    "acute_vs_chronic_indicator":       _DX,
    "cpt_category_encoded":             _PROC,
    "is_high_complexity_em":            _PROC,
    "principal_dx_supports_procedure":  _DX,

    # --- Category D — coding integrity ---
    "has_modifier":                     _PROC,
    "modifier_count_total":             _PROC,
    "has_required_modifier_for_cpt":    _PROC,
    "required_modifier_present":        _PROC,
    "has_invalid_modifier_combo":       _PROC,
    "is_likely_unbundled":              _PROC,
    "cpt_pos_alignment_score":          _PROC,
    "frequency_code_encoded":           _PROC,
    "is_replacement_claim":             _PROC,
    "cpt_frequency_for_patient_ytd":    _PROC,
    "cpt_frequency_exceeds_limit":      _PROC,

    # --- Category E — timely filing ---
    "service_to_submission_days":       _TIMELY,
    "payer_timely_filing_days":         _TIMELY,
    "timely_filing_proximity_ratio":    _TIMELY,
    "is_past_timely_filing":            _TIMELY,
    "is_near_timely_filing":            _TIMELY,

    # --- Category F — documentation ---
    "has_paperwork_attachment":         _DOC,
    "paperwork_required_for_cpt":       _DOC,
    "paperwork_missing_when_required":  _DOC,
    "has_certification_segment":        _DOC,
    "has_notes":                        _DOC,

    # --- Category G — patient history ---
    "claims_in_last_30d":               _HIST,
    "claims_in_last_90d":               _HIST,
    "claims_in_last_365d":              _HIST,
    "prior_denials_with_payer":         _HIST,
    "prior_denials_with_payer_and_cpt": _HIST,
    "prior_paid_with_payer":            _HIST,
    "days_since_last_claim":            _HIST,
    "is_new_patient_to_provider":       _HIST,
    "annual_charges_for_patient":       _HIST,
    "same_day_visits_for_patient":      _HIST,

    # --- Category H — provider profile ---
    "billing_provider_npi_encoded":         _PROV,
    "rendering_provider_npi_encoded":       _PROV,
    "referring_provider_present":           _PROV,
    "billing_rendering_same_npi":           _PROV,
    "provider_specialty_taxonomy_encoded":  _PROV,
    "provider_overall_denial_rate":         _PROV,
    "provider_payer_denial_rate":           _PROV,
    "provider_cpt_denial_rate":             _PROV,
    "provider_volume_band":                 _PROV,
    "provider_specialty_matches_cpt":       _PROV,

    # --- Category I — joint encoders ---
    "payer_cpt_denial_rate":           _SIM,
    "payer_dx_denial_rate":            _SIM,
    "payer_pos_denial_rate":           _SIM,
    "cpt_dx_denial_rate":              _SIM,
    "payer_provider_denial_rate":      _SIM,
    "provider_cpt_denial_rate_joint":  _SIM,

    # --- Category K — target-encoded categoricals ---
    "payer_name_encoded":          _COV,
    "primary_cpt_encoded":         _PROC,
    "primary_dx_encoded":           _DX,
    "place_of_service_encoded":    _PROC,
    "facility_type_code_encoded":  _PROC,

    # --- Category L — rarity / unseen / missing ---
    "is_rare_payer":               _COV,
    "is_rare_cpt":                 _PROC,
    "is_rare_dx":                  _DX,
    "unseen_payer":                _COV,
    "unseen_cpt":                  _PROC,
    "unseen_dx":                   _DX,
    "unseen_billing_provider":     _PROV,
    "unseen_rendering_provider":   _PROV,
    "unseen_any":                  _GEN,
    "missing_payer":               _COV,
    "missing_diagnosis":           _DX,
    "missing_procedure":           _PROC,
    "missing_pos":                 _PROC,
    "missing_count":               _GEN,

    # --- Category Z — availability ---
    "avail_procedure_codes_metadata":  _GEN,
    "avail_payer_policies":            _GEN,
    "avail_ncci_edits":                _GEN,
    "avail_lcd_coverage":              _GEN,
    "reference_data_completeness":     _GEN,

    # --- Category M — 837P / healthcare ---
    "e_and_m_level":                   _PROC,
    "is_telehealth":                   _PROC,
    "surgery_global_period_active":    _PROC,
    "is_preventive_visit":             _PROC,
    "is_consultation":                 _PROC,
    "cob_indicator":                   _COV,

    # --- Category M — 837P / therapy ---
    "discipline_modifier_encoded":     _PROC,
    "kx_modifier_present":             _PROC,
    "cap_proximity":                   _COV,
    "plan_of_care_present":            _DOC,
    "plan_of_care_recent":             _DOC,
    "kh_modifier":                     _PROC,
    "evaluation_vs_treatment":         _PROC,
    "is_maintenance_therapy":          _PROC,
    "therapy_sessions_ytd":            _HIST,

    # --- Category M — 837P / transport ---
    "ambulance_cert_present":          _DOC,
    "transport_miles":                 _PROC,
    "patient_weight_lbs":              _PROC,
    "transport_reason_code_encoded":   _PROC,
    "round_trip_indicator":            _PROC,
    "emergent_indicator":              _PROC,
    "origin_dest_specified":           _PROC,
    "los_modifier_encoded":            _PROC,

    # --- Category M — 837I / home_care ---
    "hipps_code_encoded":                  _PROC,
    "episode_length_days":                 _PROC,
    "revenue_code_count_skilled":          _PROC,
    "visit_count_in_episode":              _PROC,
    "homebound_certification_present":     _DOC,
    "oasis_within_5_days":                 _DOC,
    "face_to_face_encounter_present":      _DOC,
    "physician_certification_present":     _DOC,
    "is_lupa":                             _PROC,
    "discipline_count_total":              _PROC,
    "is_recertification_episode":          _DOC,

    # --- Category M — 837I / institutional_other ---
    "inpatient_revenue_code_present":   _PROC,
    "hospice_revenue_code_present":     _PROC,
    "drg_assigned":                     _PROC,
    "admission_date_present":           _COV,
    "statement_period_days":            _COV,

    # --- Category M — 837D / dental ---
    "tooth_number_specified":              _PROC,
    "tooth_surface_count":                 _PROC,
    "cdt_category_encoded":                _PROC,
    "predetermination_filed":              _DOC,
    "orthodontia_indicator":               _PROC,
    "is_preventive_service":               _PROC,
    "service_age_in_months_for_tooth":     _HIST,
    "radiograph_within_year":              _PROC,

    # --- Category M — 837P / specialty ---
    "ndc_drug_present":            _PROC,
    "j_code_count":                _PROC,
    "is_high_cost_drug":           _PROC,
    "cr3_certification_present":   _DOC,
    "is_rental":                   _PROC,
    "is_purchase":                 _PROC,
    "is_h_code":                   _PROC,
    "is_initial_assessment":       _PROC,
    "is_group_therapy":            _PROC,
    "clia_number_present":         _DOC,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_reason(
    feature_name: str,
    claim_subtype: str | None = None,
) -> tuple[str, str, str]:
    """Return ``(slug, title, sentence)`` for one FB feature.

    If ``claim_subtype`` is supplied and a variant-specific override is
    registered for ``(bucket_slug, normalised_subtype)``, the override
    sentence replaces the default. Unknown features collapse to
    REASON_GENERAL so a raw FB name can never surface in the UI even if
    the mapping drifts behind the registry.
    """
    slug, title, default = _FEATURE_TO_BUCKET.get(feature_name, _GEN)
    sub = _normalise_subtype(claim_subtype)
    if sub is not None:
        override = _VARIANT_OVERRIDES.get((slug, sub))
        if override is not None:
            return slug, title, override
    return slug, title, default


def render_risk_factors(
    factors: Iterable[Any] | None,
    *,
    top_k: int = 5,
    claim_subtype: str | None = None,
) -> list[dict[str, Any]]:
    """Convert SHAP risk factors → deduplicated business-language reason rows.

    ``factors`` may be a list of RiskFactor dataclass instances or dicts; each
    element is expected to expose ``feature`` (str) and ``impact`` (float).
    Negative-impact factors are "protective" (they reduce the predicted risk)
    and are therefore excluded — they are not denial reasons.

    ``claim_subtype`` (e.g. "healthcare", "dental", "home_care") is forwarded
    to ``render_reason`` so variant-aware overrides fire when registered.

    Output rows carry only the bucket slug + title + business sentence. The
    raw FB feature name never appears in the returned rows.
    """
    if not factors:
        return []

    by_slug: dict[str, dict[str, Any]] = {}
    for rf in factors:
        feature = _attr(rf, "feature")
        impact = _attr_float(rf, "impact")
        if impact <= 0:
            continue
        slug, title, sentence = render_reason(
            str(feature or ""), claim_subtype=claim_subtype,
        )
        existing = by_slug.get(slug)
        if existing is None or impact > existing["impact"]:
            by_slug[slug] = {
                "feature": slug,
                "label":   title,
                "reason":  sentence,
                "impact":  round(float(impact), 4),
                "direction": "increases denial risk",
            }

    rows = sorted(by_slug.values(), key=lambda r: r["impact"], reverse=True)
    return rows[:top_k]


def _attr(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _attr_float(obj: Any, name: str) -> float:
    v = _attr(obj, name)
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


__all__ = [
    "REASON_AUTHORIZATION",
    "REASON_COVERAGE",
    "REASON_PROCEDURE",
    "REASON_DIAGNOSIS",
    "REASON_TIMELY",
    "REASON_DOCUMENTATION",
    "REASON_HISTORY",
    "REASON_PROVIDER",
    "REASON_BILLING",
    "REASON_SIMILAR",
    "REASON_GENERAL",
    "REASON_BUCKETS",
    "render_reason",
    "render_risk_factors",
    # Exposed for tests + tooling — NOT for runtime callers (use render_reason).
    "_VARIANT_OVERRIDES",
    "_SUBTYPE_NORMALISE",
]
