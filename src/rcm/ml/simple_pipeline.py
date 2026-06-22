"""Simple, self-contained denial-prediction pipeline.

Designed to replace v2's per-variant + target-encoder + materialized-view ML
scaffold, which depends on a feature-engineering layer that often isn't ready
on a fresh DB. This module:

    * pulls data straight from claims + remittance_claims + payers + lines + diagnoses
    * applies a small, fixed feature set (no leakage-prone target encoders)
    * trains a single XGBoost across all variants
    * computes per-claim SHAP values for the top-N denial reasons
    * saves / loads a single pickle artifact

Public surface:

    await build_training_frame(session)       -> (X_df, y_series, raw_rows)
    await train(session, artifact_path)       -> dict of metrics
    await predict_file(session, edi_file_id)  -> list[ScoredClaim]
    load_artifact(artifact_path)              -> ModelArtifact

A "ScoredClaim" has the per-claim risk + SHAP-derived denial reasons in plain
English, ready for the upload-card UI.
"""

from __future__ import annotations

import json
import logging
import pickle
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from xgboost import XGBClassifier

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Feature spec — small, fixed, no encoders that need fitting on labels
# ---------------------------------------------------------------------------

_VARIANT_BUCKETS = ("837P", "837I", "837D")
_TOP_PAYERS = 20
_TOP_PROCEDURES = 30
_TOP_DIAGNOSES = 30


@dataclass
class ModelArtifact:
    booster: XGBClassifier
    calibrator: CalibratedClassifierCV | None
    feature_columns: list[str]
    # vocabs needed to one-hot at predict time
    payer_vocab: list[str]
    procedure_vocab: list[str]
    diagnosis_vocab: list[str]
    subtype_vocab: list[str]
    decision_threshold: float
    model_version: str
    trained_at: str
    n_training_samples: int
    n_test_samples: int
    metrics: dict[str, float]
    # plain-English label per feature for the UI
    feature_labels: dict[str, str]


@dataclass
class ScoredClaim:
    claim_id: int
    claim_number: str
    payer_name: str | None
    service_variant: str
    claim_subtype: str
    risk_score: float           # calibrated probability of denial
    risk_level: str             # HIGH / MEDIUM / LOW
    top_denial_reasons: list[dict[str, Any]] = field(default_factory=list)


def _safe_str_or_none(v: Any) -> str | None:
    """Map (None, pandas NaN, empty/whitespace string) → None; otherwise str(v).

    Pandas reads NULL columns as `float('nan')` (object-dtype Series can hold
    floats), and `float('nan')` is neither `None` nor a string — so it slips
    past `str | None` Pydantic schemas at the API boundary and raises a
    ValidationError (see CR-060). Use this helper at every spot where a
    DataFrame cell is assigned to a downstream `str | None` field.
    """
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    s = str(v).strip()
    return s or None


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

_TRAINING_SQL = """
    SELECT
        cl.id              AS claim_id,
        cl.claim_number    AS claim_number,
        cl.service_variant AS service_variant,
        cl.claim_subtype   AS claim_subtype,
        cl.total_charge_amount::float8 AS total_charge_amount,
        cl.authorization_number IS NOT NULL AS has_authorization,
        cl.referral_number IS NOT NULL      AS has_referral,
        cl.frequency_code,
        p.canonical_name AS payer_name,
        (SELECT count(*) FROM claim_lines lin WHERE lin.claim_id = cl.id) AS line_count,
        (SELECT count(*) FROM diagnoses dx   WHERE dx.claim_id = cl.id) AS diagnosis_count,
        (SELECT lin.procedure_code FROM claim_lines lin
         WHERE lin.claim_id = cl.id ORDER BY lin.line_number LIMIT 1) AS primary_procedure,
        (SELECT dx.diagnosis_code FROM diagnoses dx
         WHERE dx.claim_id = cl.id ORDER BY dx.sequence_number LIMIT 1) AS primary_diagnosis,
        EXISTS (
            SELECT 1 FROM remittance_claims rc
            WHERE rc.claim_id = cl.id AND rc.claim_status_code = '4'
        ) AS denied
    FROM claims cl
    LEFT JOIN payers p ON p.id = cl.payer_id
    WHERE cl.deleted_at IS NULL
      AND EXISTS (SELECT 1 FROM remittance_claims rc WHERE rc.claim_id = cl.id)
"""

