"""CR-117 Stage 1 + CR-118 Stage 2 — lifecycle-aware features.

Eleven features comparing a replacement claim (freq=7) to its original
(freq=1, matched via (claim_number, payer_id IS NOT DISTINCT FROM)):

  Stage 1 (CR-117):
    had_prior_denial               int8  — 1 iff original was denied (CLP02='4') strictly before this row
    prior_denial_bucket            int8  — canonical denial bucket of the original's top CARC, encoded
    days_since_original_denial     int16 — replacement.service_from_date - original.remittance_date, clipped [0, 365]
    auth_added_in_replacement      int8  — original.authorization_number IS NULL AND replacement.authorization_number IS NOT NULL
    referral_added_in_replacement  int8  — symmetric for referral_number

  Stage 2 (CR-118):
    modifier_added_in_replacement     int8  — replacement carries ≥1 modifier the original did not
    diagnosis_changed_in_replacement  int8  — set(replacement.diagnoses) != set(original.diagnoses)
    procedure_changed_in_replacement  int8  — set(replacement.procedure_codes) != set(original.procedure_codes)
    lines_changed_in_replacement      int16 — replacement.claim_lines_count - original.claim_lines_count (clipped [-99, +99])
    charge_changed_in_replacement     int8  — sign(replacement.total_charge_amount - original.total_charge_amount) ∈ {-1, 0, +1}

  correction_action_count          int8  — sum of all 7 binary correction flags (max 7)

The compute is exposed as a standalone function and is NOT called from
`FeatureBuilder._assemble` until Stage 3 (registry + booster retrain).

Leakage protection lives in `dataset.load_original_snapshots()` (SQL
`remittance_date < replacement.service_from_date` for the outcome-derived
features). Stage 2 features are structural diffs only — no labels touched,
no future state touched — so they are leakage-safe by construction.
"""

from __future__ import annotations

from typing import Any, Iterable

import numpy as np
import pandas as pd


LIFECYCLE_FEATURE_COLUMNS: tuple[str, ...] = (
    "had_prior_denial",
    "prior_denial_bucket",
    "days_since_original_denial",
    "auth_added_in_replacement",
    "referral_added_in_replacement",
    "modifier_added_in_replacement",
    "diagnosis_changed_in_replacement",
    "procedure_changed_in_replacement",
    "lines_changed_in_replacement",
    "charge_changed_in_replacement",
    "correction_action_count",
)


# Canonical reason-bucket → int encoding. Matches the 11 buckets defined
# in `rcm.ml.denial_buckets._VALID_BUCKETS`. Unknown / missing → 0.
_BUCKET_TO_INT: dict[str, int] = {
    "general":       0,
    "history":       1,
    "similar":       2,
    "authorization": 3,
    "coverage":      4,
    "documentation": 5,
    "procedure":     6,
    "diagnosis":     7,
    "timely_filing": 8,
    "billing":       9,
    "provider":     10,
}


_INT16_COLUMNS: frozenset[str] = frozenset({
    "days_since_original_denial",
    "lines_changed_in_replacement",
})


def _empty_frame(index: pd.Index) -> pd.DataFrame:
    """Return an all-zero frame with the canonical column set."""
    n = len(index)
    out = pd.DataFrame(index=index)
    for c in LIFECYCLE_FEATURE_COLUMNS:
        dtype = "int16" if c in _INT16_COLUMNS else "int8"
        out[c] = pd.Series(np.zeros(n, dtype=dtype), index=index)
    return out


def _present(series: pd.Series | None) -> np.ndarray:
    """True iff each value is a non-empty stripped string. NA/None → False."""
    if series is None:
        return np.zeros(0, dtype=bool)
    s = series.astype("string").fillna("").str.strip()
    return (s != "").to_numpy(dtype=bool, na_value=False)


def _to_bool_array(s: pd.Series, *, na_value: bool = False) -> np.ndarray:
    """Robust nullable-boolean → numpy bool conversion."""
    if hasattr(s, "to_numpy"):
        return s.to_numpy(dtype=bool, na_value=na_value)
    return np.asarray(s, dtype=bool)


