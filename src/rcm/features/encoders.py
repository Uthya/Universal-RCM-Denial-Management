"""Leakage-safe target encoder.

Pattern: at FIT time, use sklearn's TargetEncoder with the cross_val_predict
mechanism so each training row's encoded value comes from a fold that did
NOT include that row's target. At TRANSFORM time (validation, test, predict),
use the full-fit encoder (every fold's data combined).

Why this matters: a naive target encoder leaks the row's own label into its
feature, which inflates training metrics and produces a model that fails to
generalize. The cv= argument to sklearn's TargetEncoder produces the
fold-aware transform automatically when calling fit_transform on training,
then a deterministic global transform on inference data.

This wrapper additionally tracks:
    * the training vocabulary per encoded column (used by Category L for
      unseen flags)
    * the global mean target (used as the unseen-category fallback)
    * the version of the encoder, persisted to disk so a model artifact's
      encoder bundle can be reloaded reproducibly at predict time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.preprocessing import TargetEncoder

from rcm.features.constants import (
    MISSING_CATEGORICAL_SENTINEL,
    TARGET_ENCODER_CV_FOLDS,
    TARGET_ENCODER_RANDOM_STATE,
    TARGET_ENCODER_SMOOTHING,
)

logger = logging.getLogger(__name__)


ENCODER_BUNDLE_VERSION = "v1.0.0"


@dataclass
class _PerColumnState:
    """Per-encoded-column state. Saved together as the encoder bundle."""
    column: str
    encoder: TargetEncoder
    vocabulary: frozenset[str]
    global_mean: float
    n_training_rows: int


@dataclass
class LeakageSafeTargetEncoder:
    """Manages target encoders for N categorical columns at once.

    Usage:
        enc = LeakageSafeTargetEncoder(columns=["payer_name", "primary_cpt", ...])
        encoded_df_train = enc.fit_transform(X_train, y_train)   # leakage-safe (OOF)
        encoded_df_test  = enc.transform(X_test)                 # global transform
        enc.save(Path("artifacts/healthcare/encoder.joblib"))
        # later:
        enc2 = LeakageSafeTargetEncoder.load(Path("artifacts/healthcare/encoder.joblib"))
        encoded_one = enc2.transform_row({"payer_name": "AETNA", ...})
    """

    columns: list[str]
    cv: int = TARGET_ENCODER_CV_FOLDS
    random_state: int = TARGET_ENCODER_RANDOM_STATE
    smoothing: str | float = TARGET_ENCODER_SMOOTHING

    # Populated by fit_transform / load
    _states: dict[str, _PerColumnState] = field(default_factory=dict)
    _fitted: bool = False
    _bundle_version: str = ENCODER_BUNDLE_VERSION

    # ---- fit ----------------------------------------------------------------
    def fit_transform(self, X: pd.DataFrame, y: pd.Series | np.ndarray) -> pd.DataFrame:
        """Fit encoders + return leakage-safe encoded columns for X (training).

        Cold-start safety: sklearn's TargetEncoder uses StratifiedKFold,
        which requires `cv` samples PER CLASS. We clamp effective_cv by
        the minority-class count so early-life variants (few positives or
        few negatives) don't blow up. When the minority class has 0 samples
        the encoder degenerates to global mean.
        """
        if not all(c in X.columns for c in self.columns):
            missing = [c for c in self.columns if c not in X.columns]
            raise KeyError(f"X missing encoder columns: {missing}")

        y_arr = np.asarray(y).astype(float).ravel()
        n_rows = len(y_arr)
        global_mean = float(y_arr.mean()) if n_rows else 0.0

        # Clamp cv by minority-class count, not just n_rows
        if n_rows >= 2:
            y_int = (y_arr > 0.5).astype(int)
            n_pos = int((y_int == 1).sum())
            n_neg = int((y_int == 0).sum())
            if n_pos > 0 and n_neg > 0:
                min_class = min(n_pos, n_neg)
                effective_cv = max(2, min(self.cv, min_class))
            else:
                # One class only — sklearn TargetEncoder needs binary;
                # treat as cold-start, return global mean for every row.
                n_rows = 1  # forces degenerate branch below
                effective_cv = 2
        else:
            effective_cv = 2

        out_cols: dict[str, np.ndarray] = {}
        for col in self.columns:
            # Coerce to string so unseen/blank rows route through a single sentinel
            x_col = X[col].astype("string").fillna(MISSING_CATEGORICAL_SENTINEL).to_numpy()
            x_2d = x_col.reshape(-1, 1)

            if n_rows < 2:
                # Cold-start degenerate path: encode every row to global mean.
                # Save a fitted-but-trivial encoder so transform_row still works.
                enc = TargetEncoder(smooth=self.smoothing, target_type="binary",
                                    cv=2, random_state=self.random_state)
                if n_rows == 1:
                    # Fit on a duplicated tiny dataset so the encoder has structure
                    enc.fit(np.vstack([x_2d, x_2d]),
                            np.concatenate([y_arr, y_arr]))
                else:
                    # Zero rows: produce an encoder that always returns 0.0
                    enc.fit(np.array([[MISSING_CATEGORICAL_SENTINEL]]),
                            np.array([0.0]))
                encoded = np.full(n_rows, global_mean, dtype="float32")
            else:
                enc = TargetEncoder(
                    smooth=self.smoothing,
                    target_type="binary",
                    cv=effective_cv,
                    random_state=self.random_state,
                )
                encoded = enc.fit_transform(x_2d, y_arr).ravel().astype("float32")

            out_cols[f"{col}_encoded"] = encoded

            vocab = frozenset(np.unique(x_col).tolist()) if n_rows else frozenset()
            self._states[col] = _PerColumnState(
                column=col,
                encoder=enc,
                vocabulary=vocab,
                global_mean=global_mean,
                n_training_rows=n_rows,
            )

        self._fitted = True
        return pd.DataFrame(out_cols, index=X.index)

    # ---- transform ----------------------------------------------------------
    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        """Encode X using the full-fit encoders. Unseen categories get the
        global training mean (handled by sklearn's TargetEncoder)."""
        self._require_fitted()
        out_cols: dict[str, np.ndarray] = {}
        for col in self.columns:
            if col not in X.columns:
                raise KeyError(f"transform: X missing column {col!r}")
            state = self._states[col]
            x_col = X[col].astype("string").fillna(MISSING_CATEGORICAL_SENTINEL).to_numpy()
            encoded = state.encoder.transform(x_col.reshape(-1, 1)).ravel()
            out_cols[f"{col}_encoded"] = encoded.astype("float32")
        return pd.DataFrame(out_cols, index=X.index)

    def transform_row(self, row: dict[str, Any]) -> dict[str, float]:
        """Single-row transform used by the predict path."""
        self._require_fitted()
        out: dict[str, float] = {}
        for col in self.columns:
            state = self._states[col]
            raw = row.get(col)
            v = str(raw) if raw is not None else MISSING_CATEGORICAL_SENTINEL
            if not v:
                v = MISSING_CATEGORICAL_SENTINEL
            arr = np.array([[v]], dtype=object)
            try:
                encoded = float(state.encoder.transform(arr).ravel()[0])
            except Exception:
                encoded = state.global_mean
            out[f"{col}_encoded"] = encoded
        return out

    # ---- introspection ------------------------------------------------------
    def is_known(self, column: str, value: Any) -> bool:
        """Cat-L unseen-flag computation hook. Returns True if `value` was
        present in the training vocabulary for `column`."""
        if column not in self._states:
            return False
        state = self._states[column]
        v = str(value) if value is not None else MISSING_CATEGORICAL_SENTINEL
        if not v:
            v = MISSING_CATEGORICAL_SENTINEL
        return v in state.vocabulary

    def vocabulary(self, column: str) -> frozenset[str]:
        if column not in self._states:
            return frozenset()
        return self._states[column].vocabulary

    def global_mean(self, column: str) -> float:
        if column not in self._states:
            return 0.0
        return self._states[column].global_mean

    def fitted_columns(self) -> list[str]:
        return list(self._states.keys())

    def is_fitted(self) -> bool:
        return self._fitted

    # ---- persistence --------------------------------------------------------
    def save(self, path: Path) -> None:
        self._require_fitted()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self._bundle_version,
            "columns": list(self.columns),
            "cv": self.cv,
            "random_state": self.random_state,
            "smoothing": self.smoothing,
            "states": {
                k: {
                    "encoder": s.encoder,
                    "vocabulary": list(s.vocabulary),
                    "global_mean": s.global_mean,
                    "n_training_rows": s.n_training_rows,
                }
                for k, s in self._states.items()
            },
        }
        joblib.dump(payload, path)
        logger.info("Saved encoder bundle: %s (cols=%d)", path, len(self.columns))

    @classmethod
    def load(cls, path: Path) -> "LeakageSafeTargetEncoder":
        payload = joblib.load(Path(path))
        if payload["version"] != ENCODER_BUNDLE_VERSION:
            logger.warning(
                "Encoder bundle version mismatch: saved=%s expected=%s",
                payload["version"], ENCODER_BUNDLE_VERSION,
            )
        enc = cls(
            columns=list(payload["columns"]),
            cv=payload["cv"],
            random_state=payload["random_state"],
            smoothing=payload["smoothing"],
        )
        for col, st in payload["states"].items():
            enc._states[col] = _PerColumnState(
                column=col,
                encoder=st["encoder"],
                vocabulary=frozenset(st["vocabulary"]),
                global_mean=float(st["global_mean"]),
                n_training_rows=int(st["n_training_rows"]),
            )
        enc._fitted = True
        return enc

    # ---- internals ----------------------------------------------------------
    def _require_fitted(self) -> None:
        if not self._fitted:
            raise RuntimeError("Encoder not fitted. Call fit_transform first.")
