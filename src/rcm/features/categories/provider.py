"""Category H — provider profile (9 features; CR-104 retired billing_rendering_same_npi).

Backed by mv_provider_denial_profiles + mv_provider_payer_denial_rate +
mv_provider_cpt_denial_rate. Loader catches missing-MV gracefully.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rcm.features.categories._helpers import _has_str
from rcm.features.categories.availability import RefDataLookup

logger = logging.getLogger(__name__)


@dataclass
class ProviderProfileSnapshot:
    profiles: dict[int, dict[str, Any]] = field(default_factory=dict)         # by billing_provider_id
    by_provider_payer: dict[tuple[int, int], float] = field(default_factory=dict)
    by_provider_cpt: dict[tuple[int, str], float] = field(default_factory=dict)

    def volume_band(self, provider_id: int | None) -> int:
        if provider_id is None or provider_id not in self.profiles:
            return 0
        n = int(self.profiles[provider_id].get("total_claims") or 0)
        if n < 100:
            return 0
        if n < 10_000:
            return 1
        return 2


async def load_provider_snapshot(
    session: AsyncSession, provider_ids: list[int], payer_ids: list[int],
    primary_cpts: list[str | None],
) -> ProviderProfileSnapshot:
    snap = ProviderProfileSnapshot()
    if not provider_ids:
        return snap

    distinct_providers = sorted({p for p in provider_ids if p is not None})
    distinct_payers = sorted({p for p in payer_ids if p is not None})
    distinct_cpts = sorted({c for c in primary_cpts if c})

    if distinct_providers:
        try:
            rows = (await session.execute(text("""
                SELECT billing_provider_id, total_claims, denial_rate, payer_diversity
                FROM mv_provider_denial_profiles WHERE billing_provider_id = ANY(:ids)
            """), {"ids": distinct_providers})).mappings().all()
            for r in rows:
                snap.profiles[r["billing_provider_id"]] = dict(r)
        except Exception as exc:
            logger.warning("mv_provider_denial_profiles unavailable: %s", exc)

        if distinct_payers:
            try:
                rows = (await session.execute(text("""
                    SELECT billing_provider_id, payer_id, denial_rate
                    FROM mv_provider_payer_denial_rate
                    WHERE billing_provider_id = ANY(:pid) AND payer_id = ANY(:py)
                """), {"pid": distinct_providers, "py": distinct_payers})).mappings().all()
                for r in rows:
                    snap.by_provider_payer[(r["billing_provider_id"], r["payer_id"])] = float(r["denial_rate"] or 0.0)
            except Exception as exc:
                logger.warning("mv_provider_payer_denial_rate unavailable: %s", exc)

        if distinct_cpts:
            try:
                rows = (await session.execute(text("""
                    SELECT billing_provider_id, cpt, denial_rate
                    FROM mv_provider_cpt_denial_rate
                    WHERE billing_provider_id = ANY(:pid) AND cpt = ANY(:cpts)
                """), {"pid": distinct_providers, "cpts": list(distinct_cpts)})).mappings().all()
                for r in rows:
                    snap.by_provider_cpt[(r["billing_provider_id"], r["cpt"])] = float(r["denial_rate"] or 0.0)
            except Exception as exc:
                logger.warning("mv_provider_cpt_denial_rate unavailable: %s", exc)

    return snap


def compute(
    df: pd.DataFrame,
    *,
    snapshot: ProviderProfileSnapshot | None = None,
    ref: RefDataLookup | None = None,
    billing_npi_encoded: pd.Series | None = None,
    rendering_npi_encoded: pd.Series | None = None,
    provider_taxonomy_encoded: pd.Series | None = None,
) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    snap = snapshot or ProviderProfileSnapshot()

    ref_pid = df.get("referring_provider_id", pd.Series([None] * len(df)))
    bill_id = df.get("billing_provider_id", pd.Series([None] * len(df)))
    payer_id = df.get("payer_id", pd.Series([None] * len(df)))
    cpts = df.get("primary_cpt", pd.Series([None] * len(df)))
    taxonomy = df.get("billing_provider_taxonomy", pd.Series([None] * len(df)))

    out["billing_provider_npi_encoded"] = (billing_npi_encoded if billing_npi_encoded is not None
                                            else pd.Series(0.0, index=df.index)).reindex(df.index, fill_value=0.0).astype("float32")
    out["rendering_provider_npi_encoded"] = (rendering_npi_encoded if rendering_npi_encoded is not None
                                              else pd.Series(0.0, index=df.index)).reindex(df.index, fill_value=0.0).astype("float32")
    out["referring_provider_present"] = pd.Series(
        [int(p is not None) for p in ref_pid], index=df.index,
    ).astype("int8")
    out["provider_specialty_taxonomy_encoded"] = (provider_taxonomy_encoded if provider_taxonomy_encoded is not None
                                                   else pd.Series(0.0, index=df.index)).reindex(df.index, fill_value=0.0).astype("float32")

    def _overall(pid: Any) -> float:
        if pid is None:
            return 0.0
        prof = snap.profiles.get(pid)
        return float(prof.get("denial_rate") or 0.0) if prof else 0.0

    out["provider_overall_denial_rate"] = pd.Series(
        [_overall(p) for p in bill_id], index=df.index,
    ).astype("float32")
    out["provider_payer_denial_rate"] = pd.Series(
        [float(snap.by_provider_payer.get((b, p), 0.0)) if (b is not None and p is not None) else 0.0
         for b, p in zip(bill_id, payer_id)], index=df.index,
    ).astype("float32")
    out["provider_cpt_denial_rate"] = pd.Series(
        [float(snap.by_provider_cpt.get((b, str(c)), 0.0)) if (b is not None and _has_str(c)) else 0.0
         for b, c in zip(bill_id, cpts)], index=df.index,
    ).astype("float32")
    out["provider_volume_band"] = pd.Series(
        [snap.volume_band(p) for p in bill_id], index=df.index,
    ).astype("int8")

    # provider_specialty_matches_cpt — needs procedure_codes.metadata.category
    # mapped against taxonomy. Default 1 when refs missing.
    ref = ref or RefDataLookup()

    def _match(tax: Any, cpt: Any) -> int:
        if not (_has_str(tax) and _has_str(cpt)):
            return 1
        meta = ref.procedure_metadata.get(str(cpt))
        if not meta:
            return 1
        category = meta.get("category")
        if not category:
            return 1
        # Very rough mapping; real impl would use NUCC taxonomy → CPT-category table
        return 1   # placeholder until taxonomy_cpt_match table is loaded
    out["provider_specialty_matches_cpt"] = pd.Series(
        [_match(t, c) for t, c in zip(taxonomy, cpts)], index=df.index,
    ).astype("int8")
    return out
