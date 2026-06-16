"""CR-078 / CR-078A — explanation quality preview.

Shows what the renderer produces for synthetic SHAP profiles representative
of healthcare / dental / home_care / therapy / transport / institutional /
specialty claims. Each scenario carries the claim_subtype so variant-aware
overrides (CR-078A) fire.

Lets reviewers eyeball the wording WITHOUT spinning up the model + DB. For
real-claim review, use the `/api/predictions/predict-file/{id}` endpoint.

Usage:
    PYTHONPATH=src python scripts/cr078_quality_preview.py
"""

from __future__ import annotations

from dataclasses import dataclass

from rcm.ml.reason_renderer import render_risk_factors


@dataclass
class _RF:
    feature: str
    impact: float


# Each scenario: (label, subtype, factors).  The third element is the
# ``claim_subtype`` passed to the renderer — drives variant overrides.
SCENARIOS: list[tuple[str, str, list[_RF]]] = [
    # ----- 837P / healthcare -----
    ("HC-01 healthcare — missing auth dominant", "healthcare", [
        _RF("auth_missing_when_required",  0.42),
        _RF("auth_required_for_cpt_payer", 0.18),
        _RF("payer_overall_denial_rate",   0.11),
        _RF("primary_cpt_encoded",         0.09),
        _RF("has_unspecified_diagnosis",   0.07),
    ]),
    ("HC-02 healthcare — clinical-necessity profile", "healthcare", [
        _RF("cpt_dx_alignment_score",     0.31),
        _RF("primary_dx_chapter_encoded", 0.22),
        _RF("is_high_complexity_em",      0.14),
        _RF("acute_vs_chronic_indicator", 0.08),
    ]),
    ("HC-03 healthcare — timely + provider", "healthcare", [
        _RF("is_past_timely_filing",         0.51),
        _RF("timely_filing_proximity_ratio", 0.27),
        _RF("provider_overall_denial_rate",  0.19),
        _RF("provider_cpt_denial_rate",      0.10),
    ]),
    ("HC-04 healthcare — protective signals (excluded)", "healthcare", [
        _RF("has_prior_authorization",      0.38),
        _RF("payer_overall_denial_rate",   -0.25),  # protective; excluded
        _RF("provider_specialty_matches_cpt", -0.18),
    ]),
    ("HC-05 healthcare — coverage history mix", "healthcare", [
        _RF("patient_age_vs_procedure_valid", 0.22),
        _RF("prior_denials_with_payer",       0.18),
        _RF("missing_payer",                  0.12),
        _RF("is_secondary_claim",             0.05),
    ]),

    # ----- 837D / dental -----
    ("DT-01 dental — predetermination missing", "dental", [
        _RF("predetermination_filed",      0.36),
        _RF("cdt_category_encoded",        0.21),
        _RF("orthodontia_indicator",       0.13),
        _RF("tooth_number_specified",      0.07),
    ]),
    ("DT-02 dental — frequency / history", "dental", [
        _RF("service_age_in_months_for_tooth", 0.27),
        _RF("radiograph_within_year",          0.16),
        _RF("days_since_last_claim",           0.09),
    ]),
    ("DT-03 dental — preventive code with weak coverage", "dental", [
        _RF("is_preventive_service", 0.24),
        _RF("payer_name_encoded",    0.19),
        _RF("cpt_pos_alignment_score", 0.08),
    ]),

    # ----- 837I / home_care -----
    ("HC-HC-01 home_care — F2F + homebound docs", "home_care", [
        _RF("face_to_face_encounter_present",    0.34),
        _RF("homebound_certification_present",    0.22),
        _RF("physician_certification_present",    0.14),
        _RF("oasis_within_5_days",                0.09),
    ]),
    ("HC-HC-02 home_care — LUPA + visit count", "home_care", [
        _RF("is_lupa",                  0.28),
        _RF("visit_count_in_episode",   0.19),
        _RF("episode_length_days",      0.10),
        _RF("hipps_code_encoded",       0.08),
    ]),
    ("HC-HC-03 home_care — recert + history", "home_care", [
        _RF("is_recertification_episode", 0.31),
        _RF("claims_in_last_90d",         0.18),
        _RF("therapy_sessions_ytd",       0.11),
    ]),

    # ----- 837P / therapy -----
    ("TH-01 therapy — plan-of-care + cap", "therapy", [
        _RF("plan_of_care_present",       0.33),
        _RF("plan_of_care_recent",        0.21),
        _RF("cap_proximity",              0.18),
        _RF("kx_modifier_present",        0.10),
    ]),

    # ----- 837P / transport -----
    ("TR-01 transport — ambulance docs", "transport", [
        _RF("ambulance_cert_present",     0.29),
        _RF("origin_dest_specified",      0.16),
        _RF("transport_miles",            0.09),
    ]),

    # ----- 837I / institutional_other (and aliases) -----
    ("IN-01 inpatient — admission + DRG", "inpatient", [
        _RF("admission_date_present",     0.31),
        _RF("drg_assigned",               0.18),
        _RF("statement_period_days",      0.09),
    ]),

    # ----- 837P / specialty -----
    ("SP-01 specialty — DME rental docs", "specialty", [
        _RF("cr3_certification_present",  0.27),
        _RF("is_rental",                  0.16),
        _RF("j_code_count",               0.08),
    ]),
]


def show(label: str, subtype: str, factors: list[_RF]) -> None:
    print(f"--- {label}  [claim_subtype={subtype}]")
    print("    SHAP inputs:")
    for f in factors:
        sign = "+" if f.impact > 0 else " "
        print(f"      {sign}{f.impact:+.2f}  {f.feature}")
    rows = render_risk_factors(factors, top_k=5, claim_subtype=subtype)
    print("    Rendered reasons:")
    if not rows:
        print("      (none — all inputs were protective)")
    for i, r in enumerate(rows, 1):
        print(f"      {i}. {r['reason']}")
        print(f"         feature={r['feature']!r}  label={r['label']!r}  impact={r['impact']}")
    print()


def main() -> None:
    print("CR-078 / CR-078A — explanation quality preview")
    print("=" * 72)
    print()
    for scenario in SCENARIOS:
        show(*scenario)


if __name__ == "__main__":
    main()
