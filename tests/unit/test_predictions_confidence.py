"""CR-131B — unit tests for the empirical denial forecast layer.

Replaces the CR-128/CR-128B confidence-label tests retired in CR-131B.
Covers:
  - CalibrationTable.lookup fallback ladder (bucket -> variant -> global -> model)
  - MIN_BUCKET_N (N>=30) guard
  - aggregate_forecast math (Poisson-Binomial expectation + 90% CI)
  - fallback_distribution accounting
  - empty cohort short-circuit

No real DB - synthetic CalibrationTable instances are constructed directly.
"""
from __future__ import annotations

import math

import pytest

from rcm.ml.forecast import (
    MIN_BUCKET_N,
    CalibrationRow,
    CalibrationTable,
    ClaimLookup,
    aggregate_forecast,
)


def _row(variant, subtype, lo, hi, n, d, p, plo=None, phi=None):
    return CalibrationRow(
        service_variant=variant, claim_subtype=subtype,
        bucket_lo=lo, bucket_hi=hi,
        n_adjudicated=n, n_denied=d,
        p_hat=p,
        p_hat_lo=plo if plo is not None else max(0.0, p - 0.05),
        p_hat_hi=phi if phi is not None else min(1.0, p + 0.05),
    )


def _make_table(bucket_rows=None, variant_rows=None, global_row=None):
    buckets = {}
    for r in bucket_rows or []:
        idx = int(r.bucket_lo * 10) if r.bucket_lo < 0.9999 else 9
        buckets[(r.service_variant, r.claim_subtype, idx)] = r
    variants = {}
    for r in variant_rows or []:
        variants[(r.service_variant, r.claim_subtype)] = r
    return CalibrationTable(buckets, variants, global_row, last_refresh="2026-06-25T03:00:00")


# ---------------------------------------------------------------------------
# Fallback ladder
# ---------------------------------------------------------------------------

class TestFallbackLadder:
    def test_bucket_hit_when_n_above_threshold(self):
        # Bucket row with N=100 (above the 30-row minimum) wins
        b = _row("837P", "healthcare", 0.9, 1.0, 100, 82, 0.82)
        v = _row("837P", "healthcare", None, None, 1000, 750, 0.75)
        g = _row(None, None, None, None, 5000, 2500, 0.50)
        t = _make_table([b], [v], g)
        L = t.lookup(0.97, "837P", "healthcare")
        assert L.fallback_used == "bucket"
        assert L.p_hat == 0.82
        assert L.sample_size == 100

    def test_falls_back_to_variant_when_bucket_thin(self):
        # Bucket exists but has only 5 rows (< MIN_BUCKET_N)
        b = _row("837P", "healthcare", 0.9, 1.0, 5, 4, 0.80)
        v = _row("837P", "healthcare", None, None, 1000, 750, 0.75)
        t = _make_table([b], [v], None)
        L = t.lookup(0.97, "837P", "healthcare")
        assert L.fallback_used == "variant"
        assert L.p_hat == 0.75
        assert L.sample_size == 1000

    def test_falls_back_to_global_when_no_variant(self):
        # Bucket missing AND variant missing -> global row used
        g = _row(None, None, None, None, 5000, 2500, 0.50)
        t = _make_table([], [], g)
        L = t.lookup(0.97, "837P", "healthcare")
        assert L.fallback_used == "global"
        assert L.p_hat == 0.50

    def test_falls_back_to_model_when_calibration_empty(self):
        # No rows at all - last resort returns the raw risk_score
        t = _make_table([], [], None)
        L = t.lookup(0.85, "837P", "healthcare")
        assert L.fallback_used == "model"
        assert L.p_hat == 0.85
        assert L.sample_size == 0

    def test_global_skipped_when_thin(self):
        # Global has only 5 adjudicated rows -> falls all the way to model
        g = _row(None, None, None, None, 5, 3, 0.6)
        t = _make_table([], [], g)
        L = t.lookup(0.7, "837P", "healthcare")
        assert L.fallback_used == "model"
        assert L.p_hat == 0.7

    def test_unknown_variant_uses_global(self):
        v = _row("837P", "healthcare", None, None, 1000, 750, 0.75)
        g = _row(None, None, None, None, 5000, 2500, 0.50)
        t = _make_table([], [v], g)
        # Different variant - variant cell doesn't match, fall to global
        L = t.lookup(0.97, "837Q", "healthcare")
        assert L.fallback_used == "global"

    def test_bucket_idx_top_boundary(self):
        # p=1.0 must land in bucket index 9, not raise / not 10
        b = _row("837I", "home_care", 0.9, 1.0, 50, 49, 0.98)
        v = _row("837I", "home_care", None, None, 500, 350, 0.70)
        t = _make_table([b], [v], None)
        L = t.lookup(1.0, "837I", "home_care")
        assert L.fallback_used == "bucket"
        assert L.bucket_lo == 0.9

    def test_min_bucket_n_boundary_is_inclusive(self):
        # N == MIN_BUCKET_N should be USED (not fall back)
        b = _row("837I", "home_care", 0.9, 1.0, MIN_BUCKET_N, 25, 0.83)
        v = _row("837I", "home_care", None, None, 500, 350, 0.70)
        t = _make_table([b], [v], None)
        L = t.lookup(0.95, "837I", "home_care")
        assert L.fallback_used == "bucket"


# ---------------------------------------------------------------------------
# aggregate_forecast math
# ---------------------------------------------------------------------------

