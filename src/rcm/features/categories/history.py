"""Category G — patient history (10 features).

The expensive parts are computed in SQL against ``mv_patient_claim_history``
using window functions with strict-< boundary on service_from_date to
prevent self-contamination (spec §C.3).

The Python-side `compute()` just looks up the pre-computed per-claim values
from a dict-of-dicts produced by `load_patient_history_for_claims`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rcm.features.constants import (
    PATIENT_HISTORY_DAYS_LONG,
    PATIENT_HISTORY_DAYS_MID,
    PATIENT_HISTORY_DAYS_SHORT,
)

logger = logging.getLogger(__name__)


@dataclass
class PatientHistorySnapshot:
    """Per-claim history rows keyed by claim_id. Empty when MV is empty
    (cold start) — categories fall back to safe defaults."""
    by_claim: dict[int, dict[str, Any]] = field(default_factory=dict)


_HISTORY_SQL = text(f"""
    WITH base AS (
        SELECT claim_id, patient_id, payer_id, billing_provider_id,
               service_from_date, total_charge_amount, claim_status, primary_cpt
        FROM mv_patient_claim_history
        WHERE claim_id = ANY(:ids)
    ),
    self_ranked AS (
        SELECT b.claim_id,
               b.patient_id,
               b.payer_id,
               b.billing_provider_id,
               b.service_from_date,
               b.primary_cpt,
               (
                 SELECT count(*) FROM mv_patient_claim_history h
                 WHERE h.patient_id = b.patient_id
                   AND h.service_from_date >= (b.service_from_date - INTERVAL '{PATIENT_HISTORY_DAYS_SHORT} days')
                   AND h.service_from_date <  b.service_from_date
               ) AS claims_in_last_30d,
               (
                 SELECT count(*) FROM mv_patient_claim_history h
                 WHERE h.patient_id = b.patient_id
                   AND h.service_from_date >= (b.service_from_date - INTERVAL '{PATIENT_HISTORY_DAYS_MID} days')
                   AND h.service_from_date <  b.service_from_date
               ) AS claims_in_last_90d,
               (
                 SELECT count(*) FROM mv_patient_claim_history h
                 WHERE h.patient_id = b.patient_id
                   AND h.service_from_date >= (b.service_from_date - INTERVAL '{PATIENT_HISTORY_DAYS_LONG} days')
                   AND h.service_from_date <  b.service_from_date
               ) AS claims_in_last_365d,
               (
                 SELECT count(*) FROM mv_patient_claim_history h
                 WHERE h.patient_id = b.patient_id
                   AND h.payer_id   = b.payer_id
                   AND h.claim_status = 'denied'
                   AND h.service_from_date >= (b.service_from_date - INTERVAL '{PATIENT_HISTORY_DAYS_LONG} days')
                   AND h.service_from_date <  b.service_from_date
               ) AS prior_denials_with_payer,
               (
                 SELECT count(*) FROM mv_patient_claim_history h
                 WHERE h.patient_id = b.patient_id
                   AND h.payer_id   = b.payer_id
                   AND h.claim_status = 'denied'
                   AND h.primary_cpt = b.primary_cpt
                   AND h.service_from_date >= (b.service_from_date - INTERVAL '{PATIENT_HISTORY_DAYS_LONG} days')
                   AND h.service_from_date <  b.service_from_date
               ) AS prior_denials_with_payer_and_cpt,
               (
                 SELECT count(*) FROM mv_patient_claim_history h
                 WHERE h.patient_id = b.patient_id
                   AND h.payer_id   = b.payer_id
                   AND h.claim_status IN ('paid','partially_paid')
                   AND h.service_from_date >= (b.service_from_date - INTERVAL '{PATIENT_HISTORY_DAYS_LONG} days')
                   AND h.service_from_date <  b.service_from_date
               ) AS prior_paid_with_payer,
               (
                 SELECT max(h.service_from_date) FROM mv_patient_claim_history h
                 WHERE h.patient_id = b.patient_id
                   AND h.service_from_date < b.service_from_date
               ) AS prior_service_from_date,
               (
                 SELECT count(*) FROM mv_patient_claim_history h
                 WHERE h.patient_id = b.patient_id
                   AND h.billing_provider_id = b.billing_provider_id
                   AND h.service_from_date < b.service_from_date
               ) AS prior_with_provider_count,
               (
                 SELECT COALESCE(sum(h.total_charge_amount), 0) FROM mv_patient_claim_history h
                 WHERE h.patient_id = b.patient_id
                   AND EXTRACT(YEAR FROM h.service_from_date) = EXTRACT(YEAR FROM b.service_from_date)
                   AND h.service_from_date < b.service_from_date
               ) AS annual_charges_for_patient,
               (
                 SELECT count(*) FROM mv_patient_claim_history h
                 WHERE h.patient_id = b.patient_id
                   AND h.service_from_date = b.service_from_date
                   AND h.claim_id <> b.claim_id
               ) AS same_day_visits_for_patient
        FROM base b
    )
    SELECT * FROM self_ranked