_PREDICT_FILE_SQL = """
    SELECT
        cl.id              AS claim_id,
        cl.claim_number    AS claim_number,
        cl.service_variant AS service_variant,
        cl.claim_subtype   AS claim_subtype,
        cl.total_charge_amount::float8 AS total_charge_amount,
        cl.authorization_number IS NOT NULL AS has_authorization,
        cl.referral_number IS NOT NULL      AS has_referral,
        cl.frequency_code,
        p.canonical_name AS payer_name,
        (SELECT count(*) FROM claim_lines lin WHERE lin.claim_id = cl.id) AS line_count,
        (SELECT count(*) FROM diagnoses dx   WHERE dx.claim_id = cl.id) AS diagnosis_count,
        (SELECT lin.procedure_code FROM claim_lines lin
         WHERE lin.claim_id = cl.id ORDER BY lin.line_number LIMIT 1) AS primary_procedure,
        (SELECT dx.diagnosis_code FROM diagnoses dx
         WHERE dx.claim_id = cl.id ORDER BY dx.sequence_number LIMIT 1) AS primary_diagnosis
    FROM claims cl
    LEFT JOIN payers p ON p.id = cl.payer_id
    WHERE cl.edi_file_id = :file_id AND cl.deleted_at IS NULL
"""


async def _fetch_df(session: AsyncSession, sql: str, **params) -> pd.DataFrame:
    rows = (await session.execute(text(sql), params)).mappings().all()
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Feature engineering — fixed, deterministic, no label leakage
# ---------------------------------------------------------------------------

def _build_vocabs(df: pd.DataFrame) -> tuple[list[str], list[str], list[str], list[str]]:
    payer = (df["payer_name"].fillna("__UNK__")
                .value_counts().head(_TOP_PAYERS).index.tolist())
    proc  = (df["primary_procedure"].fillna("__UNK__")
                .value_counts().head(_TOP_PROCEDURES).index.tolist())
    dx    = (df["primary_diagnosis"].fillna("__UNK__")
                .value_counts().head(_TOP_DIAGNOSES).index.tolist())
    subt  = df["claim_subtype"].fillna("__UNK__").unique().tolist()
    return payer, proc, dx, subt


def _featurize(
    df: pd.DataFrame,
    payer_vocab: list[str],
    procedure_vocab: list[str],
    diagnosis_vocab: list[str],
    subtype_vocab: list[str],
) -> pd.DataFrame:
    """Produce a numeric feature matrix. All categorical columns are one-hot
    against their vocabs; unknown values fold into an `_OTHER` slot so the
    column set is stable between train and predict time."""
    n = len(df)

    out = pd.DataFrame(index=df.index)
    out["total_charge_amount"]  = df["total_charge_amount"].fillna(0.0).astype(float)
    out["log_total_charge"]     = np.log1p(out["total_charge_amount"].clip(lower=0))
    out["line_count"]           = df["line_count"].fillna(0).astype(int)
    out["diagnosis_count"]      = df["diagnosis_count"].fillna(0).astype(int)
    out["has_authorization"]    = df["has_authorization"].fillna(False).astype(int)
    out["has_referral"]         = df["has_referral"].fillna(False).astype(int)
    out["is_replacement_freq"]  = (df["frequency_code"].fillna("") == "7").astype(int)
    out["is_void_freq"]         = (df["frequency_code"].fillna("") == "8").astype(int)

    for v in _VARIANT_BUCKETS:
        out[f"variant_{v}"] = (df["service_variant"] == v).astype(int)

    for s in subtype_vocab:
        out[f"subtype_{s}"] = (df["claim_subtype"] == s).astype(int)
    out["subtype__OTHER"] = (~df["claim_subtype"].isin(subtype_vocab)).astype(int)

    payer_col = df["payer_name"].fillna("__UNK__")
    for p in payer_vocab:
        out[f"payer_{p}"] = (payer_col == p).astype(int)
    out["payer__OTHER"] = (~payer_col.isin(payer_vocab)).astype(int)

    proc_col = df["primary_procedure"].fillna("__UNK__")
    for c in procedure_vocab:
        out[f"procedure_{c}"] = (proc_col == c).astype(int)
    out["procedure__OTHER"] = (~proc_col.isin(procedure_vocab)).astype(int)

    dx_col = df["primary_diagnosis"].fillna("__UNK__")
    for d in diagnosis_vocab:
        out[f"diagnosis_{d}"] = (dx_col == d).astype(int)
    out["diagnosis__OTHER"] = (~dx_col.isin(diagnosis_vocab)).astype(int)

    return out