def _coerce_to_set(value: Any) -> frozenset[str]:
    """Normalise a cell value to a frozenset of non-empty strings.

    Accepts: None / NaN → empty set; list / tuple / set of scalars; list of
    dicts (extracts 'code' or 'diagnosis_code' — handles the load_training_corpus
    diagnoses-as-dicts shape). Anything else collapses to empty.
    """
    if value is None:
        return frozenset()
    # pandas can sneak NaN floats into object columns
    if isinstance(value, float) and pd.isna(value):
        return frozenset()
    if isinstance(value, str):
        return frozenset([value]) if value.strip() else frozenset()
    if isinstance(value, (list, tuple, set, frozenset)):
        out: set[str] = set()
        for v in value:
            if v is None:
                continue
            if isinstance(v, dict):
                code = v.get("code") or v.get("diagnosis_code") or v.get("procedure_code")
                if code:
                    out.add(str(code).strip())
            elif isinstance(v, float) and pd.isna(v):
                continue
            else:
                s = str(v).strip()
                if s:
                    out.add(s)
        return frozenset(out)
    return frozenset()


def compute(df: pd.DataFrame, originals: pd.DataFrame) -> pd.DataFrame:
    """Compute Stage-1 lifecycle features.

    Parameters
    ----------
    df
        Per-claim DataFrame indexed by ``claim_id``. Required columns:
        ``frequency_code``, ``authorization_number``, ``referral_number``,
        ``service_from_date``. Other columns are ignored.
    originals
        Per-replacement original snapshot, indexed by ``child_id`` (the
        replacement's ``claim_id``). Produced by
        ``rcm.features.dataset.load_original_snapshots``. Expected columns:
        ``original_id`` (Int64 | NaN),
        ``original_claim_status_code`` (str | None),
        ``original_remittance_date`` (date | None),
        ``original_authorization_number`` (str | None),
        ``original_referral_number`` (str | None),
        ``original_top_carc_bucket`` (str | None — canonical reason bucket).
        Rows missing from this frame for a given ``claim_id`` are treated as
        "no resolvable original" and emit zeros.

    Returns
    -------
    pd.DataFrame
        Indexed identically to ``df`` with six columns in the canonical
        ``LIFECYCLE_FEATURE_COLUMNS`` order. Non-freq=7 rows are forced to
        zero on all six columns (defensive — these features only mean
        something for replacement claims).
    """
    if len(df) == 0:
        return _empty_frame(df.index)

    # Align originals to df.index. Rows without an original get NaN.
    orig = originals.reindex(df.index) if not originals.empty else pd.DataFrame(index=df.index)

    # Mask: only freq=7 rows can have non-zero lifecycle features.
    freq = df.get("frequency_code")
    if freq is None:
        is_freq7 = np.zeros(len(df), dtype=bool)
    else:
        is_freq7 = (
            freq.astype("string").fillna("").str.strip() == "7"
        ).to_numpy(dtype=bool, na_value=False)

    out = pd.DataFrame(index=df.index)

    # ---- had_prior_denial ------------------------------------------------
    status = orig.get("original_claim_status_code")
    if status is None:
        had_prior = np.zeros(len(df), dtype=bool)
    else:
        had_prior = (
            status.astype("string").fillna("").str.strip() == "4"
        ).to_numpy(dtype=bool, na_value=False)
    out["had_prior_denial"] = (had_prior & is_freq7).astype("int8")
    had_arr = out["had_prior_denial"].to_numpy(dtype="int8")

    # ---- prior_denial_bucket --------------------------------------------
    bucket = orig.get("original_top_carc_bucket")
    if bucket is None:
        bucket_int = np.zeros(len(df), dtype="int8")
    else:
        bucket_int = (
            bucket.astype("string")
                  .str.strip()
                  .str.lower()
                  .map(_BUCKET_TO_INT)
                  .fillna(0)
                  .to_numpy(dtype="int8", na_value=0)
        )
    # Bucket is only meaningful if a prior denial actually exists.
    out["prior_denial_bucket"] = (bucket_int * had_arr).astype("int8")

    # ---- days_since_original_denial -------------------------------------
    repl_d = pd.to_datetime(df.get("service_from_date"), errors="coerce")
    orig_d_raw = orig.get("original_remittance_date")
    if orig_d_raw is None:
        days = np.zeros(len(df), dtype="int16")
    else:
        orig_d = pd.to_datetime(orig_d_raw, errors="coerce")
        delta_days = (repl_d - orig_d).dt.days
        days = delta_days.clip(lower=0, upper=365).fillna(0).to_numpy(dtype="int16", na_value=0)
    # Only emit when there's a real prior denial AND it's a freq=7 row.
    out["days_since_original_denial"] = (days * had_arr.astype("int16")).astype("int16")

    # ---- auth_added_in_replacement --------------------------------------
    out["auth_added_in_replacement"] = _field_added(
        df.get("authorization_number"),
        orig.get("original_authorization_number"),
        is_freq7,
    )

    # ---- referral_added_in_replacement ----------------------------------
    out["referral_added_in_replacement"] = _field_added(
        df.get("referral_number"),
        orig.get("original_referral_number"),
        is_freq7,
    )

    # ====================================================================
    # CR-118 Stage 2 — correction-delta features
    # ====================================================================
    has_original = (
        orig["original_id"].notna().to_numpy(dtype=bool)
        if "original_id" in orig.columns
        else np.zeros(len(df), dtype=bool)
    )

    # ---- modifier_added_in_replacement ----------------------------------
    out["modifier_added_in_replacement"] = _set_added(
        df.get("modifiers"),
        orig.get("original_modifiers"),
        is_freq7, has_original,
    )

    # ---- diagnosis_changed_in_replacement -------------------------------
    out["diagnosis_changed_in_replacement"] = _set_changed(
        df.get("diagnoses") if "diagnoses" in df.columns else df.get("diagnosis_codes"),
        orig.get("original_diagnosis_codes"),
        is_freq7, has_original,
    )

    # ---- procedure_changed_in_replacement -------------------------------
    out["procedure_changed_in_replacement"] = _set_changed(
        df.get("procedure_codes"),
        orig.get("original_procedure_codes"),
        is_freq7, has_original,
    )

    # ---- lines_changed_in_replacement -----------------------------------
    out["lines_changed_in_replacement"] = _delta_int16(
        df.get("claim_lines_count"),
        orig.get("original_claim_lines_count"),
        is_freq7, has_original, clip_lo=-99, clip_hi=99,
    )

    # ---- charge_changed_in_replacement ----------------------------------
    out["charge_changed_in_replacement"] = _delta_sign(
        df.get("total_charge_amount"),
        orig.get("original_total_charge_amount"),
        is_freq7, has_original,
    )

    # ---- correction_action_count (Stage 1 + Stage 2 combined) -----------
    # Sum of the 7 binary correction flags. Two features carry magnitudes
    # (lines_changed_in_replacement int16, charge_changed_in_replacement
    # signed int8); for the count we only care whether they're non-zero.
    flags = [
        out["auth_added_in_replacement"].to_numpy(dtype="int8"),
        out["referral_added_in_replacement"].to_numpy(dtype="int8"),
        out["modifier_added_in_replacement"].to_numpy(dtype="int8"),
        out["diagnosis_changed_in_replacement"].to_numpy(dtype="int8"),
        out["procedure_changed_in_replacement"].to_numpy(dtype="int8"),
        (out["lines_changed_in_replacement"].to_numpy(dtype="int16") != 0).astype("int8"),
        (out["charge_changed_in_replacement"].to_numpy(dtype="int8") != 0).astype("int8"),
    ]
    out["correction_action_count"] = np.sum(flags, axis=0).astype("int8")

    return out[list(LIFECYCLE_FEATURE_COLUMNS)]


