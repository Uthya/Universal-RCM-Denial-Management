"""CR-078 — claim-focused denial-reason renderer tests.

Pure-Python; no DB. Guards that:
  * No raw FB feature name can leak to the API response.
  * Multiple features collapse to the same business sentence.
  * Negative-impact ("protective") SHAP factors are excluded.
  * Sentences match the canonical set exactly (UI contract).
  * Dedupe keeps the highest-impact occurrence as the sort key.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from rcm.ml.reason_renderer import (
    REASON_AUTHORIZATION,
    REASON_BILLING,
    REASON_BUCKETS,
    REASON_COVERAGE,
    REASON_DIAGNOSIS,
    REASON_DOCUMENTATION,
    REASON_GENERAL,
    REASON_HISTORY,
    REASON_PROCEDURE,
    REASON_PROVIDER,
    REASON_SIMILAR,
    REASON_TIMELY,
    _SUBTYPE_NORMALISE,
    _VARIANT_OVERRIDES,
    render_reason,
    render_risk_factors,
)


@dataclass
class _RF:
    feature: str
    impact: float
    direction: str = "risk"


class TestRenderReason:
    @pytest.mark.parametrize(
        "feature, expected",
        [
            ("has_prior_authorization",       REASON_AUTHORIZATION),
            ("auth_missing_when_required",    REASON_AUTHORIZATION),
            ("referral_required_for_specialty", REASON_AUTHORIZATION),
            ("payer_overall_denial_rate",     REASON_COVERAGE),
            ("payer_name_encoded",            REASON_COVERAGE),
            ("missing_payer",                 REASON_COVERAGE),
            ("has_modifier",                  REASON_PROCEDURE),
            ("primary_cpt_encoded",           REASON_PROCEDURE),
            ("missing_procedure",             REASON_PROCEDURE),
            ("has_unspecified_diagnosis",     REASON_DIAGNOSIS),
            ("primary_dx_encoded",            REASON_DIAGNOSIS),
            ("missing_diagnosis",             REASON_DIAGNOSIS),
            ("diagnosis_count",               REASON_DIAGNOSIS),
            ("is_past_timely_filing",         REASON_TIMELY),
            ("paperwork_missing_when_required", REASON_DOCUMENTATION),
            ("plan_of_care_present",          REASON_DOCUMENTATION),
            ("homebound_certification_present", REASON_DOCUMENTATION),
            ("claims_in_last_30d",            REASON_HISTORY),
            ("prior_denials_with_payer",      REASON_HISTORY),
            ("provider_overall_denial_rate",  REASON_PROVIDER),
            ("billing_provider_npi_encoded",  REASON_PROVIDER),
            ("unseen_rendering_provider",     REASON_PROVIDER),
            ("total_charge_amount",           REASON_BILLING),
            ("line_count",                    REASON_BILLING),
            ("payer_cpt_denial_rate",         REASON_SIMILAR),
            ("cpt_dx_denial_rate",            REASON_SIMILAR),
            ("avail_payer_policies",          REASON_GENERAL),
            ("reference_data_completeness",   REASON_GENERAL),
            ("missing_count",                 REASON_GENERAL),
        ],
    )
    def test_known_feature_maps_to_expected_bucket(self, feature, expected):
        slug, title, sentence = render_reason(feature)
        assert sentence == expected
        # Slug + title are also part of the public surface and must be
        # human-readable, not a raw feature name.
        assert slug != feature
        assert title and " " not in slug or slug.replace("_", " ").isalpha()

    def test_unknown_feature_collapses_to_general(self):
        slug, title, sentence = render_reason("totally_made_up_feature_xyz")
        assert sentence == REASON_GENERAL
        assert slug == "general"
        assert title == "Claim Details"

    def test_empty_feature_collapses_to_general(self):
        _, _, sentence = render_reason("")
        assert sentence == REASON_GENERAL

    def test_eleven_unique_sentences(self):
        sentences = {b[2] for b in REASON_BUCKETS}
        assert len(sentences) == 11

    def test_no_sentence_contains_internal_terminology(self):
        forbidden = ("SHAP", "shap", "encoded ", "encoder",
                     "feature ", "FB ", "feature contribution",
                     "%", "percentile", "target-encod")
        # Defaults
        for slug, title, sentence in REASON_BUCKETS:
            for f in forbidden:
                assert f not in sentence, (
                    f"Bucket {slug!r} default sentence leaks internal term "
                    f"{f!r}: {sentence!r}"
                )
        # CR-078A variant overrides must clear the same guard.
        for (slug, subtype), sentence in _VARIANT_OVERRIDES.items():
            for f in forbidden:
                assert f not in sentence, (
                    f"Variant override {(slug, subtype)!r} leaks internal "
                    f"term {f!r}: {sentence!r}"
                )

    def test_default_sentences_match_cr_078a_spec(self):
        """Defaults are part of the user-facing contract; lock them down."""
        assert REASON_AUTHORIZATION == "Authorization requirements may not be fully satisfied for this claim."
        assert REASON_COVERAGE == "Coverage eligibility or member information should be reviewed."
        assert REASON_PROCEDURE == "The billed procedure information may increase denial risk."
        assert REASON_DIAGNOSIS == "Diagnosis details may require additional review before submission."
        assert REASON_TIMELY == "Submission timing should be reviewed against payer filing deadlines."
        assert REASON_DOCUMENTATION == "Supporting documentation may be insufficient or incomplete."
        assert REASON_HISTORY == "Previous claim patterns indicate a higher denial risk."
        assert REASON_PROVIDER == "Provider-related claim information should be reviewed."
        assert REASON_BILLING == "Billing details may require verification before submission."
        assert REASON_SIMILAR == "Claims with similar characteristics have historically shown higher denial risk."
        assert REASON_GENERAL == "This claim contains factors commonly associated with denials."


class TestRenderRiskFactors:
    def test_none_input_returns_empty(self):
        assert render_risk_factors(None) == []

    def test_empty_input_returns_empty(self):
        assert render_risk_factors([]) == []

    def test_only_positive_impact_factors_included(self):
        factors = [
            _RF("auth_missing_when_required",  0.42),
            _RF("payer_overall_denial_rate",  -0.18),  # protective; excluded
            _RF("primary_cpt_encoded",         0.10),
        ]
        rows = render_risk_factors(factors)
        sentences = [r["reason"] for r in rows]
        assert REASON_AUTHORIZATION in sentences
        assert REASON_PROCEDURE in sentences
        assert REASON_COVERAGE not in sentences  # the negative one

    def test_dedupes_by_bucket_keeping_max_impact(self):
        factors = [
            _RF("has_prior_authorization",      0.15),
            _RF("auth_missing_when_required",   0.40),  # winner of AUTH
            _RF("referral_missing_when_required", 0.05),
        ]
        rows = render_risk_factors(factors)
        assert len(rows) == 1
        assert rows[0]["reason"] == REASON_AUTHORIZATION
        assert rows[0]["impact"] == pytest.approx(0.40)

    def test_caps_at_top_k(self):
        # Eleven distinct buckets — request 5, expect 5.
        # CR-093: ordering is now (actionability_tier, -impact) rather than
        # purely impact-DESC. The cap-at-top_k contract is unchanged; only
        # the cross-tier ordering rule changed.
        factors = [
            _RF("auth_missing_when_required", 0.10),  # authorization (tier 0)
            _RF("missing_payer",              0.20),  # coverage      (tier 1)
            _RF("primary_cpt_encoded",        0.30),  # procedure     (tier 0)
            _RF("primary_dx_encoded",         0.40),  # diagnosis     (tier 0)
            _RF("is_past_timely_filing",      0.50),  # timely_filing (tier 1)
            _RF("paperwork_missing_when_required", 0.60),  # documentation (tier 0)
            _RF("claims_in_last_30d",         0.70),  # history       (tier 2)
            _RF("provider_overall_denial_rate", 0.80),  # provider    (tier 1)
            _RF("total_charge_amount",        0.90),  # billing       (tier 1)
            _RF("payer_cpt_denial_rate",      1.00),  # similar       (tier 2)
            _RF("avail_payer_policies",       1.10),  # general       (tier 2)
        ]
        rows = render_risk_factors(factors, top_k=5)
        assert len(rows) == 5
        # CR-093 ordering rule: (tier, -impact). Within tier 0 (directly
        # actionable: authorization, documentation, procedure, diagnosis)
        # the four entries sort by impact DESC, so the top-5 fills with all
        # four tier-0 buckets and then the highest-impact tier-1 bucket.
        slugs = [r["feature"] for r in rows]
        impacts = [r["impact"] for r in rows]
        # Tier 0 buckets first, ordered by impact DESC:
        #   documentation (0.60) > diagnosis (0.40) > procedure (0.30) > authorization (0.10)
        # Then the highest tier-1 bucket:
        #   billing (0.90) — beats provider 0.80, timely_filing 0.50, coverage 0.20
        assert slugs == [
            "documentation", "diagnosis", "procedure", "authorization", "billing",
        ]
        assert impacts == [0.6, 0.4, 0.3, 0.1, 0.9]

    def test_no_raw_feature_name_in_output(self):
        factors = [
            _RF("auth_missing_when_required", 0.42),
            _RF("primary_cpt_encoded",        0.31),
            _RF("provider_overall_denial_rate", 0.22),
            _RF("totally_made_up_feature_xyz", 0.10),
        ]
        rows = render_risk_factors(factors)
        for row in rows:
            for key in ("feature", "label", "reason"):
                v = row.get(key) or ""
                # Forbid any underscore-cased FB-style identifier in the
                # public-facing fields. Bucket slugs use single words.
                for forbidden in (
                    "auth_missing", "primary_cpt", "provider_overall",
                    "_encoded", "_denial_rate", "FB feature", "totally_made_up",
                ):
                    assert forbidden not in v, (
                        f"Row exposes raw FB token {forbidden!r} via {key}: {v!r}"
                    )

    def test_unknown_feature_collapses_to_general_sentence(self):
        rows = render_risk_factors([_RF("brand_new_feature_added_yesterday", 0.5)])
        assert len(rows) == 1
        assert rows[0]["reason"] == REASON_GENERAL
        assert rows[0]["feature"] == "general"
        assert rows[0]["label"] == "Claim Details"

    def test_accepts_dict_inputs_not_only_dataclasses(self):
        rows = render_risk_factors([
            {"feature": "is_past_timely_filing", "impact": 0.7, "direction": "risk"},
        ])
        assert len(rows) == 1
        assert rows[0]["reason"] == REASON_TIMELY

    def test_direction_is_user_facing(self):
        rows = render_risk_factors([_RF("auth_missing_when_required", 0.42)])
        assert rows[0]["direction"] == "increases denial risk"


class TestVariantOverrides:
    """CR-078A — variant-aware sentence selection."""

    def test_documentation_override_per_variant(self):
        # The spec gave explicit wording for these three subtypes.
        _, _, hc = render_reason("paperwork_missing_when_required", "healthcare")
        _, _, dt = render_reason("paperwork_missing_when_required", "dental")
        _, _, hh = render_reason("paperwork_missing_when_required", "home_care")
        assert hc == "Clinical documentation may require review."
        assert dt == "Treatment documentation may require review."
        assert hh == "Home health documentation requirements may need verification."

    def test_documentation_default_when_subtype_unknown(self):
        _, _, sentence = render_reason("paperwork_missing_when_required", "unrecognised_subtype")
        assert sentence == REASON_DOCUMENTATION

    def test_documentation_default_when_subtype_omitted(self):
        _, _, sentence = render_reason("paperwork_missing_when_required")
        assert sentence == REASON_DOCUMENTATION

    def test_inpatient_alias_routes_to_institutional_other(self):
        # 837I/inpatient and 837I/hospice are aliases for institutional_other
        # per parsing/routing.py and the feature registry. The override table
        # is keyed on institutional_other; the alias must reach it.
        _, _, ip = render_reason("paperwork_missing_when_required", "inpatient")
        _, _, ho = render_reason("paperwork_missing_when_required", "hospice")
        _, _, io = render_reason("paperwork_missing_when_required", "institutional_other")
        assert ip == "Facility documentation may require review."
        assert ho == "Facility documentation may require review."
        assert io == "Facility documentation may require review."

    def test_procedure_override_per_variant(self):
        _, _, dt = render_reason("primary_cpt_encoded", "dental")
        _, _, hh = render_reason("primary_cpt_encoded", "home_care")
        _, _, th = render_reason("primary_cpt_encoded", "therapy")
        assert dt == "The billed dental treatment information may increase denial risk."
        assert hh == "The billed home-health services may increase denial risk."
        assert th == "The billed therapy services may increase denial risk."

    def test_coverage_override_for_home_care_and_facility(self):
        _, _, hh = render_reason("payer_overall_denial_rate", "home_care")
        _, _, fac = render_reason("payer_overall_denial_rate", "inpatient")  # alias
        assert hh == "Home-health coverage eligibility or member information should be reviewed."
        assert fac == "Inpatient or facility coverage information should be reviewed."

    def test_no_override_means_default_fires(self):
        # Authorization has no variant overrides — defaults must apply
        # for every subtype.
        for subtype in ("healthcare", "dental", "home_care", "therapy",
                        "transport", "institutional_other", "specialty",
                        "inpatient", "hospice"):
            _, _, sentence = render_reason("auth_missing_when_required", subtype)
            assert sentence == REASON_AUTHORIZATION, (
                f"unexpected auth override for subtype={subtype}"
            )

    def test_render_risk_factors_forwards_subtype(self):
        factors = [_RF("paperwork_missing_when_required", 0.42)]
        rows = render_risk_factors(factors, claim_subtype="dental")
        assert len(rows) == 1
        assert rows[0]["reason"] == "Treatment documentation may require review."

    def test_render_risk_factors_default_when_subtype_omitted(self):
        factors = [_RF("paperwork_missing_when_required", 0.42)]
        rows = render_risk_factors(factors)
        assert rows[0]["reason"] == REASON_DOCUMENTATION

    def test_every_override_targets_a_known_bucket(self):
        known_slugs = {b[0] for b in REASON_BUCKETS}
        for (slug, _subtype) in _VARIANT_OVERRIDES:
            assert slug in known_slugs, (
                f"variant override references unknown bucket {slug!r}"
            )

    def test_normalisation_table_aliases_only_known_subtypes(self):
        # Sanity check on _SUBTYPE_NORMALISE so aliases stay consistent with
        # the feature registry's column aliasing for 837I.
        for alias, target in _SUBTYPE_NORMALISE.items():
            assert alias != target, (
                f"normalisation should map alias to a different subtype, "
                f"but {alias!r} maps to itself"
            )

    def test_no_override_contains_internal_terminology(self):
        # Belt-and-braces — also enforced in TestRenderReason but kept here
        # so a future contributor adding overrides reads it locally.
        forbidden = ("SHAP", "shap", "encoded ", "encoder", "FB ",
                     "feature contribution", "%", "percentile")
        for (slug, subtype), sentence in _VARIANT_OVERRIDES.items():
            for f in forbidden:
                assert f not in sentence, (
                    f"override {(slug, subtype)!r} leaks {f!r}: {sentence!r}"
                )