def _feature_labels(columns: list[str]) -> dict[str, str]:
    """Map technical feature names → human-readable labels for the UI."""
    labels: dict[str, str] = {}
    for col in columns:
        if col == "total_charge_amount":
            labels[col] = "Billed amount"
        elif col == "log_total_charge":
            labels[col] = "Billed amount (scale)"
        elif col == "line_count":
            labels[col] = "Number of service lines"
        elif col == "diagnosis_count":
            labels[col] = "Number of diagnoses"
        elif col == "has_authorization":
            labels[col] = "Authorization number present"
        elif col == "has_referral":
            labels[col] = "Referral number present"
        elif col == "is_replacement_freq":
            labels[col] = "Replacement claim (frequency=7)"
        elif col == "is_void_freq":
            labels[col] = "Void claim (frequency=8)"
        elif col.startswith("variant_"):
            labels[col] = f"Service variant: {col[len('variant_'):]}"
        elif col.startswith("subtype_"):
            labels[col] = f"Claim subtype: {col[len('subtype_'):]}"
        elif col.startswith("payer_"):
            v = col[len("payer_"):]
            labels[col] = f"Payer: {v if v != '__UNK__' else 'unknown'}"
        elif col.startswith("procedure_"):
            v = col[len("procedure_"):]
            labels[col] = f"Procedure code: {v if v != '__UNK__' else 'unknown'}"
        elif col.startswith("diagnosis_"):
            v = col[len("diagnosis_"):]
            labels[col] = f"Diagnosis code: {v if v != '__UNK__' else 'unknown'}"
        else:
            labels[col] = col
    return labels


# ---------------------------------------------------------------------------
# Train
# ---------------------------------------------------------------------------