""")


async def load_patient_history_for_claims(
    session: AsyncSession, claim_ids: list[int],
) -> PatientHistorySnapshot:
    """Resolve patient-history features for a batch of claim ids.

    Catches an empty MV / missing table gracefully — returns an empty
    snapshot so callers fall back to safe defaults. The MV is empty before
    the first nightly refresh; this lets the FE pipeline run regardless.
    """
    if not claim_ids:
        return PatientHistorySnapshot()
    try:
        rows = (await session.execute(_HISTORY_SQL, {"ids": claim_ids})).mappings().all()
    except Exception as exc:
        logger.warning("Patient-history MV unavailable: %s; falling back to safe defaults", exc)
        return PatientHistorySnapshot()
    snap = PatientHistorySnapshot()
    for r in rows:
        snap.by_claim[r["claim_id"]] = dict(r)
    return snap


def compute(df: pd.DataFrame, *, snapshot: PatientHistorySnapshot | None = None) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    by_claim = snapshot.by_claim if snapshot else {}

    cids = df.get("claim_id", pd.Series([None] * len(df)))
    svc_from_col = df.get("service_from_date", pd.Series([None] * len(df)))

    def _get(cid: Any, key: str, default: Any = 0) -> Any:
        row = by_claim.get(cid)
        if row is None:
            return default
        return row.get(key, default) or default

    out["claims_in_last_30d"] = pd.Series([int(_get(c, "claims_in_last_30d")) for c in cids], index=df.index).astype("int16")
    out["claims_in_last_90d"] = pd.Series([int(_get(c, "claims_in_last_90d")) for c in cids], index=df.index).astype("int16")
    out["claims_in_last_365d"] = pd.Series([int(_get(c, "claims_in_last_365d")) for c in cids], index=df.index).astype("int16")
    out["prior_denials_with_payer"] = pd.Series([int(_get(c, "prior_denials_with_payer")) for c in cids], index=df.index).astype("int16")
    out["prior_denials_with_payer_and_cpt"] = pd.Series([int(_get(c, "prior_denials_with_payer_and_cpt")) for c in cids], index=df.index).astype("int16")
    out["prior_paid_with_payer"] = pd.Series([int(_get(c, "prior_paid_with_payer")) for c in cids], index=df.index).astype("int16")

    # days_since_last_claim from prior_service_from_date
    days = []
    for cid, sf in zip(cids, svc_from_col):
        prior = by_claim.get(cid, {}).get("prior_service_from_date")
        if prior and sf:
            try:
                days.append(int((pd.to_datetime(sf).date() - pd.to_datetime(prior).date()).days))
            except Exception:
                days.append(0)
        else:
            days.append(0)
    out["days_since_last_claim"] = pd.Series(days, index=df.index).astype("int16")

    out["is_new_patient_to_provider"] = pd.Series(
        [int(_get(c, "prior_with_provider_count", 0) == 0) for c in cids], index=df.index,
    ).astype("int8")
    out["annual_charges_for_patient"] = pd.Series(
        [float(_get(c, "annual_charges_for_patient", 0.0)) for c in cids], index=df.index,
    ).astype("float32")
    out["same_day_visits_for_patient"] = pd.Series(
        [int(_get(c, "same_day_visits_for_patient", 0)) for c in cids], index=df.index,
    ).astype("int8")
    return out
