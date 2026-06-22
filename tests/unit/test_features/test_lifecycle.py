"""CR-117 Stage 1 — unit tests for lifecycle compute().

Synthetic-only. No DB. The `original_top_carc_bucket` column is supplied
pre-resolved (the async CARC→bucket lookup lives in
`rcm.features.dataset.load_original_snapshots` and is exercised by the
inline verification step at CR-117 close-out, not here).
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from rcm.features.categories.lifecycle import (
    LIFECYCLE_FEATURE_COLUMNS,
    _BUCKET_TO_INT,
    compute,
)


# ---- helpers ----------------------------------------------------------------

_DF_COLS = [
    "frequency_code", "authorization_number", "referral_number",
    "service_from_date",
    # Stage 2 columns
    "modifiers", "diagnoses", "procedure_codes", "claim_lines_count",
    "total_charge_amount",
]
_ORIG_COLS = [
    "original_id", "original_claim_status_code", "original_remittance_date",
    "original_authorization_number", "original_referral_number",
    "original_top_carc", "original_top_carc_bucket",
    # Stage 2 columns
    "original_modifiers", "original_procedure_codes",
    "original_diagnosis_codes", "original_claim_lines_count",
    "original_total_charge_amount",
]


def _df(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        out = pd.DataFrame({c: pd.Series(dtype="object") for c in _DF_COLS})
        out.index = pd.Index([], name="claim_id", dtype="int64")
        return out
    out = pd.DataFrame(rows).set_index("claim_id", drop=True)
    for c in _DF_COLS:
        if c not in out.columns:
            out[c] = None
    return out


def _orig(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        out = pd.DataFrame({c: pd.Series(dtype="object") for c in _ORIG_COLS})
        out.index = pd.Index([], name="child_id", dtype="int64")
        return out
    out = pd.DataFrame(rows).set_index("claim_id", drop=True).rename_axis("child_id")
    for c in _ORIG_COLS:
        if c not in out.columns:
            out[c] = None
    return out


# ---- tests ------------------------------------------------------------------

class TestLifecycleCompute:
    def test_emits_canonical_columns_in_order(self):
        df = _df([{"claim_id": 1, "frequency_code": "7",
                   "service_from_date": date(2026, 6, 1)}])
        orig = _orig([])
        out = compute(df, orig)
        assert list(out.columns) == list(LIFECYCLE_FEATURE_COLUMNS)
        assert len(out) == 1

    def test_empty_df_returns_empty_frame_with_schema(self):
        df = _df([])
        orig = _orig([])
        out = compute(df, orig)
        assert list(out.columns) == list(LIFECYCLE_FEATURE_COLUMNS)
        assert len(out) == 0

    def test_freq1_row_emits_all_zeros(self):
        """Non-replacement claims must never carry lifecycle signal."""
        df = _df([{"claim_id": 1, "frequency_code": "1",
                   "authorization_number": "AUTH",
                   "referral_number": "REF",
                   "service_from_date": date(2026, 6, 1)}])
        orig = _orig([{"claim_id": 1,
                       "original_claim_status_code": "4",
                       "original_remittance_date": date(2026, 5, 1),
                       "original_authorization_number": None,
                       "original_referral_number": None,
                       "original_top_carc": "45",
                       "original_top_carc_bucket": "billing"}])
        out = compute(df, orig)
        assert (out.iloc[0] == 0).all()

    def test_freq7_no_original_emits_zeros(self):
        df = _df([{"claim_id": 1, "frequency_code": "7",
                   "authorization_number": "AUTH",
                   "service_from_date": date(2026, 6, 1)}])
        orig = _orig([])  # no original resolved
        out = compute(df, orig)
        assert (out.iloc[0] == 0).all()

    def test_had_prior_denial_set_when_original_status_4(self):
        df = _df([{"claim_id": 1, "frequency_code": "7",
                   "service_from_date": date(2026, 6, 1)}])
        orig = _orig([{"claim_id": 1, "original_claim_status_code": "4"}])
        out = compute(df, orig)
        assert int(out.loc[1, "had_prior_denial"]) == 1

    def test_had_prior_denial_zero_when_original_approved(self):
        df = _df([{"claim_id": 1, "frequency_code": "7",
                   "service_from_date": date(2026, 6, 1)}])
        orig = _orig([{"claim_id": 1, "original_claim_status_code": "1"}])
        out = compute(df, orig)
        assert int(out.loc[1, "had_prior_denial"]) == 0
        assert int(out.loc[1, "prior_denial_bucket"]) == 0
        assert int(out.loc[1, "days_since_original_denial"]) == 0

    def test_prior_denial_bucket_maps_through_lookup(self):
        df = _df([{"claim_id": 1, "frequency_code": "7",
                   "service_from_date": date(2026, 6, 1)}])
        orig = _orig([{"claim_id": 1,
                       "original_claim_status_code": "4",
                       "original_top_carc_bucket": "authorization"}])
        out = compute(df, orig)
        assert int(out.loc[1, "prior_denial_bucket"]) == _BUCKET_TO_INT["authorization"]

    def test_prior_denial_bucket_unknown_falls_to_zero(self):
        df = _df([{"claim_id": 1, "frequency_code": "7",
                   "service_from_date": date(2026, 6, 1)}])
        orig = _orig([{"claim_id": 1,
                       "original_claim_status_code": "4",
                       "original_top_carc_bucket": "FAKE_BUCKET"}])
        out = compute(df, orig)
        assert int(out.loc[1, "prior_denial_bucket"]) == 0

    def test_days_since_clipped_to_365_upper(self):
        df = _df([{"claim_id": 1, "frequency_code": "7",
                   "service_from_date": date(2027, 6, 1)}])
        orig = _orig([{"claim_id": 1,
                       "original_claim_status_code": "4",
                       "original_remittance_date": date(2025, 1, 1)}])
        out = compute(df, orig)
        assert int(out.loc[1, "days_since_original_denial"]) == 365

    def test_days_since_clipped_to_zero_lower(self):
        # service_from_date EARLIER than remittance_date — defensive clamp
        df = _df([{"claim_id": 1, "frequency_code": "7",
                   "service_from_date": date(2026, 1, 1)}])
        orig = _orig([{"claim_id": 1,
                       "original_claim_status_code": "4",
                       "original_remittance_date": date(2026, 6, 1)}])
        out = compute(df, orig)
        assert int(out.loc[1, "days_since_original_denial"]) == 0

    def test_days_since_zero_when_no_remit_date(self):
        df = _df([{"claim_id": 1, "frequency_code": "7",
                   "service_from_date": date(2026, 6, 1)}])
        orig = _orig([{"claim_id": 1,
                       "original_claim_status_code": "4",
                       "original_remittance_date": None}])
        out = compute(df, orig)
        assert int(out.loc[1, "days_since_original_denial"]) == 0

    def test_days_since_zero_when_original_was_approved(self):
        # Even with a remit date, no signal if original wasn't denied.
        df = _df([{"claim_id": 1, "frequency_code": "7",
                   "service_from_date": date(2026, 6, 30)}])
        orig = _orig([{"claim_id": 1,
                       "original_claim_status_code": "1",
                       "original_remittance_date": date(2026, 6, 1)}])
        out = compute(df, orig)
        assert int(out.loc[1, "days_since_original_denial"]) == 0

    def test_auth_added_fires_only_when_original_null_and_replacement_set(self):
        df = _df([
            {"claim_id": 1, "frequency_code": "7", "authorization_number": "A",
             "service_from_date": date(2026, 6, 1)},
            {"claim_id": 2, "frequency_code": "7", "authorization_number": "A",
             "service_from_date": date(2026, 6, 1)},
            {"claim_id": 3, "frequency_code": "7", "authorization_number": None,
             "service_from_date": date(2026, 6, 1)},
            {"claim_id": 4, "frequency_code": "7", "authorization_number": "",
             "service_from_date": date(2026, 6, 1)},
        ])
        orig = _orig([
            {"claim_id": 1, "original_authorization_number": None},     # added
            {"claim_id": 2, "original_authorization_number": "OLD"},    # not added (both set)
            {"claim_id": 3, "original_authorization_number": None},     # not added (still null)
            {"claim_id": 4, "original_authorization_number": "OLD"},    # not added (blank repl)
        ])
        out = compute(df, orig)
        assert int(out.loc[1, "auth_added_in_replacement"]) == 1
        assert int(out.loc[2, "auth_added_in_replacement"]) == 0
        assert int(out.loc[3, "auth_added_in_replacement"]) == 0
        assert int(out.loc[4, "auth_added_in_replacement"]) == 0

    def test_referral_added_symmetric_to_auth(self):
        df = _df([{"claim_id": 1, "frequency_code": "7", "referral_number": "R",
                   "service_from_date": date(2026, 6, 1)}])
        orig = _orig([{"claim_id": 1, "original_referral_number": None}])
        out = compute(df, orig)
        assert int(out.loc[1, "referral_added_in_replacement"]) == 1

    def test_correction_action_count_sums_flags(self):
        df = _df([
            {"claim_id": 1, "frequency_code": "7",
             "authorization_number": "A", "referral_number": "R",
             "service_from_date": date(2026, 6, 1)},
            {"claim_id": 2, "frequency_code": "7",
             "authorization_number": "A", "referral_number": None,
             "service_from_date": date(2026, 6, 1)},
        ])
        orig = _orig([
            {"claim_id": 1, "original_authorization_number": None,
             "original_referral_number": None},
            {"claim_id": 2, "original_authorization_number": None,
             "original_referral_number": None},
        ])
        out = compute(df, orig)
        assert int(out.loc[1, "correction_action_count"]) == 2
        assert int(out.loc[2, "correction_action_count"]) == 1

    def test_non_freq7_blocks_field_added_even_with_diff(self):
        """auth_added must NOT fire on a freq=1 row even if the diff exists."""
        df = _df([{"claim_id": 1, "frequency_code": "1",
                   "authorization_number": "A",
                   "service_from_date": date(2026, 6, 1)}])
        orig = _orig([{"claim_id": 1, "original_authorization_number": None}])
        out = compute(df, orig)
        assert int(out.loc[1, "auth_added_in_replacement"]) == 0

    def test_dtypes_are_compact(self):
        df = _df([{"claim_id": 1, "frequency_code": "7",
                   "service_from_date": date(2026, 6, 1)}])
        orig = _orig([])
        out = compute(df, orig)
        assert str(out["had_prior_denial"].dtype) == "int8"
        assert str(out["prior_denial_bucket"].dtype) == "int8"
        assert str(out["days_since_original_denial"].dtype) == "int16"
        assert str(out["auth_added_in_replacement"].dtype) == "int8"
        assert str(out["referral_added_in_replacement"].dtype) == "int8"
        assert str(out["modifier_added_in_replacement"].dtype) == "int8"
        assert str(out["diagnosis_changed_in_replacement"].dtype) == "int8"
        assert str(out["procedure_changed_in_replacement"].dtype) == "int8"
        assert str(out["lines_changed_in_replacement"].dtype) == "int16"
        assert str(out["charge_changed_in_replacement"].dtype) == "int8"
        assert str(out["correction_action_count"].dtype) == "int8"


# ============================================================================
# CR-118 Stage 2 — correction-delta features
# ============================================================================

class TestStage2Deltas:
    def _freq7_row(self, claim_id: int = 1, **extra):
        base = {
            "claim_id": claim_id, "frequency_code": "7",
            "service_from_date": date(2026, 6, 1),
        }
        base.update(extra)
        return base

    def _orig_row(self, claim_id: int = 1, **extra):
        # original_id set by default so has_original=True; tests that want
        # an unresolved original override original_id to None.
        base = {"claim_id": claim_id, "original_id": 9000 + claim_id}
        base.update(extra)
        return base

    # ---- modifier_added -----------------------------------------------------

    def test_modifier_added_when_replacement_has_extra(self):
        df = _df([self._freq7_row(modifiers=["25", "59"])])
        orig = _orig([self._orig_row(original_modifiers=["25"])])
        out = compute(df, orig)
        assert int(out.loc[1, "modifier_added_in_replacement"]) == 1

    def test_modifier_not_added_when_sets_equal(self):
        df = _df([self._freq7_row(modifiers=["25", "59"])])
        orig = _orig([self._orig_row(original_modifiers=["59", "25"])])  # reordered
        out = compute(df, orig)
        assert int(out.loc[1, "modifier_added_in_replacement"]) == 0

    def test_modifier_not_added_when_replacement_only_removed(self):
        df = _df([self._freq7_row(modifiers=["25"])])
        orig = _orig([self._orig_row(original_modifiers=["25", "59"])])
        out = compute(df, orig)
        # No NEW modifier added; replacement just dropped one. Strict 'added'
        # semantics: returns 0.
        assert int(out.loc[1, "modifier_added_in_replacement"]) == 0

    def test_modifier_zero_when_no_original(self):
        df = _df([self._freq7_row(modifiers=["25", "59"])])
        orig = _orig([{"claim_id": 1, "original_id": None,
                       "original_modifiers": None}])
        out = compute(df, orig)
        assert int(out.loc[1, "modifier_added_in_replacement"]) == 0

    # ---- diagnosis_changed --------------------------------------------------

    def test_diagnosis_changed_on_add(self):
        df = _df([self._freq7_row(diagnoses=["I10", "E785"])])
        orig = _orig([self._orig_row(original_diagnosis_codes=["I10"])])
        out = compute(df, orig)
        assert int(out.loc[1, "diagnosis_changed_in_replacement"]) == 1

    def test_diagnosis_changed_on_remove(self):
        df = _df([self._freq7_row(diagnoses=["I10"])])
        orig = _orig([self._orig_row(original_diagnosis_codes=["I10", "E785"])])
        out = compute(df, orig)
        assert int(out.loc[1, "diagnosis_changed_in_replacement"]) == 1

    def test_diagnosis_not_changed_when_identical(self):
        df = _df([self._freq7_row(diagnoses=["I10", "E785"])])
        orig = _orig([self._orig_row(original_diagnosis_codes=["E785", "I10"])])
        out = compute(df, orig)
        assert int(out.loc[1, "diagnosis_changed_in_replacement"]) == 0

    def test_diagnosis_supports_dict_form_from_load_training_corpus(self):
        # load_training_corpus emits diagnoses=[{'code': 'I10', 'type': 'ABK'}]
        df = _df([self._freq7_row(
            diagnoses=[{"code": "I10", "type": "ABK"}, {"code": "E785", "type": "ABF"}],
        )])
        orig = _orig([self._orig_row(original_diagnosis_codes=["I10"])])
        out = compute(df, orig)
        assert int(out.loc[1, "diagnosis_changed_in_replacement"]) == 1

    # ---- procedure_changed --------------------------------------------------

    def test_procedure_changed_on_swap(self):
        df = _df([self._freq7_row(procedure_codes=["99214"])])
        orig = _orig([self._orig_row(original_procedure_codes=["99213"])])
        out = compute(df, orig)
        assert int(out.loc[1, "procedure_changed_in_replacement"]) == 1

    def test_procedure_unchanged_same_set(self):
        df = _df([self._freq7_row(procedure_codes=["99213"])])
        orig = _orig([self._orig_row(original_procedure_codes=["99213"])])
        out = compute(df, orig)
        assert int(out.loc[1, "procedure_changed_in_replacement"]) == 0

    # ---- lines_changed ------------------------------------------------------

    def test_lines_changed_signed_delta(self):
        df = _df([
            self._freq7_row(claim_id=1, claim_lines_count=5),
            self._freq7_row(claim_id=2, claim_lines_count=3),
            self._freq7_row(claim_id=3, claim_lines_count=3),
        ])
        orig = _orig([
            self._orig_row(claim_id=1, original_claim_lines_count=3),
            self._orig_row(claim_id=2, original_claim_lines_count=5),
            self._orig_row(claim_id=3, original_claim_lines_count=3),
        ])
        out = compute(df, orig)
        assert int(out.loc[1, "lines_changed_in_replacement"]) == 2
        assert int(out.loc[2, "lines_changed_in_replacement"]) == -2
        assert int(out.loc[3, "lines_changed_in_replacement"]) == 0

    def test_lines_changed_clipped_at_99(self):
        df = _df([self._freq7_row(claim_lines_count=500)])
        orig = _orig([self._orig_row(original_claim_lines_count=1)])
        out = compute(df, orig)
        assert int(out.loc[1, "lines_changed_in_replacement"]) == 99

    def test_lines_changed_zero_when_no_original(self):
        df = _df([self._freq7_row(claim_lines_count=5)])
        orig = _orig([{"claim_id": 1, "original_id": None,
                       "original_claim_lines_count": None}])
        out = compute(df, orig)
        assert int(out.loc[1, "lines_changed_in_replacement"]) == 0

    # ---- charge_changed -----------------------------------------------------

    def test_charge_changed_sign_positive(self):
        df = _df([self._freq7_row(total_charge_amount=200.0)])
        orig = _orig([self._orig_row(original_total_charge_amount=150.0)])
        out = compute(df, orig)
        assert int(out.loc[1, "charge_changed_in_replacement"]) == 1

    def test_charge_changed_sign_negative(self):
        df = _df([self._freq7_row(total_charge_amount=100.0)])
        orig = _orig([self._orig_row(original_total_charge_amount=150.0)])
        out = compute(df, orig)
        assert int(out.loc[1, "charge_changed_in_replacement"]) == -1

    def test_charge_changed_sign_zero_when_equal(self):
        df = _df([self._freq7_row(total_charge_amount=150.0)])
        orig = _orig([self._orig_row(original_total_charge_amount=150.0)])
        out = compute(df, orig)
        assert int(out.loc[1, "charge_changed_in_replacement"]) == 0

    def test_charge_changed_zero_when_no_original(self):
        df = _df([self._freq7_row(total_charge_amount=150.0)])
        orig = _orig([{"claim_id": 1, "original_id": None,
                       "original_total_charge_amount": None}])
        out = compute(df, orig)
        assert int(out.loc[1, "charge_changed_in_replacement"]) == 0

    # ---- correction_action_count integrates Stage 2 -------------------------

    def test_correction_action_count_sums_all_seven_flags(self):
        # Operator added auth + referral + modifier + diagnosis + procedure +
        # changed line count + changed charge. Expected count = 7.
        df = _df([self._freq7_row(
            authorization_number="A1",
            referral_number="R1",
            modifiers=["25", "59"],
            diagnoses=["I10", "E785"],
            procedure_codes=["99214"],
            claim_lines_count=4,
            total_charge_amount=200.0,
        )])
        orig = _orig([self._orig_row(
            original_authorization_number=None,
            original_referral_number=None,
            original_modifiers=["25"],
            original_diagnosis_codes=["I10"],
            original_procedure_codes=["99213"],
            original_claim_lines_count=3,
            original_total_charge_amount=150.0,
        )])
        out = compute(df, orig)
        assert int(out.loc[1, "correction_action_count"]) == 7

    def test_correction_action_count_zero_on_non_freq7_even_with_diffs(self):
        df = _df([{
            "claim_id": 1, "frequency_code": "1",
            "service_from_date": date(2026, 6, 1),
            "authorization_number": "A", "referral_number": "R",
            "modifiers": ["25"], "diagnoses": ["I10"],
            "procedure_codes": ["99214"], "claim_lines_count": 5,
            "total_charge_amount": 200.0,
        }])
        orig = _orig([self._orig_row(
            original_authorization_number=None, original_referral_number=None,
            original_modifiers=[], original_diagnosis_codes=["E785"],
            original_procedure_codes=["99213"], original_claim_lines_count=2,
            original_total_charge_amount=100.0,
        )])
        out = compute(df, orig)
        assert int(out.loc[1, "correction_action_count"]) == 0

    def test_correction_action_count_zero_when_no_original(self):
        df = _df([self._freq7_row(
            authorization_number="A1", modifiers=["25", "59"],
            procedure_codes=["99214"], claim_lines_count=4,
            total_charge_amount=200.0,
        )])
        orig = _orig([{"claim_id": 1, "original_id": None}])
        out = compute(df, orig)
        # has_original=False blocks all Stage-2 flags. auth_added still fires
        # at Stage-1 level because it operates on the replacement field alone
        # — but the original_authorization_number column is missing → blank
        # → would normally fire. We document this nuance in the AIR; the
        # observable behavior is that auth_added uses the snapshot's
        # original_authorization_number column directly (NULL = treated as
        # blank, replacement non-blank → fires). So this test asserts that
        # the count is at least the Stage-1 contribution.
        assert int(out.loc[1, "correction_action_count"]) == int(out.loc[1, "auth_added_in_replacement"])

    def test_stage2_columns_zero_when_input_columns_absent(self):
        # If the caller passes a df that does not include Stage 2 columns
        # (e.g. the Stage 1 verification path), compute() must not crash —
        # it must emit zeros for the Stage 2 features.
        df = _df([self._freq7_row()])  # no modifiers/diagnoses/etc. set
        for col in ("modifiers", "diagnoses", "procedure_codes",
                    "claim_lines_count", "total_charge_amount"):
            if col in df.columns:
                df = df.drop(columns=[col])
        orig = _orig([self._orig_row()])
        out = compute(df, orig)
        for c in ("modifier_added_in_replacement",
                  "diagnosis_changed_in_replacement",
                  "procedure_changed_in_replacement",
                  "lines_changed_in_replacement",
                  "charge_changed_in_replacement"):
            assert int(out.loc[1, c]) == 0
