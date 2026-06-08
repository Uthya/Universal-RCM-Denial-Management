"""Category-level computers on synthetic mini-frames."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from rcm.features.categories import (
    authorization,
    availability,
    base,
    clinical,
    coding,
    coverage,
    documentation,
    rarity,
    timely,
)
from rcm.features.categories.availability import RefDataLookup
from rcm.features.categories.rarity import RarityState


@pytest.fixture
def synth_df():
    return pd.DataFrame([
        {
            "claim_id": 1,
            "service_variant": "837P",
            "claim_subtype": "healthcare",
            "claim_number": "C1",
            "payer_id": 100,
            "patient_id": 200,
            "billing_provider_id": 300,
            "rendering_provider_id": 300,
            "referring_provider_id": None,
            "payer_canonical_name": "AETNA",
            "patient_dob": date(1980, 1, 1),
            "patient_gender": "F",
            "billing_provider_npi": "1234567890",
            "rendering_provider_npi": "1234567890",
            "billing_provider_taxonomy": "207Q00000X",
            "subscriber_cob": "P",
            "subscriber_relationship": "18",
            "service_from_date": date(2026, 6, 1),
            "service_to_date": date(2026, 6, 1),
            "submission_date": date(2026, 6, 10),
            "total_charge_amount": 150.0,
            "facility_type_code": "11",
            "frequency_code": "1",
            "authorization_number": "AUTH001",
            "referral_number": None,
            "primary_cpt": "99213",
            "primary_dx": "I10",
            "primary_dx_type": "ABK",
            "primary_pos": "11",
            "procedure_codes": ["99213"],
            "modifiers": ["25"],
            "revenue_codes": [],
            "hipps_codes": [],
            "tooth_numbers": [],
            "ndc_drug_codes": [],
            "lines_billed_sum": 150.0,
            "lines_units_sum": 1.0,
            "claim_lines_count": 1,
            "diagnoses_count": 2,
            "diagnoses": [{"code": "I10", "type": "ABK"}, {"code": "E119", "type": "ABF"}],
            "variant_data": {},
            "has_paperwork": False,
            "has_certification": False,
        },
        {
            "claim_id": 2,
            "service_variant": "837P",
            "claim_subtype": "healthcare",
            "claim_number": "C2",
            "payer_id": 100,
            "patient_id": 201,
            "billing_provider_id": 300,
            "rendering_provider_id": 300,
            "referring_provider_id": None,
            "payer_canonical_name": "AETNA",
            "patient_dob": date(1955, 6, 15),
            "patient_gender": "M",
            "billing_provider_npi": "1234567890",
            "rendering_provider_npi": "1234567890",
            "billing_provider_taxonomy": "207Q00000X",
            "subscriber_cob": "S",
            "subscriber_relationship": "18",
            "service_from_date": date(2026, 1, 1),  # old → near timely filing
            "service_to_date": date(2026, 1, 1),
            "submission_date": date(2026, 11, 1),
            "total_charge_amount": 500.0,
            "facility_type_code": "11",
            "frequency_code": "7",  # replacement
            "authorization_number": None,
            "referral_number": None,
            "primary_cpt": "99215",  # high complexity
            "primary_dx": "I10",
            "primary_dx_type": "ABK",
            "primary_pos": "11",
            "procedure_codes": ["99215", "97110"],
            "modifiers": ["25", "59"],   # invalid combo
            "revenue_codes": [],
            "hipps_codes": [],
            "tooth_numbers": [],
            "ndc_drug_codes": [],
            "lines_billed_sum": 500.0,
            "lines_units_sum": 2.0,
            "claim_lines_count": 2,
            "diagnoses_count": 1,
            "diagnoses": [{"code": "I10", "type": "ABK"}],
            "variant_data": {"notes": [{"ref": "ADD", "text": "hi"}]},
            "has_paperwork": True,
            "has_certification": False,
        },
    ]).set_index("claim_id", drop=False)


class TestBase:
    def test_base_features(self, synth_df):
        b = base.compute(synth_df)
        assert set(b.columns) >= {"total_charge_amount", "line_count", "diagnosis_count",
                                   "service_month", "weekend_service", "is_single_day_service"}
        assert b.loc[1, "total_charge_amount"] == 150.0
        assert b.loc[2, "line_count"] == 2
        assert b.loc[1, "service_month"] == 6
        assert b.loc[1, "is_single_day_service"] == 1


class TestCoverage:
    def test_age_band(self, synth_df):
        c = coverage.compute(synth_df)
        # Patient 1 born 1980 → 46 in 2026 → band 2 (40-64)
        assert c.loc[1, "patient_age_band"] == 2
        # Patient 2 born 1955 → 70 → band 3 (65+)
        assert c.loc[2, "patient_age_band"] == 3

    def test_cob_position(self, synth_df):
        c = coverage.compute(synth_df)
        assert c.loc[1, "cob_position_encoded"] == 1   # P
        assert c.loc[2, "cob_position_encoded"] == 2   # S
        assert c.loc[2, "is_secondary_claim"] == 1


class TestAuthorization:
    def test_has_flags(self, synth_df):
        a = authorization.compute(synth_df)
        assert a.loc[1, "has_prior_authorization"] == 1
        assert a.loc[2, "has_prior_authorization"] == 0
        assert a.loc[1, "has_referral"] == 0
        # No ref data → required flags default 0
        assert a.loc[1, "auth_required_for_cpt_payer"] == 0
        assert a.loc[1, "auth_missing_when_required"] == 0

    def test_with_ref_data(self, synth_df):
        ref = RefDataLookup(
            payer_policies_by_payer={
                "AETNA": [
                    {"policy_type": "prior_auth", "applies_to_codes": ["99213"]},
                ]
            }
        )
        a = authorization.compute(synth_df, ref=ref)
        assert a.loc[1, "auth_required_for_cpt_payer"] == 1
        assert a.loc[1, "auth_missing_when_required"] == 0  # has auth
        assert a.loc[2, "auth_required_for_cpt_payer"] == 0  # cpt 99215 not in list


class TestClinical:
    def test_unspecified_dx(self, synth_df):
        # Inject a Z-code dx for claim 1
        synth_df.loc[1, "primary_dx"] = "Z00.00"
        c = clinical.compute(synth_df)
        assert c.loc[1, "has_unspecified_diagnosis"] == 1
        assert c.loc[2, "has_unspecified_diagnosis"] == 0

    def test_high_complexity_em(self, synth_df):
        c = clinical.compute(synth_df)
        assert c.loc[1, "is_high_complexity_em"] == 0  # 99213
        assert c.loc[2, "is_high_complexity_em"] == 1  # 99215


class TestCoding:
    def test_modifier_count(self, synth_df):
        c = coding.compute(synth_df)
        assert c.loc[1, "modifier_count_total"] == 1
        assert c.loc[2, "modifier_count_total"] == 2
        # 25+59 is in the invalid combo set
        assert c.loc[2, "has_invalid_modifier_combo"] == 1

    def test_replacement_claim(self, synth_df):
        c = coding.compute(synth_df)
        assert c.loc[1, "is_replacement_claim"] == 0
        assert c.loc[2, "is_replacement_claim"] == 1

    def test_unbundled_with_ncci(self, synth_df):
        # Strip modifier 59 (NCCI override) from claim 2 so the unbundling
        # heuristic actually fires
        synth_df.at[2, "modifiers"] = ["25"]
        ref = RefDataLookup(ncci_pairs=frozenset({("99215", "97110")}))
        c = coding.compute(synth_df, ref=ref)
        assert c.loc[2, "is_likely_unbundled"] == 1
        assert c.loc[1, "is_likely_unbundled"] == 0

    def test_unbundled_neutralized_by_override_modifier(self, synth_df):
        # When modifier 59 is present, NCCI flagging is overridden
        ref = RefDataLookup(ncci_pairs=frozenset({("99215", "97110")}))
        c = coding.compute(synth_df, ref=ref)
        # Claim 2 already has modifier 59
        assert c.loc[2, "is_likely_unbundled"] == 0


class TestTimely:
    def test_days_and_ratio(self, synth_df):
        t = timely.compute(synth_df)
        # Claim 1: 9 days, well within 365
        assert t.loc[1, "service_to_submission_days"] == 9
        assert t.loc[1, "is_past_timely_filing"] == 0
        # Claim 2: 304 days, ratio 0.83 → near
        assert t.loc[2, "service_to_submission_days"] == 304
        assert t.loc[2, "timely_filing_proximity_ratio"] >= 0.83
        assert t.loc[2, "is_near_timely_filing"] == 0  # ratio is 0.832… just under 0.85


class TestDocumentation:
    def test_paperwork_flags(self, synth_df):
        d = documentation.compute(synth_df)
        assert d.loc[1, "has_paperwork_attachment"] == 0
        assert d.loc[2, "has_paperwork_attachment"] == 1
        assert d.loc[2, "has_notes"] == 1


class TestRarity:
    def test_no_state_means_no_flags(self, synth_df):
        r = rarity.compute(synth_df, state=None)
        # When no vocab, unseen flags are 0 (treat as known)
        assert r.loc[1, "unseen_payer"] == 0
        assert r.loc[1, "unseen_any"] == 0
        # is_rare also 0 (no volume info)
        assert r.loc[1, "is_rare_payer"] == 0

    def test_unseen_with_state(self, synth_df):
        state = RarityState.fit(synth_df.head(1))   # vocab only has claim 1's values
        # Inject a new payer for claim 2
        synth_df.loc[2, "payer_canonical_name"] = "BCBS"
        r = rarity.compute(synth_df, state=state)
        assert r.loc[1, "unseen_payer"] == 0
        assert r.loc[2, "unseen_payer"] == 1
        assert r.loc[2, "unseen_any"] == 1
        # Mutual exclusion: unseen=1 → is_rare=0
        assert r.loc[2, "is_rare_payer"] == 0


class TestAvailability:
    def test_empty_ref_completeness_zero(self, synth_df):
        a = availability.compute(synth_df, ref=None)
        assert (a["reference_data_completeness"] == 0.0).all()

    def test_partial_ref_completeness(self, synth_df):
        ref = RefDataLookup(
            ncci_pairs=frozenset({("a", "b")}),
            payer_policies_by_payer={"AETNA": [{}]},
        )
        a = availability.compute(synth_df, ref=ref)
        # 2 of 4 → 0.5
        assert a.loc[1, "reference_data_completeness"] == pytest.approx(0.5)
        assert a.loc[1, "avail_ncci_edits"] == 1
        assert a.loc[1, "avail_payer_policies"] == 1
        assert a.loc[1, "avail_procedure_codes_metadata"] == 0
        assert a.loc[1, "avail_lcd_coverage"] == 0