# ---- Stage 2 helpers --------------------------------------------------------

def _set_added(
    replacement: pd.Series | None,
    original: pd.Series | None,
    is_freq7: np.ndarray,
    has_original: np.ndarray,
) -> np.ndarray:
    """1 iff (set(replacement) - set(original)) is non-empty AND row is freq=7
    AND an original was resolved. When the original is missing, emit 0 (we
    don't penalise the model with a spurious 'added' signal we can't justify)."""
    n = len(is_freq7)
    if replacement is None or original is None:
        return np.zeros(n, dtype="int8")
    out = np.zeros(n, dtype=bool)
    for i, (a, b) in enumerate(zip(replacement, original)):
        if not (is_freq7[i] and has_original[i]):
            continue
        repl_set = _coerce_to_set(a)
        orig_set = _coerce_to_set(b)
        out[i] = bool(repl_set - orig_set)
    return out.astype("int8")


def _set_changed(
    replacement: pd.Series | None,
    original: pd.Series | None,
    is_freq7: np.ndarray,
    has_original: np.ndarray,
) -> np.ndarray:
    """1 iff set(replacement) != set(original) AND row is freq=7 AND an
    original was resolved. Detects additions OR removals OR swaps."""
    n = len(is_freq7)
    if replacement is None or original is None:
        return np.zeros(n, dtype="int8")
    out = np.zeros(n, dtype=bool)
    for i, (a, b) in enumerate(zip(replacement, original)):
        if not (is_freq7[i] and has_original[i]):
            continue
        repl_set = _coerce_to_set(a)
        orig_set = _coerce_to_set(b)
        out[i] = repl_set != orig_set
    return out.astype("int8")


