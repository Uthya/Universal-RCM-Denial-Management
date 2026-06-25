"""Per-variant Cat-M block tests + dispatch + global-fallback."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from rcm.features.categories.history import PatientHistorySnapshot
from rcm.features.registry import (
    FEATURE_COLUMNS_DENTAL,
    FEATURE_COLUMNS_GLOBAL,
    FEATURE_COLUMNS_HEALTHCARE,
    FEATURE_COLUMNS_HOME_CARE,
    FEATURE_COLUMNS_INSTITUTIONAL_OTHER,
    FEATURE_COLUMNS_SPECIALTY,
    FEATURE_COLUMNS_THERAPY,
    FEATURE_COLUMNS_TRANSPORT,
    feature_count_by_variant,
    get_feature_columns,
    is_registered_variant,
    registered_variants,
    universal_columns,
)
from rcm.features.variants import (
    dental,
    global_fallback,
    healthcare,
    home_care,
    institutional_other,
    specialty,
    therapy,
    transport,
)


def _row(**overrides) -> dict:
    """One row with sensible defaults for every column the variant blocks read."""
    base = {
        "claim_id": 1,
        "service_variant": "837P",
        "claim_subtype": "healthcare",
        "primary_cpt": "99213",
        "primary_dx": "I10",
        "primary_pos": "11",
        "procedure_codes": ["99213"],
        "modifiers": [],
        "revenue_codes": [],
        "hipps_codes": [],
        "tooth_numbers": [],
        "ndc_drug_codes": [],
        "cert_types": [],
        "attachment_types": [],
        "variant_data": {},
        "service_from_date": date(2026, 6, 1),
        "service_to_date": date(2026, 6, 1),
        "total_charge_amount": 150.0,
        "home_care_episode": None,
        "transport_cert": None,
    }
    base.update(overrides)
    return base


class TestRegistryCounts:
    """Spec §4.2 prescribes exact universal + variant feature counts.
    These are load-bearing — model artifacts persist `feature_columns`, so
    bumping a count is a SCHEMA change that needs a CHANGELOG entry."""

    def test_universal_is_102(self):
        # CR-104 retired 7 universal Tier-A features (108→101).
        # CR-122B retired frequency_code_encoded (101→100).
        # CR-126B added 2 recency smoothed features (100→102).
        assert len(universal_columns()) == 102

    @pytest.mark.parametrize("key, expected_count", [
        # CR-104 baseline; CR-122B -1; CR-126B +2 universal across every variant,
        # EXCEPT 837D which excludes the 2 recency features per the CR-126
        # pre-merge gate (PR-AUC regression on tiny positive class).
        (("837P", "healthcare"),           106),
        (("837P", "therapy"),              111),
        (("837P", "transport"),            110),
        (("837P", "specialty"),            112),
        (("837I", "home_care"),            113),
        (("837I", "institutional_other"),  107),
        (("837I", "inpatient"),            107),   # alias
        (("837I", "hospice"),              107),   # alias
        (("837I", "specialty"),            107),   # alias
        (("837D", "dental"),               108),   # CR-126B exclusion: 110-2=108
        (("_global", "_global"),           102),
    ])
    def test_per_variant_count(self, key, expected_count):
        actual = len(get_feature_columns(*key))
        assert actual == expected_count, f"{key} produced {actual}, expected {expected_count}"

    def test_count_by_variant_helper(self):
        counts = feature_count_by_variant()
        assert counts[("837P", "healthcare")] == 106
        assert counts[("_global", "_global")] == 102

    def test_registered_variants_returns_all_11(self):
        assert len(registered_variants()) == 11


class TestTherapyVariant:
    def test_gp_modifier_present(self):
        df = pd.DataFrame([_row(
            modifiers=["GP", "KX"],
            primary_cpt="97110",
            attachment_types=["PN"],
            variant_data={"plan_of_care_start_date": "2026-05-20"},
        )])
        out = therapy.compute(df)
        assert len(out.columns) == 9
        assert out.loc[0, "discipline_modifier_encoded"] > 0
        assert out.loc[0, "kx_modifier_present"] == 1
        assert out.loc[0, "plan_of_care_present"] == 1
        assert out.loc[0, "plan_of_care_recent"] == 1

    def test_eval_cpt_detected(self):
        df = pd.DataFrame([_row(primary_cpt="97161")])
        out = therapy.compute(df)
        assert out.loc[0, "evaluation_vs_treatment"] == 1
        df2 = pd.DataFrame([_row(primary_cpt="97110")])
        out2 = therapy.compute(df2)
        assert out2.loc[0, "evaluation_vs_treatment"] == 0

    def test_maintenance_modifier_aliased(self):
        df = pd.DataFrame([_row(modifiers=["KH"])])
        out = therapy.compute(df)
        assert out.loc[0, "kh_modifier"] == 1
        assert out.loc[0, "is_maintenance_therapy"] == 1

    def test_cap_proximity_clipped(self):
        # Inject an absurd YTD charge via history snapshot
        snap = PatientHistorySnapshot()
        snap.by_claim[1] = {"annual_charges_for_patient": 100_000.0}
        df = pd.DataFrame([_row()])
        out = therapy.compute(df, history_snapshot=snap)
        # 100k / 2330 = 43 → clipped to 5.0
        assert out.loc[0, "cap_proximity"] == pytest.approx(5.0)


class TestTransportVariant:
    def test_no_cert_defaults_zero(self):
        df = pd.DataFrame([_row()])
        out = transport.compute(df)
        assert len(out.columns) == 8
        assert out.loc[0, "ambulance_cert_present"] == 0
        assert out.loc[0, "transport_miles"] == 0.0
        assert out.loc[0, "patient_weight_lbs"] == 0

    def test_cert_populated(self):
        df = pd.DataFrame([_row(transport_cert={
            "transport_miles": 12.0,
            "patient_weight_lbs": 180,
            "transport_reason_code": "B",
            "round_trip": False,
            "emergent": False,
            "level_of_service": "ALS",
            "origin_address": {"line1": "..."},
            "destination_address": {"line1": "..."},
        })])
        out = transport.compute(df)
        assert out.loc[0, "ambulance_cert_present"] == 1
        assert out.loc[0, "transport_miles"] == 12.0
        assert out.loc[0, "patient_weight_lbs"] == 180
        assert out.loc[0, "transport_reason_code_encoded"] == 2.0  # B → 2
        assert out.loc[0, "los_modifier_encoded"] == 1.0           # ALS → 1
        assert out.loc[0, "origin_dest_specified"] == 1


class TestHomeCareVariant:
    def test_lupa_detected(self):
        df = pd.DataFrame([_row(home_care_episode={
            "episode_start_date": date(2026, 5, 1),
            "episode_end_date":   date(2026, 5, 30),
            "visit_count": 3,
            "homebound_certified": True,
            "discipline_mix": {"PT": 2, "SN": 1},
        })])
        out = home_care.compute(df)
        assert len(out.columns) == 11
        assert out.loc[0, "is_lupa"] == 1
        assert out.loc[0, "visit_count_in_episode"] == 3
        assert out.loc[0, "discipline_count_total"] == 3
        assert out.loc[0, "episode_length_days"] == 29

    def test_homebound_cert_present(self):
        df = pd.DataFrame([_row(cert_types=["homebound"])])
        out = home_care.compute(df)
        assert out.loc[0, "homebound_certification_present"] == 1

    def test_oasis_within_5_days(self):
        df = pd.DataFrame([_row(home_care_episode={
            "episode_start_date": date(2026, 5, 1),
            "oasis_assessment_date": date(2026, 5, 3),
        })])
        out = home_care.compute(df)
        assert out.loc[0, "oasis_within_5_days"] == 1

    def test_skilled_revenue_count(self):
        df = pd.DataFrame([_row(revenue_codes=["0551", "0571", "0572", "0589"])])
        out = home_care.compute(df)
        # 0551, 0571, 0572 are skilled; 0589 is not
        assert out.loc[0, "revenue_code_count_skilled"] == 3


class TestDentalVariant:
    def test_tooth_specified(self):
        df = pd.DataFrame([_row(tooth_numbers=["14", "15"])])
        out = dental.compute(df)
        assert len(out.columns) == 8
        assert out.loc[0, "tooth_number_specified"] == 1
        assert out.loc[0, "tooth_surface_count"] == 2.0

    def test_cdt_category_encoded(self):
        # D2391 is restorative (D2)
        df = pd.DataFrame([_row(primary_cpt="D2391")])
        out = dental.compute(df)
        assert out.loc[0, "cdt_category_encoded"] == 2.0
        # D1110 is preventive (D1)
        df2 = pd.DataFrame([_row(primary_cpt="D1110")])
        out2 = dental.compute(df2)
        assert out2.loc[0, "cdt_category_encoded"] == 1.0
        assert out2.loc[0, "is_preventive_service"] == 1

    def test_predetermination_filed(self):
        df = pd.DataFrame([_row(variant_data={"references": {"F8": "PREDET123"}})])
        out = dental.compute(df)
        assert out.loc[0, "predetermination_filed"] == 1

    def test_radiograph(self):
        df = pd.DataFrame([_row(primary_cpt="D0220")])
        out = dental.compute(df)
        assert out.loc[0, "radiograph_within_year"] == 1


class TestSpecialtyVariant:
    def test_oncology_signals(self):
        df = pd.DataFrame([_row(
            primary_cpt="J9999",
            procedure_codes=["J9999", "J0001"],
            ndc_drug_codes=["12345-6789-01"],
            total_charge_amount=10_000.0,
        )])
        out = specialty.compute(df)
        assert len(out.columns) == 10
        assert out.loc[0, "ndc_drug_present"] == 1
        assert out.loc[0, "j_code_count"] == 2
        assert out.loc[0, "is_high_cost_drug"] == 1

    def test_dme_signals(self):
        df = pd.DataFrame([_row(
            modifiers=["RR"],
            cert_types=["dme"],
        )])
        out = specialty.compute(df)
        assert out.loc[0, "cr3_certification_present"] == 1
        assert out.loc[0, "is_rental"] == 1
        assert out.loc[0, "is_purchase"] == 0

    def test_behavioral_signals(self):
        df = pd.DataFrame([_row(primary_cpt="H0001")])
        out = specialty.compute(df)
        assert out.loc[0, "is_h_code"] == 1

        df2 = pd.DataFrame([_row(primary_cpt="90791")])
        out2 = specialty.compute(df2)
        assert out2.loc[0, "is_initial_assessment"] == 1

        df3 = pd.DataFrame([_row(primary_cpt="90853")])
        out3 = specialty.compute(df3)
        assert out3.loc[0, "is_group_therapy"] == 1

    def test_lab_clia(self):
        df = pd.DataFrame([_row(variant_data={"references": {"X4": "CLIA12345"}})])
        out = specialty.compute(df)
        assert out.loc[0, "clia_number_present"] == 1


class TestInstitutionalOtherVariant:
    def test_inpatient_revenue(self):
        df = pd.DataFrame([_row(revenue_codes=["0110", "0120"])])
        out = institutional_other.compute(df)
        assert len(out.columns) == 5
        assert out.loc[0, "inpatient_revenue_code_present"] == 1
        assert out.loc[0, "hospice_revenue_code_present"] == 0

    def test_hospice_revenue(self):
        df = pd.DataFrame([_row(revenue_codes=["0820", "0830"])])
        out = institutional_other.compute(df)
        assert out.loc[0, "hospice_revenue_code_present"] == 1
        assert out.loc[0, "inpatient_revenue_code_present"] == 0

    def test_statement_period(self):
        df = pd.DataFrame([_row(
            service_from_date=date(2026, 5, 1),
            service_to_date=date(2026, 5, 30),
        )])
        out = institutional_other.compute(df)
        assert out.loc[0, "statement_period_days"] == 29


class TestGlobalFallback:
    def test_returns_empty_columns(self):
        df = pd.DataFrame([_row()])
        out = global_fallback.compute(df)
        # Cat M block for global = zero columns; the universal cols come from
        # the other categories, not from here
        assert len(out.columns) == 0
        assert len(out) == 1   # same row count as input


class TestFeatureBuilderDispatch:
    """The dispatch table must wire each variant to its module + fall back
    cleanly to global for unknown variants."""

    def test_known_variants_resolve_to_distinct_column_lists(self):
        # Two different known variants must NOT produce the same column count
        # (otherwise the dispatch is broken)
        ths = get_feature_columns("837P", "therapy")
        hcs = get_feature_columns("837P", "healthcare")
        assert ths != hcs
        # And neither equals the global fallback
        assert ths != FEATURE_COLUMNS_GLOBAL
        assert hcs != FEATURE_COLUMNS_GLOBAL

    def test_unknown_variant_resolves_to_global(self):
        cols = get_feature_columns("837Z", "mystery", fall_back_to_global=True)
        assert cols == FEATURE_COLUMNS_GLOBAL

    def test_unknown_variant_raises_without_fallback(self):
        with pytest.raises(KeyError, match="No FEATURE_COLUMNS registered"):
            get_feature_columns("837Z", "mystery")

    def test_is_registered_variant(self):
        assert is_registered_variant("837P", "healthcare") is True
        assert is_registered_variant("837P", "therapy") is True
        assert is_registered_variant("837Z", "mystery") is False