class TestAggregateForecast:
    def test_empty_cohort(self):
        f = aggregate_forecast([])
        assert f.n_high == 0
        assert f.expected_denials == 0.0
        assert f.expected_approvals == 0.0
        assert f.interval_low == 0.0
        assert f.interval_high == 0.0
        assert f.fallback_distribution == {"bucket": 0.0, "variant": 0.0, "global": 0.0, "model": 0.0}

    def test_single_claim_certain(self):
        L = ClaimLookup(p_hat=1.0, sample_size=100, fallback_used="bucket",
                        bucket_lo=0.9, bucket_hi=1.0)
        f = aggregate_forecast([L])
        assert f.n_high == 1
        assert f.expected_denials == 1.0
        assert f.expected_approvals == 0.0
        assert f.forecast_confidence_pct == 1.0
        assert f.interval_low == 1.0
        assert f.interval_high == 1.0

    def test_single_claim_max_uncertainty(self):
        L = ClaimLookup(p_hat=0.5, sample_size=50, fallback_used="bucket",
                        bucket_lo=0.4, bucket_hi=0.5)
        f = aggregate_forecast([L])
        # std = sqrt(0.5*0.5) = 0.5, 90% CI = [0.5 - 1.645*0.5, 0.5 + 1.645*0.5]
        assert f.expected_denials == 0.5
        # Interval clips to [0, 1]
        assert f.interval_low == 0.0
        assert f.interval_high == 1.0

    def test_ten_claims_at_85pct(self):
        # 10 claims, each p=0.85 -> expected=8.5, var=10*0.85*0.15=1.275, std=1.13
        Ls = [ClaimLookup(p_hat=0.85, sample_size=200, fallback_used="bucket",
                          bucket_lo=0.8, bucket_hi=0.9)] * 10
        f = aggregate_forecast(Ls)
        assert f.n_high == 10
        assert f.expected_denials == 8.5
        # 90% CI: [8.5 - 1.645*1.13, 8.5 + 1.645*1.13] = ~[6.64, 10] (clipped)
        assert 6.0 < f.interval_low < 7.0
        assert f.interval_high == 10.0  # clipped to N
        assert f.forecast_confidence_pct == 0.85

    def test_mixed_probabilities_linearity(self):
        ps = [0.9, 0.7, 0.5, 0.3, 0.1]
        Ls = [ClaimLookup(p_hat=p, sample_size=100, fallback_used="bucket",
                          bucket_lo=None, bucket_hi=None) for p in ps]
        f = aggregate_forecast(Ls)
        # E = 2.5 regardless of distribution
        assert f.expected_denials == 2.5
        assert f.forecast_confidence_pct == 0.5

    def test_fallback_distribution_accounting(self):
        Ls = [
            ClaimLookup(p_hat=0.9, sample_size=100, fallback_used="bucket", bucket_lo=None, bucket_hi=None),
            ClaimLookup(p_hat=0.9, sample_size=100, fallback_used="bucket", bucket_lo=None, bucket_hi=None),
            ClaimLookup(p_hat=0.7, sample_size=500, fallback_used="variant", bucket_lo=None, bucket_hi=None),
            ClaimLookup(p_hat=0.5, sample_size=5000, fallback_used="global", bucket_lo=None, bucket_hi=None),
        ]
        f = aggregate_forecast(Ls)
        assert f.fallback_distribution["bucket"] == 0.5
        assert f.fallback_distribution["variant"] == 0.25
        assert f.fallback_distribution["global"] == 0.25
        assert f.fallback_distribution["model"] == 0.0

    def test_historical_sample_size_avg(self):
        Ls = [
            ClaimLookup(p_hat=0.9, sample_size=100, fallback_used="bucket", bucket_lo=None, bucket_hi=None),
            ClaimLookup(p_hat=0.9, sample_size=200, fallback_used="bucket", bucket_lo=None, bucket_hi=None),
        ]
        f = aggregate_forecast(Ls)
        assert f.historical_sample_size == 150

    def test_last_refresh_passthrough(self):
        Ls = [ClaimLookup(p_hat=0.5, sample_size=100, fallback_used="bucket",
                          bucket_lo=None, bucket_hi=None)]
        f = aggregate_forecast(Ls, last_refresh="2026-06-25T03:00:00")
        assert f.last_calibration_refresh == "2026-06-25T03:00:00"

    def test_interval_clipping(self):
        # Two claims at p=0.99 -> E=1.98, std=sqrt(0.0198)~0.14, upper=2.21 clipped to 2.0
        Ls = [ClaimLookup(p_hat=0.99, sample_size=100, fallback_used="bucket",
                          bucket_lo=None, bucket_hi=None)] * 2
        f = aggregate_forecast(Ls)
        assert f.interval_high == 2.0
        assert f.interval_low > 1.7


# ---------------------------------------------------------------------------
# Smoke test for the predictions module's reduced surface (no confidence_label)
# ---------------------------------------------------------------------------

class TestScoredClaimSurface:
    def test_scored_claim_no_longer_has_confidence_label(self):
        from rcm.ml.simple_pipeline import ScoredClaim
        sc = ScoredClaim(
            claim_id=1, claim_number="X", payer_name=None,
            service_variant="837P", claim_subtype="healthcare",
            risk_score=0.9, risk_level="HIGH",
        )
        assert not hasattr(sc, "confidence_label")

    def test_confidence_label_for_helper_removed(self):
        with pytest.raises(ImportError):
            from rcm.ml.simple_pipeline import confidence_label_for  # noqa: F401