async def train(
    session: AsyncSession,
    artifact_path: Path,
    *,
    test_size: float = 0.2,
    random_state: int = 42,
) -> dict[str, Any]:
    """Pull adjudicated claims, fit XGBoost, save artifact, return metrics.

    Raises NoTrainingDataError if there are fewer than 10 adjudicated claims
    (the UI translates this to a friendly "upload + adjudicate more claims"
    message rather than a crash)."""
    df = await _fetch_df(session, _TRAINING_SQL)
    if df.empty or len(df) < 10:
        raise NoTrainingDataError(
            f"Need at least 10 adjudicated claims to train; have {len(df)}. "
            "Upload more 837 / 835 pairs first."
        )

    y = df["denied"].astype(int).to_numpy()
    if y.sum() == 0 or y.sum() == len(y):
        raise NoTrainingDataError(
            f"Need both denied and paid claims to train; got "
            f"{int(y.sum())}/{len(y)} denied."
        )

    payer_vocab, proc_vocab, dx_vocab, subtype_vocab = _build_vocabs(df)
    X = _featurize(df, payer_vocab, proc_vocab, dx_vocab, subtype_vocab)
    feature_cols = list(X.columns)

    # Holdout split (stratified). Small dataset → guarantee both classes in test.
    test_size = min(test_size, max(0.1, 2 / len(y)))
    if min(int(y.sum()), int((1 - y).sum())) < 2:
        X_train, X_test, y_train, y_test = X, X.iloc[:0], y, y[:0]
    else:
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=test_size, stratify=y, random_state=random_state,
        )

    n_pos = int(y_train.sum())
    n_neg = int((1 - y_train).sum())
    spw = (n_neg / max(n_pos, 1)) if n_pos > 0 else 1.0

    base = XGBClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.08,
        random_state=random_state,
        scale_pos_weight=spw,
        eval_metric="logloss",
        tree_method="hist",
        n_jobs=1,
    )
    t0 = time.perf_counter()
    base.fit(X_train.to_numpy(), y_train)

    # Calibrate via 5-fold (or fewer if data is tiny)
    n_cv = min(5, n_pos, n_neg) if min(n_pos, n_neg) >= 2 else 0
    if n_cv >= 2:
        calibrator = CalibratedClassifierCV(
            estimator=XGBClassifier(
                n_estimators=200, max_depth=4, learning_rate=0.08,
                random_state=random_state, scale_pos_weight=spw,
                eval_metric="logloss", tree_method="hist", n_jobs=1,
            ),
            method="isotonic",
            cv=n_cv,
        )
        calibrator.fit(X_train.to_numpy(), y_train)
    else:
        calibrator = None

    train_seconds = time.perf_counter() - t0

    # Eval on holdout (if it exists). Pick a threshold from the TRAIN scores
    # so HIGH-risk actually fires — fixed 0.5 collapses to all-negative when
    # the calibrator is conservative, which makes the UI look broken.
    train_proba = (calibrator.predict_proba(X_train.to_numpy())[:, 1]
                   if calibrator is not None
                   else base.predict_proba(X_train.to_numpy())[:, 1])
    pos_scores = train_proba[y_train == 1]
    if len(pos_scores) > 0:
        # HIGH threshold = the lower of: 25th percentile of denied scores,
        # or median of ALL scores (so even on a weak model we still surface
        # the riskiest ~half as HIGH and the user actually sees the SHAP
        # explanations the model is producing).
        pct_denied = float(np.percentile(pos_scores, 25))
        median_all = float(np.median(train_proba))
        threshold = float(max(0.15, min(0.50, min(pct_denied, median_all))))
    else:
        threshold = 0.5

    if len(y_test) > 0:
        proba = (calibrator.predict_proba(X_test.to_numpy())[:, 1]
                 if calibrator is not None
                 else base.predict_proba(X_test.to_numpy())[:, 1])
        y_pred = (proba >= threshold).astype(int)
        metrics = {
            "accuracy":  float(accuracy_score(y_test, y_pred)),
            "precision": float(precision_score(y_test, y_pred, zero_division=0)),
            "recall":    float(recall_score(y_test, y_pred, zero_division=0)),
            "f1":        float(f1_score(y_test, y_pred, zero_division=0)),
            "roc_auc":   float(roc_auc_score(y_test, proba))
                            if len(set(y_test.tolist())) == 2 else None,
        }
    else:
        metrics = {
            "accuracy": None, "precision": None, "recall": None,
            "f1": None, "roc_auc": None,
        }

    artifact = ModelArtifact(
        booster=base,
        calibrator=calibrator,
        feature_columns=feature_cols,
        payer_vocab=payer_vocab,
        procedure_vocab=proc_vocab,
        diagnosis_vocab=dx_vocab,
        subtype_vocab=subtype_vocab,
        decision_threshold=threshold,
        model_version=f"v1.simple.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
        trained_at=datetime.now(timezone.utc).isoformat(),
        n_training_samples=int(len(y_train)),
        n_test_samples=int(len(y_test)),
        metrics=metrics,
        feature_labels=_feature_labels(feature_cols),
    )

    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    with open(artifact_path, "wb") as f:
        pickle.dump(artifact, f)
    logger.info("Saved model artifact to %s (rows=%d, denied=%d)",
                artifact_path, len(y), int(y.sum()))

    return {
        "status": "success",
        "split": {
            "train_samples": int(len(y_train)),
            "test_samples": int(len(y_test)),
        },
        "metrics": metrics,
        "training_time_seconds": round(train_seconds, 3),
        "model_version": artifact.model_version,
        "n_features": len(feature_cols),
        "n_denied": int(y.sum()),
        "n_paid": int(len(y) - y.sum()),
    }


# ---------------------------------------------------------------------------
# Predict + SHAP
# ---------------------------------------------------------------------------

def load_artifact(artifact_path: Path) -> ModelArtifact:
    with open(artifact_path, "rb") as f:
        return pickle.load(f)


def _risk_level(score: float, threshold: float) -> str:
    if score >= threshold:           return "HIGH"
    if score >= max(threshold * 0.5, 0.20):  return "MEDIUM"
    return "LOW"


