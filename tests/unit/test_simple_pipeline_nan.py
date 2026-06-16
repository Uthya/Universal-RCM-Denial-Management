"""CR-060 regression tests for _safe_str_or_none coercion in simple_pipeline.

Reproduces the NaN-from-LEFT-JOIN bug: a claim with NULL payer_id round-trips
through pandas as ``float('nan')`` in the payer_name column. ScoredClaim is
constructed from that row and then serialized through HighRiskClaimItem,
which is typed ``str | None`` — Pydantic accepts None but not NaN.

These tests are pure-Python; no DB required.
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from rcm.ml.simple_pipeline import ScoredClaim, _safe_str_or_none
from rcm.schemas.public import HighRiskClaimItem


class TestSafeStrOrNone:
    def test_none_passes_through(self):
        assert _safe_str_or_none(None) is None

    def test_nan_becomes_none(self):
        assert _safe_str_or_none(float("nan")) is None
        assert _safe_str_or_none(math.nan) is None

    def test_empty_string_becomes_none(self):
        assert _safe_str_or_none("") is None

    def test_whitespace_becomes_none(self):
        assert _safe_str_or_none("   ") is None
        assert _safe_str_or_none("\t\n") is None

    def test_valid_string_preserved(self):
        assert _safe_str_or_none("AETNA") == "AETNA"

    def test_whitespace_trimmed(self):
        assert _safe_str_or_none("  AETNA  ") == "AETNA"

    def test_non_string_coerced(self):
        assert _safe_str_or_none(42) == "42"

    def test_pandas_nat_handling(self):
        # pd.NA / NaT also propagate as falsy floats in some columns
        # _safe_str_or_none should not raise on these inputs
        # (and should yield None for representative missing markers).
        assert _safe_str_or_none(pd.NA) is None or _safe_str_or_none(pd.NA) == "<NA>"
        # NaT becomes 'NaT' as a string; we accept that — not a regression target.


class TestScoredClaimWithNaNPayer:
    """End-to-end: build a ScoredClaim from a pandas row with NaN payer_name
    and verify it serializes through HighRiskClaimItem (no Pydantic ValidationError)."""

    def _build_row(self, payer_name=float("nan")):
        # Mirror the columns the production query produces (see simple_pipeline.py
        # line 100+). Only the fields ScoredClaim reads need to be present here.
        return pd.Series({
            "claim_id": 12345,
            "claim_number": "TEST-001",
            "payer_name": payer_name,
            "service_variant": "837P",
            "claim_subtype": "healthcare",
        })

    def test_nan_payer_serializes_as_null(self):
        row = self._build_row(payer_name=float("nan"))
        scored = ScoredClaim(
            claim_id=int(row["claim_id"]),
            claim_number=str(row["claim_number"]),
            payer_name=_safe_str_or_none(row.get("payer_name")),
            service_variant=_safe_str_or_none(row.get("service_variant")) or "",
            claim_subtype=_safe_str_or_none(row.get("claim_subtype")) or "",
            risk_score=0.85,
            risk_level="HIGH",
            top_denial_reasons=[],
        )
        item = HighRiskClaimItem(
            claim_id=scored.claim_id,
            claim_number=scored.claim_number,
            payer_name=scored.payer_name,
            service_variant=scored.service_variant,
            claim_subtype=scored.claim_subtype,
            risk_score=scored.risk_score,
            risk_level=scored.risk_level,
            top_denial_reasons=scored.top_denial_reasons,
        )
        # The whole point — Pydantic accepted None, not NaN
        assert item.payer_name is None
        assert item.service_variant == "837P"
        assert item.claim_subtype == "healthcare"

    def test_valid_payer_preserved(self):
        row = self._build_row(payer_name="AETNA")
        scored = ScoredClaim(
            claim_id=int(row["claim_id"]),
            claim_number=str(row["claim_number"]),
            payer_name=_safe_str_or_none(row.get("payer_name")),
            service_variant=_safe_str_or_none(row.get("service_variant")) or "",
            claim_subtype=_safe_str_or_none(row.get("claim_subtype")) or "",
            risk_score=0.85,
            risk_level="HIGH",
            top_denial_reasons=[],
        )
        item = HighRiskClaimItem(
            claim_id=scored.claim_id,
            claim_number=scored.claim_number,
            payer_name=scored.payer_name,
            service_variant=scored.service_variant,
            claim_subtype=scored.claim_subtype,
            risk_score=scored.risk_score,
            risk_level=scored.risk_level,
            top_denial_reasons=scored.top_denial_reasons,
        )
        assert item.payer_name == "AETNA"

    def test_all_optional_string_fields_handle_nan(self):
        """Defensive — service_variant and claim_subtype are NOT NULL in the
        DB, but if a future regression caused them to be NaN, the helper +
        the `or ""` fallback at the construction site must still keep the
        endpoint from 500-ing."""
        scored = ScoredClaim(
            claim_id=1,
            claim_number="X",
            payer_name=_safe_str_or_none(float("nan")),
            service_variant=_safe_str_or_none(float("nan")) or "",
            claim_subtype=_safe_str_or_none(float("nan")) or "",
            risk_score=0.5,
            risk_level="MEDIUM",
            top_denial_reasons=[],
        )
        # Pydantic must accept this without ValidationError
        item = HighRiskClaimItem(
            claim_id=scored.claim_id,
            claim_number=scored.claim_number,
            payer_name=scored.payer_name,
            service_variant=scored.service_variant,
            claim_subtype=scored.claim_subtype,
            risk_score=scored.risk_score,
            risk_level=scored.risk_level,
            top_denial_reasons=scored.top_denial_reasons,
        )
        assert item.payer_name is None
        # "" is an acceptable str under the `str | None` schema. The contract
        # is "doesn't 500"; UI can render "" however it likes.
        assert item.service_variant == ""
        assert item.claim_subtype == ""

    def test_raw_nan_into_pydantic_still_fails_without_helper(self):
        """Negative control — proves the helper is necessary. Without it,
        Pydantic raises ValidationError on float('nan')."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="string_type"):
            HighRiskClaimItem(
                claim_id=1,
                claim_number="X",
                payer_name=float("nan"),     # the bug
                service_variant="837P",
                claim_subtype="healthcare",
                risk_score=0.5,
                risk_level="MEDIUM",
                top_denial_reasons=[],
            )
