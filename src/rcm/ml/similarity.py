"""CR-136 — historical similarity forecast engine.

Answers "of N HIGH claims today, how many will deny?" by retrieving the
K nearest historically adjudicated claims and using their empirical denial
rate as the per-claim probability.  Replaces (or augments) CR-131B's
bucket-precision forecast.

No new ML model is introduced.  The retrieval is a deterministic KDTree
over four target-encoded features:

  - risk_score (the model's calibrated probability — unchanged input)
  - service_variant_TE (target-encoded variant)
  - payer_TE (target-encoded payer)
  - is_replacement (binary)

Per CR-134's feature-ablation audit these four features deliver MAE 0.582
on the temporal eval split — beating CR-135's deterministic best (0.804)
by ~28%, well above CR-136's 20% target.

Loaded once at process startup from ``mv_forecast_history``; refreshed via
the CR-127 cascade.  Falls back to ``CalibrationTable`` (CR-131B/CR-132D)
when an input cell has no historical neighbours.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import asyncpg
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Number of historical neighbours retrieved per prediction.  CR-134/CR-135
# scans identified K=10 as optimal across both per-claim MAE and file-level
# MAE; smaller K → noise from individual neighbours; larger K → over-smoothing.
DEFAULT_K = 10

# Minimum train rows per variant before we trust the per-variant index.
# Below this, fall back to a global index then to the bucket-based forecaster.
MIN_VARIANT_HISTORY = 100


@dataclass(frozen=True)
class NeighbourSet:
    """Result of one similarity lookup for a single claim."""
    p_hat: float
    n_neighbours: int
    n_denied: int
    n_paid: int
    average_similarity: float       # 0..1; 1 = exact match
    matching_factors: tuple[str, ...]  # human-readable selection rationale
    fallback_used: str              # "similarity" | "calibration_bucket" | "model"


@dataclass
class SimilarityEngine:
    """In-process KDTree over historical adjudicated claims.

    Build once at startup via ``await SimilarityEngine.load(conn)``.  Lookup
    per claim returns a ``NeighbourSet``.  Thread-safe for read; rebuild on
    refresh by constructing a fresh instance.
    """

    history: pd.DataFrame                    # rows from mv_forecast_history
    payer_te: dict[int, float] = field(default_factory=dict)
    variant_te: dict[str, float] = field(default_factory=dict)
    train_mean: float = 0.0
    _trees: dict[str, object] = field(default_factory=dict)   # per-variant KDTrees
    _norms: dict[str, tuple] = field(default_factory=dict)    # (mean, std) per variant

    @classmethod
    async def load(
        cls,
        conn: asyncpg.Connection,
        *,
        engine_sa=None,
    ) -> "SimilarityEngine":
        """Populate from ``mv_forecast_history``. Returns a ready-to-query
        engine. If the MV is empty or insufficient, the engine still loads —
        callers will see ``fallback_used='model'`` on every lookup.

        The MV holds adjudicated claim metadata only; ``predicted_risk`` is
        recomputed at load time by calling the current FB bundles. This
        decouples the similarity index from ``prediction_log`` so the engine
        works even when the production API has been quiet for a while.

        ``engine_sa`` is an optional SQLAlchemy AsyncEngine used to drive
        ``_load_predict_corpus`` + the FB ``HealthcarePredictor``s. If None
        is supplied, the engine builds itself from a SQLAlchemy engine
        constructed from ``rcm.core.config.settings.DATABASE_URL``.
        """
        rows = await conn.fetch(
            """
            SELECT claim_id, service_variant, claim_subtype, payer_id,
                   is_replacement, denied
            FROM mv_forecast_history
            """
        )
        if not rows:
            logger.warning("CR-136 similarity engine: mv_forecast_history empty")
            return cls(history=pd.DataFrame())

        history = pd.DataFrame([dict(r) for r in rows])
        history["denied"] = history["denied"].astype(int)
        history["is_replacement"] = history["is_replacement"].astype(int)

        # Score each historical claim with the current FB bundles. This is
        # the bottleneck at startup (~2-3 min for 100k claims) but only runs
        # once per /reload-bundles cycle.
        history["predicted_risk"] = await _score_history(history, engine_sa)
        # Drop rows the FB pipeline couldn't score (e.g. variant has no bundle)
        history = history.dropna(subset=["predicted_risk"]).reset_index(drop=True)
        history["predicted_risk"] = history["predicted_risk"].astype(float)
        logger.info(
            "CR-136 similarity engine: scored %d historical rows", len(history)
        )

        # Target encodings: mean denial rate per payer / variant.  These are
        # the SAME numeric encodings used at lookup time so distance is
        # meaningful (Euclidean over (score, payer_TE, variant_TE, is_repl)).
        payer_te = history.groupby("payer_id")["denied"].mean().to_dict()
        variant_te = history.groupby("service_variant")["denied"].mean().to_dict()
        train_mean = float(history["denied"].mean())

        engine = cls(
            history=history, payer_te=payer_te,
            variant_te=variant_te, train_mean=train_mean,
        )
        engine._build_indices()
        logger.info(
            "CR-136 similarity engine loaded: %d historical claims, "
            "%d variants, %d payers, base_rate=%.3f",
            len(history),
            history["service_variant"].nunique(),
            len(payer_te),
            train_mean,
        )
        return engine

    def _build_indices(self) -> None:
        """One KDTree per service_variant for efficient variant-restricted
        retrieval.  Falls back to a global tree when a variant has too few
        rows."""
        try:
            from sklearn.neighbors import KDTree
        except ImportError:
            logger.warning("scikit-learn not available; similarity engine disabled")
            return
        h = self.history
        for variant in h["service_variant"].unique():
            sub = h[h["service_variant"] == variant]
            if len(sub) < MIN_VARIANT_HISTORY:
                continue
            X = self._encode(sub)
            mu = X.mean(axis=0); sd = X.std(axis=0); sd[sd == 0] = 1.0
            X_n = (X - mu) / sd
            self._trees[variant] = (KDTree(X_n), sub.reset_index(drop=True))
            self._norms[variant] = (mu, sd)
        # Global fallback tree (any variant)
        if len(h) >= MIN_VARIANT_HISTORY:
            X = self._encode(h)
            mu = X.mean(axis=0); sd = X.std(axis=0); sd[sd == 0] = 1.0
            X_n = (X - mu) / sd
            from sklearn.neighbors import KDTree
            self._trees["__GLOBAL__"] = (KDTree(X_n), h.reset_index(drop=True))
            self._norms["__GLOBAL__"] = (mu, sd)

    def _encode(self, df: pd.DataFrame) -> np.ndarray:
        """Encode a (sub)DataFrame into the 4-feature numeric matrix."""
        return np.column_stack([
            df["predicted_risk"].astype(float).to_numpy(),
            df["payer_id"].map(self.payer_te).fillna(self.train_mean).astype(float).to_numpy(),
            df["service_variant"].map(self.variant_te).fillna(self.train_mean).astype(float).to_numpy(),
            df["is_replacement"].astype(float).to_numpy(),
        ])

    def lookup(
        self,
        *,
        risk_score: float,
        service_variant: str,
        payer_id: int | None,
        is_replacement: bool,
        k: int = DEFAULT_K,
    ) -> NeighbourSet:
        """Find the K nearest historical claims and summarise their outcomes."""
        # Build the query vector with the same encoding as the index
        q_payer_te   = self.payer_te.get(payer_id, self.train_mean)
        q_variant_te = self.variant_te.get(service_variant, self.train_mean)
        q = np.array([[
            float(risk_score),
            float(q_payer_te),
            float(q_variant_te),
            float(int(is_replacement)),
        ]])

        # Variant-specific tree preferred; fall through to global; fall through
        # to "model" (return raw risk_score).
        tree_info = self._trees.get(service_variant) or self._trees.get("__GLOBAL__")
        if tree_info is None:
            return NeighbourSet(
                p_hat=float(risk_score), n_neighbours=0, n_denied=0, n_paid=0,
                average_similarity=0.0, matching_factors=(),
                fallback_used="model",
            )
        tree, sub_history = tree_info
        norm_key = service_variant if service_variant in self._trees else "__GLOBAL__"
        mu, sd = self._norms[norm_key]
        q_n = (q - mu) / sd

        # KDTree expects float64; sklearn handles internally
        k_eff = min(k, len(sub_history))
        dists, idx = tree.query(q_n, k=k_eff)
        neighbour_rows = sub_history.iloc[idx[0]]
        n_denied = int(neighbour_rows["denied"].sum())
        n_neighbours = int(len(neighbour_rows))
        n_paid = n_neighbours - n_denied
        p_hat = float(n_denied / n_neighbours) if n_neighbours else float(risk_score)

        # Average similarity = 1 / (1 + mean(distance)).  Distances are in
        # standardised feature units; this maps them to a (0, 1] score where
        # 1 = identical, 0.5 ≈ ~1 std away.
        mean_dist = float(dists.mean())
        avg_sim = 1.0 / (1.0 + mean_dist)

        # Matching factors: which features made the neighbours similar to
        # the query.  We surface flags for the dimensions where the
        # neighbours strongly match the query.
        factors: list[str] = []
        # Same variant share
        if (neighbour_rows["service_variant"] == service_variant).mean() >= 0.7:
            factors.append("Same service variant")
        # Same payer share
        if payer_id is not None and (neighbour_rows["payer_id"] == payer_id).mean() >= 0.6:
            factors.append("Same payer")
        # Same replacement status share
        if (neighbour_rows["is_replacement"] == int(is_replacement)).mean() >= 0.8:
            factors.append("Same original/replacement status")
        # Score within ±0.05
        if (np.abs(neighbour_rows["predicted_risk"].astype(float) - float(risk_score)) <= 0.05).mean() >= 0.6:
            factors.append("Similar calibrated score")

        return NeighbourSet(
            p_hat=p_hat,
            n_neighbours=n_neighbours,
            n_denied=n_denied,
            n_paid=n_paid,
            average_similarity=avg_sim,
            matching_factors=tuple(factors),
            fallback_used="similarity",
        )


# ---------------------------------------------------------------------------
# Aggregation: per-file forecast + neighbour summary
# ---------------------------------------------------------------------------

@dataclass
class SimilarityForecast:
    """Batch-level forecast aggregated over a HIGH cohort using historical
    similarity per-claim probabilities."""
    n_high: int
    expected_denials: float
    expected_approvals: float
    forecast_confidence_pct: float
    interval_low: float
    interval_high: float
    historical_evidence_count: int        # total neighbours retrieved
    average_similarity: float             # mean of per-claim avg similarity
    data_window_days: int
    fallback_distribution: dict[str, float]
    last_history_refresh: str | None


async def _score_history(history: pd.DataFrame, engine_sa) -> pd.Series:
    """Score every row in ``history`` with the current FB bundle for its
    (variant, subtype). Returns a Series aligned to history.index with
    ``predicted_risk`` floats (or NaN where no bundle is available)."""
    if history.empty:
        return pd.Series([], dtype=float, index=history.index)

    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from rcm.core.config import settings
    from rcm.ml.predictor import HealthcarePredictor
    from rcm.ml.shadow import _load_predict_corpus

    owns_engine = False
    if engine_sa is None:
        engine_sa = create_async_engine(
            settings.DATABASE_URL, pool_pre_ping=True
        )
        owns_engine = True

    art_root = Path("artifacts/featurebuilder")
    bundles = {
        ("837P", "healthcare"): art_root / "837P_healthcare",
        ("837D", "dental"):     art_root / "837D_dental",
        ("837I", "home_care"):  art_root / "837I_home_care",
    }
    pred_map: dict[int, float] = {}
    try:
        for (variant, subtype), art_dir in bundles.items():
            if not art_dir.is_dir():
                continue
            m = (history["service_variant"] == variant) & (history["claim_subtype"] == subtype)
            cids = history.loc[m, "claim_id"].astype(int).tolist()
            if not cids:
                continue
            try:
                predictor = HealthcarePredictor.load(art_dir)
            except Exception as exc:
                logger.warning(
                    "CR-136: cannot load predictor for %s/%s: %s",
                    variant, subtype, exc,
                )
                continue
            # Batch through the corpus loader
            BATCH = 5000
            for start in range(0, len(cids), BATCH):
                chunk = cids[start:start + BATCH]
                async with AsyncSession(engine_sa, expire_on_commit=False) as session:
                    df = await _load_predict_corpus(session, chunk)
                    if df.empty:
                        continue
                    results = await predictor.predict(session, df)
                for r in results:
                    if r.claim_id is not None:
                        pred_map[int(r.claim_id)] = float(r.risk_score)
    finally:
        if owns_engine:
            await engine_sa.dispose()

    return history["claim_id"].astype(int).map(pred_map)


def aggregate_similarity_forecast(
    neighbour_sets: list[NeighbourSet],
    *,
    window_days: int = 180,
    last_history_refresh: str | None = None,
) -> SimilarityForecast:
    n = len(neighbour_sets)
    if n == 0:
        return SimilarityForecast(
            n_high=0, expected_denials=0.0, expected_approvals=0.0,
            forecast_confidence_pct=0.0, interval_low=0.0, interval_high=0.0,
            historical_evidence_count=0, average_similarity=0.0,
            data_window_days=window_days,
            fallback_distribution={"similarity": 0.0, "model": 0.0},
            last_history_refresh=last_history_refresh,
        )
    ps = [ns.p_hat for ns in neighbour_sets]
    expected = float(sum(ps))
    var = float(sum(p * (1.0 - p) for p in ps))
    std = math.sqrt(var) if var > 0 else 0.0
    lo = max(0.0, expected - 1.645 * std)
    hi = min(float(n), expected + 1.645 * std)

    counts: dict[str, int] = {}
    for ns in neighbour_sets:
        counts[ns.fallback_used] = counts.get(ns.fallback_used, 0) + 1
    dist = {k: v / n for k, v in counts.items()}

    total_evidence = sum(ns.n_neighbours for ns in neighbour_sets)
    avg_sim_overall = (
        sum(ns.average_similarity for ns in neighbour_sets) / n
        if n > 0 else 0.0
    )

    return SimilarityForecast(
        n_high=n,
        expected_denials=round(expected, 2),
        expected_approvals=round(n - expected, 2),
        forecast_confidence_pct=round(expected / n, 4),
        interval_low=round(lo, 2),
        interval_high=round(hi, 2),
        historical_evidence_count=int(total_evidence),
        average_similarity=round(avg_sim_overall, 4),
        data_window_days=window_days,
        fallback_distribution=dist,
        last_history_refresh=last_history_refresh,
    )