def _delta_int16(
    replacement: pd.Series | None,
    original: pd.Series | None,
    is_freq7: np.ndarray,
    has_original: np.ndarray,
    *, clip_lo: int, clip_hi: int,
) -> np.ndarray:
    """Signed delta clipped to [clip_lo, clip_hi]. Zero when the row is not
    freq=7 OR the original is missing OR either input is NaN."""
    n = len(is_freq7)
    if replacement is None or original is None:
        return np.zeros(n, dtype="int16")
    repl = pd.to_numeric(replacement, errors="coerce").to_numpy(dtype="float64", na_value=np.nan)
    orig = pd.to_numeric(original, errors="coerce").to_numpy(dtype="float64", na_value=np.nan)
    valid = is_freq7 & has_original & ~np.isnan(repl) & ~np.isnan(orig)
    delta = np.where(valid, repl - orig, 0.0)
    return np.clip(delta, clip_lo, clip_hi).astype("int16")


def _delta_sign(
    replacement: pd.Series | None,
    original: pd.Series | None,
    is_freq7: np.ndarray,
    has_original: np.ndarray,
) -> np.ndarray:
    """Sign of the delta ∈ {-1, 0, +1}. Zero when the row is not freq=7 OR
    the original is missing OR either input is NaN, OR the delta is exactly 0."""
    n = len(is_freq7)
    if replacement is None or original is None:
        return np.zeros(n, dtype="int8")
    repl = pd.to_numeric(replacement, errors="coerce").to_numpy(dtype="float64", na_value=np.nan)
    orig = pd.to_numeric(original, errors="coerce").to_numpy(dtype="float64", na_value=np.nan)
    valid = is_freq7 & has_original & ~np.isnan(repl) & ~np.isnan(orig)
    diff = np.where(valid, repl - orig, 0.0)
    sign = np.sign(diff).astype("int8")
    return sign


def _field_added(
    replacement: pd.Series | None,
    original: pd.Series | None,
    is_freq7: np.ndarray,
) -> np.ndarray:
    """True iff original is NULL/blank AND replacement is non-blank, AND
    the row is a freq=7 replacement.

    A blank string is treated identically to NULL — operators occasionally
    submit empty `''` in the EDI for missing fields. Returned as int8 array
    aligned to the input series order.
    """
    n = len(is_freq7)
    if replacement is None or original is None:
        return np.zeros(n, dtype="int8")
    repl_present = _present(replacement)
    orig_present = _present(original)
    added = (~orig_present) & repl_present & is_freq7
    return added.astype("int8")
