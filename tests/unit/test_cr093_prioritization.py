"""CR-093 — actionability-tier prioritization of denial reasons.

Guards:
  1. When the input contains a Tier-A (directly actionable) reason, the
     FIRST-displayed reason is from Tier-A, regardless of how much larger
     a Tier-B/C reason's SHAP impact is.
  2. Within a tier, ordering remains impact DESC.
  3. Ordering is deterministic: same input -> same output.
  4. All input reasons (positive impact) survive; only the order changes.
  5. Every bucket slug in REASON_BUCKETS has an entry in
     _ACTIONABILITY_TIER (no slug silently falls to tier 2).
"""

from __future__ import annotations

from rcm.ml.reason_renderer import (
    REASON_BUCKETS,
    _ACTIONABILITY_TIER,
    render_risk_factors,
)


# All 11 canonical slugs
_ALL_SLUGS = {b[0] for b in REASON_BUCKETS}


def test_actionability_tier_covers_every_bucket() -> None:
    """No bucket slug may fall through to the default tier — the dict must
    enumerate all 11 canonical buckets explicitly."""
    missing = _ALL_SLUGS - set(_ACTIONABILITY_TIER.keys())
    assert not missing, (
        f"_ACTIONABILITY_TIER is missing entries for: {sorted(missing)}. "
        f"Every bucket in REASON_BUCKETS must be tier-classified."
    )


def test_three_tiers_present() -> None:
    """Sanity: there must be at least one bucket in each of the 3 tiers."""
    tiers = set(_ACTIONABILITY_TIER.values())
    assert tiers == {0, 1, 2}, f"expected tiers {{0,1,2}}, got {tiers}"


def test_tier_A_promoted_over_tier_C_even_with_lower_impact() -> None:
    """The headline CR-093 invariant: a tier-A reason with smaller SHAP
    impact must surface above a tier-C reason with larger SHAP impact."""
    factors = [
        # Tier-C with HUGE impact (similar to the 'similar' bucket dominating)
        {"feature": "payer_cpt_denial_rate", "impact": 5.000},
        # Tier-A with TINY impact (a missing auth-feature signal)
        {"feature": "has_prior_authorization", "impact": 0.050},
    ]
    rows = render_risk_factors(factors, top_k=5)
    assert rows[0]["feature"] == "authorization", (
        f"Tier-A 'authorization' must come first; got {rows[0]['feature']!r}"
    )
    assert rows[1]["feature"] == "similar"
    # Numeric impacts must be preserved on the rows so callers can still see
    # the raw SHAP magnitude.
    assert rows[0]["impact"] == 0.05
    assert rows[1]["impact"] == 5.0


def test_within_tier_sort_remains_impact_desc() -> None:
    """Within the same tier, ordering is still impact DESC."""
    factors = [
        # Three tier-A reasons with different impacts
        {"feature": "has_prior_authorization", "impact": 0.1},   # authorization
        {"feature": "missing_diagnosis",        "impact": 0.5},   # diagnosis
        {"feature": "has_paperwork_attachment", "impact": 0.3},   # documentation
    ]
    rows = render_risk_factors(factors, top_k=5)
    impacts = [r["impact"] for r in rows]
    assert impacts == sorted(impacts, reverse=True), (
        f"within-tier ordering broken: {impacts}"
    )
    # Specifically: diagnosis (0.5) > documentation (0.3) > authorization (0.1)
    assert [r["feature"] for r in rows] == ["diagnosis", "documentation", "authorization"]


def test_tier_B_above_tier_C() -> None:
    """When no tier-A reason is present, tier-B should still beat tier-C
    even if tier-C has higher impact."""
    factors = [
        {"feature": "payer_cpt_denial_rate", "impact": 2.000},  # similar (C)
        {"feature": "total_charge_amount",   "impact": 0.100},  # billing (B)
    ]
    rows = render_risk_factors(factors, top_k=5)
    assert rows[0]["feature"] == "billing"
    assert rows[1]["feature"] == "similar"


def test_only_tier_C_reasons_still_ordered_by_impact() -> None:
    """If the only positive-impact reasons are tier-C, ordering is impact DESC
    among them (no tier-A or tier-B to promote)."""
    factors = [
        {"feature": "same_day_visits_for_patient", "impact": 0.20},  # history
        {"feature": "payer_cpt_denial_rate",       "impact": 0.50},  # similar
    ]
    rows = render_risk_factors(factors, top_k=5)
    assert rows[0]["feature"] == "similar"  # higher impact among the C's
    assert rows[1]["feature"] == "history"


def test_deterministic_ordering_across_calls() -> None:
    """Same input -> same output, byte-identical."""
    factors = [
        {"feature": "payer_cpt_denial_rate",     "impact": 0.9},
        {"feature": "has_prior_authorization",   "impact": 0.3},
        {"feature": "missing_diagnosis",          "impact": 0.2},
        {"feature": "total_charge_amount",        "impact": 1.5},
        {"feature": "same_day_visits_for_patient","impact": 0.8},
    ]
    a = render_risk_factors(factors, top_k=5)
    b = render_risk_factors(factors, top_k=5)
    c = render_risk_factors(factors, top_k=5)
    assert a == b == c, "render_risk_factors must be deterministic"


def test_no_reasons_dropped_only_reordered() -> None:
    """Every bucket present in the (positive-impact) input must appear in the
    output (top_k permitting). Only the order changes; no information loss."""
    factors = [
        {"feature": "payer_cpt_denial_rate",     "impact": 0.9},   # similar
        {"feature": "has_prior_authorization",   "impact": 0.3},   # authorization
        {"feature": "missing_diagnosis",          "impact": 0.2},   # diagnosis
        {"feature": "total_charge_amount",        "impact": 1.5},   # billing
        {"feature": "same_day_visits_for_patient","impact": 0.8},   # history
    ]
    rows = render_risk_factors(factors, top_k=10)
    seen_slugs = {r["feature"] for r in rows}
    assert seen_slugs == {"similar", "authorization", "diagnosis", "billing", "history"}


def test_negative_impact_still_filtered_unchanged() -> None:
    """CR-093 does NOT change the negative-impact filter — protective
    features are still excluded."""
    factors = [
        {"feature": "has_prior_authorization", "impact": -0.5},  # protective, filtered
        {"feature": "total_charge_amount",     "impact": +0.1},  # billing, surfaces
    ]
    rows = render_risk_factors(factors, top_k=5)
    assert len(rows) == 1
    assert rows[0]["feature"] == "billing"


def test_empty_input_safe() -> None:
    assert render_risk_factors([]) == []
    assert render_risk_factors(None) == []
