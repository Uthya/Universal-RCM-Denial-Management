"""CR-092 Issue 2 — renderer coverage + leakage prevention.

Guards two invariants:

  1. Every feature in ``rcm.features.registry.FEATURE_REGISTRY`` resolves to
     a non-general bucket via ``render_reason`` (i.e. it has an explicit
     entry in ``_FEATURE_TO_BUCKET`` — not the catch-all).
  2. ``render_reason`` for any *unknown* feature returns the GENERAL bucket
     (the fallback contract), and the returned slug NEVER equals the raw
     feature name.
  3. ``render_risk_factors`` for a mixed batch of known + unknown features
     produces ONLY bucket-slug ``feature`` fields — no raw feature name
     can leak even if the renderer drifts behind the registry.

If a new FB feature is added without a renderer mapping, test #1 fails.
If the renderer's fallback is broken, tests #2 and #3 catch it.
"""

from __future__ import annotations

import pytest

from rcm.features.registry import FEATURE_REGISTRY
from rcm.ml.reason_renderer import (
    REASON_BUCKETS,
    _FEATURE_TO_BUCKET,
    _GEN,
    render_reason,
    render_risk_factors,
)


# The 11 canonical bucket slugs the renderer is allowed to emit.
_VALID_SLUGS = {b[0] for b in REASON_BUCKETS}


class TestRendererCoverage:
    """Test #1 — every registered FB feature has an explicit bucket."""

    @pytest.mark.parametrize("feature_name", sorted(FEATURE_REGISTRY.keys()))
    def test_feature_has_explicit_bucket(self, feature_name: str) -> None:
        """Every name in FEATURE_REGISTRY must be in _FEATURE_TO_BUCKET so its
        bucket assignment is deliberate, not a fallback."""
        assert feature_name in _FEATURE_TO_BUCKET, (
            f"feature {feature_name!r} is in FEATURE_REGISTRY but missing from "
            f"reason_renderer._FEATURE_TO_BUCKET. Add an entry (or explicitly map "
            f"to _GEN if no specific bucket applies). Otherwise this feature's "
            f"bucket silently falls back to GENERAL, hiding important denial "
            f"context."
        )


class TestRendererFallback:
    """Tests #2 — unknown features must collapse to GENERAL; raw names never
    appear in the slug field even when no mapping exists."""

    def test_unknown_feature_collapses_to_general(self) -> None:
        slug, title, sentence = render_reason("unknown_feature_xyz")
        assert (slug, title, sentence) == _GEN, (
            f"unknown features must fall back to REASON_GENERAL; got "
            f"({slug!r}, {title!r}, {sentence!r})"
        )

    def test_unknown_feature_slug_is_general_not_raw(self) -> None:
        for raw_name in (
            "has_referral_unknown_variant",
            "is_replacement_freq",  # simple_pipeline internal column
            "payer_AETNA_one_hot",   # simple_pipeline-style one-hot
            "totally_made_up_xyz",
        ):
            slug, _, _ = render_reason(raw_name)
            assert slug != raw_name, (
                f"render_reason({raw_name!r}) returned slug={slug!r} — raw "
                f"feature name must NOT appear in the slug field"
            )
            assert slug in _VALID_SLUGS, (
                f"render_reason({raw_name!r}) returned slug={slug!r} which is "
                f"not in the canonical 11-bucket set"
            )

    def test_empty_feature_name_safe(self) -> None:
        slug, title, sentence = render_reason("")
        assert (slug, title, sentence) == _GEN


class TestRenderRiskFactorsNeverLeaks:
    """Test #3 — render_risk_factors output's ``feature`` field is ALWAYS a
    canonical bucket slug, even for an input batch full of raw + unknown names."""

    def test_mixed_known_and_unknown_features(self) -> None:
        factors = [
            # Known FB feature with a renderer mapping
            {"feature": "has_referral",        "impact": 0.5},
            # Unknown / leaked-style name (simple_pipeline internal)
            {"feature": "is_replacement_freq", "impact": 0.4},
            # Pure garbage
            {"feature": "unknown_feature_xyz", "impact": 0.3},
            # An FB feature that's variant-specific (home_care bucket override)
            {"feature": "total_units",         "impact": 0.6},
        ]
        rendered = render_risk_factors(factors, top_k=10, claim_subtype="home_care")
        for row in rendered:
            slug = row["feature"]
            assert slug in _VALID_SLUGS, (
                f"render_risk_factors emitted feature={slug!r} which is NOT a "
                f"canonical bucket slug. Raw feature names must never reach "
                f"the rendered output."
            )

    def test_simple_pipeline_one_hot_names_dont_leak(self) -> None:
        """Simple_pipeline emits one-hot columns like ``payer_X`` / ``cpt_99213``
        which are NOT in the registry. They must collapse to GENERAL."""
        factors = [
            {"feature": "payer_AETNA",     "impact": 0.4},
            {"feature": "payer_MULTIPLAN", "impact": 0.3},
            {"feature": "cpt_99213",       "impact": 0.5},
            {"feature": "dx_K0500",        "impact": 0.2},
        ]
        rendered = render_risk_factors(factors, top_k=10)
        for row in rendered:
            assert row["feature"] in _VALID_SLUGS

    def test_empty_input(self) -> None:
        assert render_risk_factors([]) == []
        assert render_risk_factors(None) == []