def _explain_feature(
    col: str,
    claim_row: dict[str, Any],
) -> str | None:
    """Convert one (feature_column, claim_row) into a plain-English sentence
    explaining a CLAIM-SPECIFIC data gap or anomaly.

    Deliberately returns None for categorical-prior features (payer, variant,
    subtype, procedure code, diagnosis code). Those reflect historical denial
    rates against a category — they aren't legitimate reasons to deny any one
    claim. A legitimate claim from a high-denial-rate payer is still a
    legitimate claim. Only specific data gaps / anomalies on THIS claim are
    surfaced as reasons."""

    # ----- claim-specific gaps (missing required fields, anomalies) -----

    if col == "diagnosis_count":
        n = int(claim_row.get("diagnosis_count") or 0)
        if n == 0:
            return (
                "This claim has no diagnoses attached, so the payer cannot "
                "verify medical necessity."
            )
        return None  # non-zero counts aren't a specific issue

    if col == "has_authorization":
        if not claim_row.get("has_authorization"):
            return (
                "No authorization number is on this claim — if the service "
                "requires prior authorization the payer will deny it."
            )
        return None

    if col == "has_referral":
        if not claim_row.get("has_referral"):
            return (
                "No referral number is on this claim — if a referral was "
                "required the claim will be denied."
            )
        return None

    if col == "is_replacement_freq":
        # Only flag as a reason if this IS a replacement AND the original
        # claim control number is missing (a real data gap, not a category).
        if (claim_row.get("frequency_code") == "7"
            and not claim_row.get("previous_payer_claim_control_no")):
            return (
                "This is a replacement claim but the original payer claim "
                "control number is missing — payers treat replacements "
                "without that link as duplicates and deny them."
            )
        return None

    if col == "is_void_freq":
        if (claim_row.get("frequency_code") == "8"
            and not claim_row.get("previous_payer_claim_control_no")):
            return (
                "This is a void claim but the original payer claim control "
                "number is missing — voids without the matching control "
                "number are denied."
            )
        return None

    # Numeric anomalies: only call out when the value is extreme enough that
    # a billing operator would recognise it as off. We don't have per-payer /
    # per-procedure benchmarks loaded yet, so the heuristic is conservative
    # (zero charge, zero lines) — generic "billed amount is unusual" without
    # a benchmark isn't actionable.
    if col == "total_charge_amount" or col == "log_total_charge":
        amt = float(claim_row.get("total_charge_amount") or 0)
        if amt <= 0:
            return (
                "The total billed amount on this claim is zero or missing — "
                "payers reject claims with no chargeable total."
            )
        return None

    if col == "line_count":
        n = int(claim_row.get("line_count") or 0)
        if n == 0:
            return (
                "This claim has no service lines — claims without at least "
                "one SV1/SV2/SV3 line cannot be adjudicated."
            )
        return None

    # ----- categorical priors (payer, variant, subtype, primary code) -----
    # Intentionally skipped: a high historical denial rate for a payer or a
    # procedure code is not a defensible reason to predict any one claim's
    # denial. Those signals still drive the *probability*, but the user-visible
    # reason should point to something specific about this claim.
    if (col.startswith(("payer_", "variant_", "subtype_",
                        "procedure_", "diagnosis_"))
        or col in ("payer__OTHER", "procedure__OTHER", "diagnosis__OTHER",
                   "subtype__OTHER")):
        return None

    return None


