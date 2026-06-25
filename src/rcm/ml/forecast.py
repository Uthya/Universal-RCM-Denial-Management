"""CR-131B — empirical denial forecast for HIGH-flagged claims.

Answers: "Of N HIGH claims predicted today, how many will deny once 835s
arrive?"  The answer is NOT the model's self-reported risk_score (which CR-129
showed is miscalibrated per variant).  It is the historical precision of
HIGH-bucket claims with similar risk_score over a rolling 90-day window,
joined from ``prediction_log`` and ``remittance_claims`` into
``mv_forecast_calibration``.

The lookup follows a fallback ladder per CR-131A:
  A. (service_variant, claim_subtype, score_bucket)   — N >= 30
  B. (service_variant, claim_subtype)                  — variant-level row
  C. global                                            — variant=NULL row
  D. risk_score                                        — last resort

CR-131A.1 measured bucket-level forecasting reduces MAE 45-87% vs variant-only
at operational batch sizes; the cost is ~33 MV rows.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import asyncpg

logger = logging.getLogger(__name__)

# Minimum bucket sample size before we trust the bucket-level estimate over
# the variant-level fallback.  Backed by CR-131A.1's sample-size scan: at
# N<30 the bucket estimator becomes noisier than the pooled variant estimator.
MIN_BUCKET_N = 30


@dataclass(frozen=True)
class CalibrationRow:
    """One row of ``mv_forecast_calibration``.  ``service_variant``,
    ``claim_subtype``, ``bucket_lo``, ``bucket_hi`` are None for the
    variant-level / global fallback rows."""
    service_variant: str | None
    claim_subtype: str | None
    bucket_lo: float | None
    bucket_hi: float | None
    n_adjudicated: int
    n_denied: int
    p_hat: float
    p_hat_lo: float
    p_hat_hi: float


@dataclass(frozen=True)
class ClaimLookup:
    """Per-claim forecast lookup result."""
    p_hat: float
    sample_size: int
    fallback_used: str   # "bucket" | "variant" | "global" | "model"
    bucket_lo: float | None
    bucket_hi: float | None


@dataclass
class Forecast:
    """Batch-level forecast aggregated over a HIGH cohort."""
    n_high: int
    expected_denials: float
    expected_approvals: float
    forecast_confidence_pct: float
    interval_low: float
    interval_high: float
    historical_sample_size: int
    data_window_days: int
    fallback_distribution: dict[str, float]   # {"bucket": 0.85, "variant": 0.15, ...}
    last_calibration_refresh: str | None       # ISO 8601 string or None


# ---------------------------------------------------------------------------
# Calibration table — loaded once per request, cached short-term in process.
# ---------------------------------------------------------------------------

class CalibrationTable:
    """In-memory snapshot of mv_forecast_calibration optimised for per-claim
    lookups.  Indexed by (service_variant, claim_subtype, bucket_idx) with
    variant-level + global rows separately addressable.
    """

    def __init__(
        self,
        bucket_rows: dict[tuple[str, str, int], CalibrationRow],
        variant_rows: dict[tuple[str, str], CalibrationRow],
        global_row: CalibrationRow | None,
        last_refresh: str | None,
    ) -> None:
        self._buckets = bucket_rows
        self._variants = variant_rows
        self._global = global_row
        self.last_refresh = last_refresh

    @classmethod
    async def load(cls, conn: asyncpg.Connection) -> "CalibrationTable":
        rows = await conn.fetch(
            """
            SELECT service_variant, claim_subtype, bucket_lo, bucket_hi,
                   n_adjudicated, n_denied,
                   p_hat::float8 AS p_hat,
                   p_hat_lo::float8 AS p_hat_lo,
                   p_hat_hi::float8 AS p_hat_hi,
                   last_refreshed_at
            FROM mv_forecast_calibration
            """
        )
        buckets: dict[tuple[str, str, int], CalibrationRow] = {}
        variants: dict[tuple[str, str], CalibrationRow] = {}
        global_row: CalibrationRow | None = None
        last_refresh: str | None = None
        for r in rows:
            if r["last_refreshed_at"] is not None:
                last_refresh = r["last_refreshed_at"].isoformat()
            cr = CalibrationRow(
                service_variant=r["service_variant"],
                claim_subtype=r["claim_subtype"],
                bucket_lo=float(r["bucket_lo"]) if r["bucket_lo"] is not None else None,
                bucket_hi=float(r["bucket_hi"]) if r["bucket_hi"] is not None else None,
                n_adjudicated=int(r["n_adjudicated"]),
                n_denied=int(r["n_denied"]),
                p_hat=float(r["p_hat"]),
                p_hat_lo=float(r["p_hat_lo"]),
                p_hat_hi=float(r["p_hat_hi"]),
            )
            if cr.service_variant is None and cr.claim_subtype is None:
                global_row = cr
            elif cr.bucket_lo is None:
                variants[(cr.service_variant, cr.claim_subtype)] = cr
            else:
                idx = _bucket_idx(cr.bucket_lo)
                buckets[(cr.service_variant, cr.claim_subtype, idx)] = cr
        return cls(buckets, variants, global_row, last_refresh)

    def lookup(
        self,
        risk_score: float,
        service_variant: str | None,
        claim_subtype: str | None,
    ) -> ClaimLookup:
        """Fallback ladder per CR-131A:
            A. (variant, subtype, bucket)   if N >= MIN_BUCKET_N
            B. (variant, subtype)
            C. global
            D. risk_score
        """
        idx = _bucket_idx(risk_score)
        if service_variant and claim_subtype:
            row = self._buckets.get((service_variant, claim_subtype, idx))
            if row is not None and row.n_adjudicated >= MIN_BUCKET_N:
                return ClaimLookup(
                    p_hat=row.p_hat,
                    sample_size=row.n_adjudicated,
                    fallback_used="bucket",
                    bucket_lo=row.bucket_lo,
                    bucket_hi=row.bucket_hi,
                )
            row_v = self._variants.get((service_variant, claim_subtype))
            if row_v is not None and row_v.n_adjudicated >= MIN_BUCKET_N:
                return ClaimLookup(
                    p_hat=row_v.p_hat,
                    sample_size=row_v.n_adjudicated,
                    fallback_used="variant",
                    bucket_lo=None,
                    bucket_hi=None,
                )
        if self._global is not None and self._global.n_adjudicated >= MIN_BUCKET_N:
            return ClaimLookup(
                p_hat=self._global.p_hat,
                sample_size=self._global.n_adjudicated,
                fallback_used="global",
                bucket_lo=None,
                bucket_hi=None,
            )
        # Last resort — use the model's own probability.  This indicates the
        # forecast layer has no data at all; usually only hit in fresh
        # deployments before any 835 has been adjudicated.
        return ClaimLookup(
            p_hat=float(risk_score),
            sample_size=0,
            fallback_used="model",
            bucket_lo=None,
            bucket_hi=None,
        )


def _bucket_idx(p: float) -> int:
    """Map a probability to a decile index 0..9.  Mirrors the SQL CASE in
    the MV definition."""
    if p >= 0.999999:
        return 9
    return max(0, min(int(p * 10), 9))


# ---------------------------------------------------------------------------
# Aggregation — Poisson-Binomial expected count + normal-approx 90% CI.
# ---------------------------------------------------------------------------

def aggregate_forecast(
    lookups: list[ClaimLookup],
    *,
    data_window_days: int = 90,
    last_refresh: str | None = None,
) -> Forecast:
    """Aggregate per-claim lookups into a batch-level forecast.

    Uses linearity of expectation for ``expected_denials = sum(p_i)`` (exact),
    and the per-claim-independence Poisson-Binomial std for the 90% CI
    (approximate at small batch sizes — for very small batches operators
    should treat the interval as a lower bound on uncertainty).
    """
    n_high = len(lookups)
    if n_high == 0:
        return Forecast(
            n_high=0, expected_denials=0.0, expected_approvals=0.0,
            forecast_confidence_pct=0.0, interval_low=0.0, interval_high=0.0,
            historical_sample_size=0, data_window_days=data_window_days,
            fallback_distribution={"bucket": 0.0, "variant": 0.0, "global": 0.0, "model": 0.0},
            last_calibration_refresh=last_refresh,
        )
    expected = sum(L.p_hat for L in lookups)
    variance = sum(L.p_hat * (1.0 - L.p_hat) for L in lookups)
    std = math.sqrt(variance) if variance > 0 else 0.0
    lo = max(0.0, expected - 1.645 * std)
    hi = min(float(n_high), expected + 1.645 * std)

    # Fallback distribution = share of HIGH claims served by each ladder
    # level.  Reveals when a forecast rests on thin or absent data.
    counts = {"bucket": 0, "variant": 0, "global": 0, "model": 0}
    for L in lookups:
        counts[L.fallback_used] = counts.get(L.fallback_used, 0) + 1
    distribution = {k: v / n_high for k, v in counts.items()}

    # Historical sample size = mean of per-claim sample sizes across the
    # cohort.  Reflects how much data underpins this forecast, not the size
    # of any single cell.
    avg_n = int(round(sum(L.sample_size for L in lookups) / n_high))

    return Forecast(
        n_high=n_high,
        expected_denials=round(expected, 2),
        expected_approvals=round(n_high - expected, 2),
        forecast_confidence_pct=round(expected / n_high, 4),
        interval_low=round(lo, 2),
        interval_high=round(hi, 2),
        historical_sample_size=avg_n,
        data_window_days=data_window_days,
        fallback_distribution=distribution,
        last_calibration_refresh=last_refresh,
    )
