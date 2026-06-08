"""Category I — joint encoder features (6 features).

These come straight from joint-denial-rate materialized views. Loader is
batch-friendly and falls back to safe defaults when the MV is empty.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rcm.features.categories._helpers import _has_str

logger = logging.getLogger(__name__)


@dataclass
class JointEncoderSnapshot:
    """All joint denial-rate maps, keyed by appropriate composite keys."""
    payer_cpt: dict[tuple[int, str, str], float] = field(default_factory=dict)   # (payer_id, variant, cpt) -> rate
    payer_dx: dict[tuple[int, str, str], float] = field(default_factory=dict)
    payer_pos: dict[tuple[int, str, str], float] = field(default_factory=dict)
    cpt_dx: dict[tuple[str, str], float] = field(default_factory=dict)
    provider_payer: dict[tuple[int, int], float] = field(default_factory=dict)
    provider_cpt: dict[tuple[int, str], float] = field(default_factory=dict)
    payer_overall: dict[int, float] = field(default_factory=dict)               # for Cat A bridging


async def load_joint_snapshot(
    session: AsyncSession,
    payer_ids: list[int], provider_ids: list[int],
    variants: list[str], primary_cpts: list[str | None],
    primary_dxs: list[str | None], primary_pos: list[str | None],
) -> JointEncoderSnapshot:
    snap = JointEncoderSnapshot()
    distinct_payers = sorted({p for p in payer_ids if p is not None})
    distinct_providers = sorted({p for p in provider_ids if p is not None})
    distinct_cpts = sorted({c for c in primary_cpts if c})
    distinct_dxs = sorted({d for d in primary_dxs if d})
    distinct_pos = sorted({p for p in primary_pos if p})
    distinct_variants = sorted({v for v in variants if _has_str(v)})

    async def _safe_fetch(sql: text, params: dict) -> list:
        try:
            return (await session.execute(sql, params)).mappings().all()
        except Exception as exc:
            logger.warning("MV unavailable: %s", exc)
            return []

    if distinct_payers and distinct_cpts and distinct_variants:
        rows = await _safe_fetch(text("""
            SELECT payer_id, service_variant, cpt, denial_rate FROM mv_payer_cpt_denial_rate
            WHERE payer_id = ANY(:py) AND service_variant = ANY(:sv) AND cpt = ANY(:cpts)
        """), {"py": distinct_payers, "sv": distinct_variants, "cpts": distinct_cpts})
        for r in rows:
            snap.payer_cpt[(r["payer_id"], r["service_variant"], r["cpt"])] = float(r["denial_rate"] or 0.0)

    if distinct_payers and distinct_dxs and distinct_variants:
        rows = await _safe_fetch(text("""
            SELECT payer_id, service_variant, dx, denial_rate FROM mv_payer_dx_denial_rate
            WHERE payer_id = ANY(:py) AND service_variant = ANY(:sv) AND dx = ANY(:dxs)
        """), {"py": distinct_payers, "sv": distinct_variants, "dxs": distinct_dxs})
        for r in rows:
            snap.payer_dx[(r["payer_id"], r["service_variant"], r["dx"])] = float(r["denial_rate"] or 0.0)

    if distinct_payers and distinct_pos and distinct_variants:
        rows = await _safe_fetch(text("""
            SELECT payer_id, service_variant, pos, denial_rate FROM mv_payer_pos_denial_rate
            WHERE payer_id = ANY(:py) AND service_variant = ANY(:sv) AND pos = ANY(:pos)
        """), {"py": distinct_payers, "sv": distinct_variants, "pos": distinct_pos})
        for r in rows:
            snap.payer_pos[(r["payer_id"], r["service_variant"], r["pos"])] = float(r["denial_rate"] or 0.0)

    if distinct_cpts and distinct_dxs:
        rows = await _safe_fetch(text("""
            SELECT cpt, dx, denial_rate FROM mv_cpt_dx_denial_rate
            WHERE cpt = ANY(:cpts) AND dx = ANY(:dxs)
        """), {"cpts": distinct_cpts, "dxs": distinct_dxs})
        for r in rows:
            snap.cpt_dx[(r["cpt"], r["dx"])] = float(r["denial_rate"] or 0.0)

    if distinct_providers and distinct_payers:
        rows = await _safe_fetch(text("""
            SELECT billing_provider_id, payer_id, denial_rate FROM mv_provider_payer_denial_rate
            WHERE billing_provider_id = ANY(:pid) AND payer_id = ANY(:py)
        """), {"pid": distinct_providers, "py": distinct_payers})
        for r in rows:
            snap.provider_payer[(r["billing_provider_id"], r["payer_id"])] = float(r["denial_rate"] or 0.0)

    if distinct_providers and distinct_cpts:
        rows = await _safe_fetch(text("""
            SELECT billing_provider_id, cpt, denial_rate FROM mv_provider_cpt_denial_rate
            WHERE billing_provider_id = ANY(:pid) AND cpt = ANY(:cpts)
        """), {"pid": distinct_providers, "cpts": distinct_cpts})
        for r in rows:
            snap.provider_cpt[(r["billing_provider_id"], r["cpt"])] = float(r["denial_rate"] or 0.0)

    if distinct_payers:
        rows = await _safe_fetch(text("""
            SELECT payer_id, avg(denial_rate) AS rate FROM mv_payer_denial_rates
            WHERE payer_id = ANY(:py) GROUP BY payer_id
        """), {"py": distinct_payers})
        for r in rows:
            snap.payer_overall[r["payer_id"]] = float(r["rate"] or 0.0)

    return snap


def compute(df: pd.DataFrame, *, snapshot: JointEncoderSnapshot | None = None) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    snap = snapshot or JointEncoderSnapshot()

    payer_id = df.get("payer_id", pd.Series([None] * len(df)))
    variant = df.get("service_variant", pd.Series([None] * len(df)))
    cpt = df.get("primary_cpt", pd.Series([None] * len(df)))
    dx = df.get("primary_dx", pd.Series([None] * len(df)))
    pos = df.get("primary_pos", pd.Series([None] * len(df)))
    bill_id = df.get("billing_provider_id", pd.Series([None] * len(df)))

    out["payer_cpt_denial_rate"] = pd.Series(
        [float(snap.payer_cpt.get((p, str(v), str(c)), 0.0))
         if (p is not None and _has_str(v) and _has_str(c)) else 0.0
         for p, v, c in zip(payer_id, variant, cpt)],
        index=df.index,
    ).astype("float32")
    out["payer_dx_denial_rate"] = pd.Series(
        [float(snap.payer_dx.get((p, str(v), str(d)), 0.0))
         if (p is not None and _has_str(v) and _has_str(d)) else 0.0
         for p, v, d in zip(payer_id, variant, dx)],
        index=df.index,
    ).astype("float32")
    out["payer_pos_denial_rate"] = pd.Series(
        [float(snap.payer_pos.get((p, str(v), str(po)), 0.0))
         if (p is not None and _has_str(v) and _has_str(po)) else 0.0
         for p, v, po in zip(payer_id, variant, pos)],
        index=df.index,
    ).astype("float32")
    out["cpt_dx_denial_rate"] = pd.Series(
        [float(snap.cpt_dx.get((str(c), str(d)), 0.0))
         if (_has_str(c) and _has_str(d)) else 0.0
         for c, d in zip(cpt, dx)],
        index=df.index,
    ).astype("float32")
    out["payer_provider_denial_rate"] = pd.Series(
        [float(snap.provider_payer.get((b, p), 0.0))
         if (b is not None and p is not None) else 0.0
         for b, p in zip(bill_id, payer_id)],
        index=df.index,
    ).astype("float32")
    out["provider_cpt_denial_rate_joint"] = pd.Series(
        [float(snap.provider_cpt.get((b, str(c)), 0.0))
         if (b is not None and _has_str(c)) else 0.0
         for b, c in zip(bill_id, cpt)],
        index=df.index,
    ).astype("float32")
    return out