def _shap_to_reasons(
    feature_row: pd.Series,
    claim_row: dict[str, Any],
    shap_values: np.ndarray,
    feature_labels: dict[str, str],
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """Convert a single claim's SHAP vector to top-K plain-English reasons.

    Filters to features whose SHAP value is positive (pushes toward 'denied')
    AND that actually apply to this claim (one-hot columns for payers /
    procedures the claim doesn't have are irrelevant). De-duplicates reason
    text in case two correlated features produce the same sentence."""
    reasons: list[dict[str, Any]] = []
    seen: set[str] = set()
    order = np.argsort(shap_values)[::-1]
    for idx in order:
        contrib = float(shap_values[idx])
        if contrib <= 0:
            continue
        col = feature_row.index[idx]
        sentence = _explain_feature(col, claim_row)
        if sentence is None or sentence in seen:
            continue
        seen.add(sentence)
        reasons.append({
            "feature": col,
            "label": feature_labels.get(col, col),
            "reason": sentence,
            "impact": round(contrib, 3),
            "direction": "increases denial risk",
        })
        if len(reasons) >= top_k:
            break
    return reasons


async def predict_file(
    session: AsyncSession,
    artifact_path: Path,
    edi_file_id: int,
) -> list[ScoredClaim]:
    """Predict every claim on this file. Returns one ScoredClaim per claim."""
    artifact = load_artifact(artifact_path)
    df = await _fetch_df(session, _PREDICT_FILE_SQL, file_id=edi_file_id)
    if df.empty:
        return []

    X = _featurize(
        df, artifact.payer_vocab, artifact.procedure_vocab,
        artifact.diagnosis_vocab, artifact.subtype_vocab,
    )
    # Align columns with training (extra cols dropped, missing cols added=0)
    X = X.reindex(columns=artifact.feature_columns, fill_value=0)

    if artifact.calibrator is not None:
        proba = artifact.calibrator.predict_proba(X.to_numpy())[:, 1]
    else:
        proba = artifact.booster.predict_proba(X.to_numpy())[:, 1]

    # SHAP via xgboost tree path on the BASE booster (calibrator is sklearn,
    # SHAP isn't directly meaningful through it — use base for explanation).
    import xgboost as xgb
    dmat = xgb.DMatrix(X.to_numpy(), feature_names=list(X.columns))
    try:
        contribs = artifact.booster.get_booster().predict(dmat, pred_contribs=True)
        # Last column is the bias term — drop it
        contribs = np.asarray(contribs)[:, :-1]
    except Exception as exc:
        logger.warning("SHAP failed (%s); reasons will be empty", exc)
        contribs = np.zeros_like(X.to_numpy())

    # CR-092 Issue 2: route simple_pipeline's raw-feature reasons through
    # the canonical renderer so the API never exposes internal column names
    # like ``is_replacement_freq`` or per-payer one-hot columns. This is the
    # SAME render_risk_factors call the FB-primary path uses, so both paths
    # emit the same bucket-form reason rows. Unknown features collapse to
    # ``general`` (REASON_GENERAL) per the renderer contract.
    from rcm.ml.reason_renderer import render_risk_factors

    out: list[ScoredClaim] = []
    for i, row in df.iterrows():
        risk = float(proba[i])
        level = _risk_level(risk, artifact.decision_threshold)
        raw_reasons = _shap_to_reasons(
            X.iloc[i], row.to_dict(), contribs[i], artifact.feature_labels,
        )
        subtype = _safe_str_or_none(row.get("claim_subtype")) or None
        # render_risk_factors expects {feature, impact} pairs and produces the
        # bucket-form rows (slug/title/sentence/impact/direction). For features
        # not in the renderer's _FEATURE_TO_BUCKET map (e.g. simple_pipeline's
        # one-hot payer/CPT columns), the lookup falls back to REASON_GENERAL.
        rendered_reasons = render_risk_factors(
            [{"feature": r.get("feature"), "impact": r.get("impact", 0.0)}
             for r in raw_reasons],
            top_k=5,
            claim_subtype=subtype,
        )
        # CR-060: every field whose downstream Pydantic type is `str | None`
        # goes through _safe_str_or_none to coerce NaN → None. Fields with
        # strict-required `str` schemas (claim_number) use the direct str()
        # path because their source columns are NOT NULL in the DB.
        out.append(ScoredClaim(
            claim_id=int(row["claim_id"]),
            claim_number=str(row["claim_number"]),
            payer_name=_safe_str_or_none(row.get("payer_name")),
            service_variant=_safe_str_or_none(row.get("service_variant")) or "",
            claim_subtype=subtype or "",
            risk_score=risk,
            risk_level=level,
            top_denial_reasons=rendered_reasons,
        ))
    return out


# ---------------------------------------------------------------------------
# Friendly errors
# ---------------------------------------------------------------------------

class NoTrainingDataError(Exception):
    """Raised when there isn't enough adjudicated data to fit anything sensible."""
