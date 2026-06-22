"""FeatureBuilder — single source for train/predict feature assembly.

Same code path produces the same column order at both training and
prediction time. The builder:

    1. Loads MV snapshots (history, provider, joint encoders) for the
       claim_ids in the input frame. Loaders gracefully degrade when MVs
       are empty (cold start, before first refresh).
    2. Fits/transforms the Category K target encoder.
    3. Computes Categories J / A / B / C / D / E / F / G / H / I / K / L / Z
       in canonical order.
    4. Adds variant-specific Category M block.
    5. Assembles the final DataFrame in FEATURE_COLUMNS_<variant> order.
    6. Validates the result against the registry (M1 strict check).

Train path:
    builder = FeatureBuilder(variant="837P", subtype="healthcare")
    artifacts = await builder.fit_transform(session, training_df, y)
    # artifacts = {features, encoder, rarity_state, ref_lookup, schema_version}

Predict path:
    builder = FeatureBuilder(variant="837P", subtype="healthcare",
                             encoder=loaded_encoder, rarity_state=loaded_state)
    X = await builder.transform(session, predict_df)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from rcm.features.categories import (
    authorization,
    availability,
    base,
    clinical,
    coding,
    coverage,
    documentation,
    encoded,
    history,
    joint,
    provider as provider_cat,
    rarity,
    timely,
)
from rcm.features.categories.availability import RefDataLookup
from rcm.features.categories.history import PatientHistorySnapshot
from rcm.features.categories.joint import JointEncoderSnapshot
from rcm.features.categories.provider import ProviderProfileSnapshot
from rcm.features.categories.rarity import RarityState
from rcm.features.constants import FEATURE_ENGINEERING_VERSION
from rcm.features.encoders import LeakageSafeTargetEncoder
from rcm.features.registry import (
    FEATURE_REGISTRY,
    FeatureSchemaError,
    get_feature_columns,
    is_registered_variant,
    validate_feature_frame,
)
from rcm.features.variants import (
    dental as dental_variant,
    global_fallback as global_variant,
    healthcare as healthcare_variant,
    home_care as home_care_variant,
    institutional_other as institutional_other_variant,
    specialty as specialty_variant,
    therapy as therapy_variant,
    transport as transport_variant,
)


# Dispatch table: (service_variant, claim_subtype) → variant module
# Aliased subtypes (837I inpatient/hospice/specialty) all route to the
# institutional_other block per spec §2.2 routing rules.
_VARIANT_DISPATCH = {
    ("837P", "healthcare"):           healthcare_variant,
    ("837P", "therapy"):              therapy_variant,
    ("837P", "transport"):            transport_variant,
    ("837P", "specialty"):            specialty_variant,
    ("837I", "home_care"):            home_care_variant,
    ("837I", "institutional_other"):  institutional_other_variant,
    ("837I", "inpatient"):            institutional_other_variant,
    ("837I", "hospice"):              institutional_other_variant,
    ("837I", "specialty"):            institutional_other_variant,
    ("837D", "dental"):               dental_variant,
    ("_global", "_global"):           global_variant,
}

logger = logging.getLogger(__name__)


# CR-107: leakage-safe denial-rate features. Each entry maps an output column
# to the cohort-key columns. At training time we recompute these per-row using
# only PRIOR-dated train rows (strict-< on service_from_date), overriding the
# global-MV values that joint.compute / provider.compute / coverage.compute
# would otherwise inject. Predict time uses the global MV unchanged.
_LEAKAGE_SAFE_KEYS: list[tuple[str, tuple[str, ...]]] = [
    ("payer_cpt_denial_rate",          ("payer_id", "primary_cpt")),
    ("payer_dx_denial_rate",           ("payer_id", "primary_dx")),
    ("payer_pos_denial_rate",          ("payer_id", "primary_pos")),
    ("cpt_dx_denial_rate",             ("primary_cpt", "primary_dx")),
    ("provider_payer_denial_rate",     ("billing_provider_id", "payer_id")),
    ("payer_provider_denial_rate",     ("billing_provider_id", "payer_id")),
    ("provider_cpt_denial_rate",       ("billing_provider_id", "primary_cpt")),
    ("provider_cpt_denial_rate_joint", ("billing_provider_id", "primary_cpt")),
    ("payer_overall_denial_rate",      ("payer_id",)),
    ("provider_overall_denial_rate",   ("billing_provider_id",)),
]
_LEAKAGE_SAFE_FEATURE_NAMES: tuple[str, ...] = tuple(f for f, _ in _LEAKAGE_SAFE_KEYS)


def compute_leakage_safe_denial_rates(
    query_df: pd.DataFrame,
    train_df: pd.DataFrame,
    train_y: np.ndarray | pd.Series,
) -> pd.DataFrame:
    """Per-row leakage-safe denial rates for the 8 MV-backed features.

    Each ``query_df`` row gets a feature value equal to the average ``train_y``
    over ``train_df`` rows that share the cohort key AND have a strictly
    earlier ``service_from_date``. Rows with any missing key value (or with
    no qualifying prior rows) receive 0.0 — matching the global-MV path's
    behaviour for unknown keys.

    The query_df row's OWN label is excluded automatically via strict-<.
    Future-dated rows are excluded automatically. Validation / held-out rows
    are excluded by passing ONLY training rows as ``train_df``.

    Returns a DataFrame indexed identically to ``query_df`` with 10 columns
    (the 8 distinct features plus the 2 mirrored Cat-I aliases of the same
    underlying aggregates).
    """
    if len(query_df) == 0:
        return pd.DataFrame(index=query_df.index,
                            columns=_LEAKAGE_SAFE_FEATURE_NAMES, dtype="float32")
    if len(train_df) == 0:
        return pd.DataFrame(
            0.0, index=query_df.index,
            columns=list(_LEAKAGE_SAFE_FEATURE_NAMES), dtype="float32",
        )

    y_arr = np.asarray(train_y).astype("float64").ravel()
    if len(y_arr) != len(train_df):
        raise ValueError(
            f"train_y length ({len(y_arr)}) must match train_df rows ({len(train_df)})"
        )

    train_df = train_df.copy()
    train_df["_y"] = y_arr
    if "service_from_date" not in train_df or "service_from_date" not in query_df:
        raise KeyError("compute_leakage_safe_denial_rates: service_from_date required")

    # Normalize dates to pandas datetime for merge_asof
    train_df = train_df.copy()
    train_df["_date"] = pd.to_datetime(train_df["service_from_date"], errors="coerce")
    query_df_norm = query_df.copy()
    query_df_norm["_date"] = pd.to_datetime(query_df_norm["service_from_date"], errors="coerce")
    query_df_norm["_orig_idx"] = np.arange(len(query_df_norm))

    out = pd.DataFrame(index=query_df.index, dtype="float32")

    # Group features by key tuple so each unique key set is computed once
    keys_to_features: dict[tuple[str, ...], list[str]] = {}
    for fname, keys in _LEAKAGE_SAFE_KEYS:
        keys_to_features.setdefault(keys, []).append(fname)

    for keys, fnames in keys_to_features.items():
        keys_list = list(keys)
        # Required columns
        needed = set(keys_list) | {"_date", "_y"}
        if not needed.issubset(set(train_df.columns)):
            for fname in fnames:
                out[fname] = np.zeros(len(query_df), dtype="float32")
            continue
        # Drop rows with NaN in any key column from the train aggregation —
        # they contribute nothing (MV path uses these as default-0 anyway).
        train_valid = train_df.dropna(subset=keys_list + ["_date"]).copy()
        if train_valid.empty:
            for fname in fnames:
                out[fname] = np.zeros(len(query_df), dtype="float32")
            continue
        # Cast key columns to a stable, non-NaN-tolerant form for grouping
        for k in keys_list:
            if train_valid[k].dtype.kind in "fiu":
                train_valid[k] = train_valid[k].astype("Int64").astype(str)
            else:
                train_valid[k] = train_valid[k].astype(str)
        # Aggregate by (keys, date). cum_y/cum_n at each (key, date) row carry
        # totals INCLUDING the rows on that date — the strict-< filter is
        # applied at lookup time by merge_asof(allow_exact_matches=False).
        agg = (train_valid.groupby(keys_list + ["_date"], sort=True, dropna=False)
                          .agg(y_sum=("_y", "sum"), y_count=("_y", "count"))
                          .reset_index())
        agg["cum_y"] = agg.groupby(keys_list, sort=False, dropna=False)["y_sum"].cumsum()
        agg["cum_n"] = agg.groupby(keys_list, sort=False, dropna=False)["y_count"].cumsum()
        # Build the query side in the same stable form
        qdf = query_df_norm[keys_list + ["_date", "_orig_idx"]].copy()
        for k in keys_list:
            col = qdf[k]
            if col.dtype.kind in "fiu":
                qdf[k] = col.astype("Int64").astype(str)
            else:
                qdf[k] = col.astype(str)
        # merge_asof needs sorted-by-on on both sides
        agg_sorted = agg.sort_values("_date")
        qdf_sorted = qdf.sort_values("_date")
        merged = pd.merge_asof(
            qdf_sorted, agg_sorted[keys_list + ["_date", "cum_y", "cum_n"]],
            on="_date", by=keys_list, direction="backward",
            allow_exact_matches=False,  # strict-< on _date
        )
        with np.errstate(divide="ignore", invalid="ignore"):
            cum_n = merged["cum_n"].fillna(0).to_numpy(dtype="float64")
            cum_y = merged["cum_y"].fillna(0).to_numpy(dtype="float64")
            rate = np.where(cum_n > 0, cum_y / np.maximum(cum_n, 1), 0.0)
        # Restore original query order
        merged = merged.assign(_safe=rate.astype("float32"))
        merged = merged.sort_values("_orig_idx")
        values = merged["_safe"].to_numpy()
        for fname in fnames:
            out[fname] = values

    return out


@dataclass
class FeatureArtifacts:
    """Everything produced at training time that the predictor needs later."""
    features: pd.DataFrame
    encoder: LeakageSafeTargetEncoder
    rarity_state: RarityState
    ref_lookup: RefDataLookup
    schema_version: str = FEATURE_ENGINEERING_VERSION


@dataclass
class FeatureBuilder:
    service_variant: str
    claim_subtype: str
    encoder: LeakageSafeTargetEncoder | None = None
    rarity_state: RarityState | None = None
    ref_lookup: RefDataLookup = field(default_factory=RefDataLookup)
    # CR-120: opt-in lifecycle features. Default-off preserves pre-CR-120 bundles.
    include_lifecycle: bool = False

    # ------------------------------------------------------------------
    # Training entry point
    # ------------------------------------------------------------------
    async def fit_transform(
        self, session: AsyncSession, df: pd.DataFrame, y: pd.Series,
        *,
        safe_rates: pd.DataFrame | None = None,
    ) -> FeatureArtifacts:
        # CR-088: populate reference-data lookup from DB if not already loaded.
        # If the caller passed a pre-loaded ref_lookup (predict-time bundle
        # restore path), we keep it as-is — only an empty default triggers a
        # fresh DB load. Empty DB tables degrade gracefully (the lookup stays
        # empty for those tables; existing default-value fallbacks in each
        # category module remain unchanged).
        await self._maybe_load_ref(session)

        if df.empty:
            empty_X = self._empty_matrix()
            self.encoder = encoded.make_encoder()
            self.rarity_state = RarityState()
            return FeatureArtifacts(
                features=empty_X, encoder=self.encoder, rarity_state=self.rarity_state,
                ref_lookup=self.ref_lookup,
            )

        # Snapshots
        hist_snap, prov_snap, joint_snap = await self._load_snapshots(session, df)
        life_snap = await self._maybe_load_lifecycle_snapshot(session, df)

        # Rarity vocab from training data
        self.rarity_state = RarityState.fit(df)

        # CR-088: enrich df with ref-derived source columns (cpt_category, dx_chapter)
        # so the LeakageSafeTargetEncoder can target-encode them alongside the
        # original 5 categoricals. The two new encoded outputs route through
        # Category K and replace the constant-0 placeholders that clinical.py
        # would otherwise emit (see _assemble below).
        df_enriched = self._attach_ref_categoricals(df)

        # Fit + transform encoder
        self.encoder, enc_frame = encoded.fit_transform(df_enriched, y)

        # Assemble all categories
        X = self._assemble(
            df,
            history_snap=hist_snap,
            provider_snap=prov_snap,
            joint_snap=joint_snap,
            encoded_frame=enc_frame,
            fit_time=True,
            safe_rates=safe_rates,
            lifecycle_snap=life_snap,
        )

        return FeatureArtifacts(
            features=X, encoder=self.encoder, rarity_state=self.rarity_state,
            ref_lookup=self.ref_lookup,
        )

    # ------------------------------------------------------------------
    # Prediction entry point
    # ------------------------------------------------------------------
    async def transform(
        self, session: AsyncSession, df: pd.DataFrame,
        *,
        safe_rates: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        if self.encoder is None:
            raise RuntimeError(
                "FeatureBuilder.transform called before fit_transform; "
                "supply a loaded encoder + rarity_state."
            )
        # CR-088: same lazy ref-data load as fit_transform. Cached on the
        # FeatureBuilder instance so subsequent transform() calls on the same
        # instance (predict-cache reuse per CR-067) avoid re-querying.
        await self._maybe_load_ref(session)

        if df.empty:
            return self._empty_matrix()

        hist_snap, prov_snap, joint_snap = await self._load_snapshots(session, df)
        life_snap = await self._maybe_load_lifecycle_snapshot(session, df)
        df_enriched = self._attach_ref_categoricals(df)
        enc_frame = encoded.transform(df_enriched, self.encoder)
        X = self._assemble(
            df,
            history_snap=hist_snap,
            provider_snap=prov_snap,
            joint_snap=joint_snap,
            encoded_frame=enc_frame,
            fit_time=False,
            safe_rates=safe_rates,
            lifecycle_snap=life_snap,
        )
        return X

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    async def _maybe_load_ref(self, session: AsyncSession) -> None:
        """CR-088: lazy DB load of reference data.

        Loads ``procedure_codes`` / ``diagnosis_codes`` / ``ncci_edits`` /
        ``cms_lcd_coverage`` / ``payer_policies`` into ``self.ref_lookup``
        exactly once per FeatureBuilder instance. Empty tables degrade to
        the existing empty defaults — the FE category modules' fallback
        behaviour is preserved verbatim.

        If the caller passed in a pre-populated ``ref_lookup`` (e.g. a test
        injecting a synthetic snapshot), it's respected as-is.
        """
        if self.ref_lookup is not None and not self.ref_lookup.is_empty:
            return
        try:
            self.ref_lookup = await RefDataLookup.from_session(session)
        except Exception as exc:
            # Defensive: an unexpected DB error during ref-data load must
            # NOT brick training/prediction. Fall back to an empty lookup;
            # downstream features use their defaults exactly as before.
            logger.warning(
                "CR-088 RefDataLookup.from_session failed; using empty lookup. %s: %s",
                type(exc).__name__, exc,
            )
            self.ref_lookup = RefDataLookup()

    def _attach_ref_categoricals(self, df: pd.DataFrame) -> pd.DataFrame:
        """CR-088: derive ``cpt_category`` and ``dx_chapter`` source columns
        from ``self.ref_lookup`` and attach them to a copy of ``df`` so the
        Category K target encoder can fit/transform them alongside the
        original 5 categoricals.

        When ``ref_lookup`` is empty (cold start, before CR-087 data load),
        both columns default to the missing-categorical sentinel; the encoder
        then treats them as a single category and produces a constant
        encoded value — matching the pre-CR-088 behaviour for back-compat.
        """
        from rcm.features.constants import MISSING_CATEGORICAL_SENTINEL
        out = df.copy()
        cpts = out.get("primary_cpt", pd.Series([None] * len(out), index=out.index))
        dxs  = out.get("primary_dx",  pd.Series([None] * len(out), index=out.index))
        out["cpt_category"] = [
            (self.ref_lookup.procedure_metadata.get(str(c), {}).get("category")
             if c is not None and not (isinstance(c, float) and pd.isna(c)) else None)
            or MISSING_CATEGORICAL_SENTINEL
            for c in cpts
        ]
        out["dx_chapter"] = [
            (self.ref_lookup.dx_chapter.get(str(d))
             if d is not None and not (isinstance(d, float) and pd.isna(d)) else None)
            or MISSING_CATEGORICAL_SENTINEL
            for d in dxs
        ]
        return out

    async def _load_snapshots(
        self, session: AsyncSession, df: pd.DataFrame,
    ) -> tuple[PatientHistorySnapshot, ProviderProfileSnapshot, JointEncoderSnapshot]:
        claim_ids = df["claim_id"].dropna().astype(int).tolist() if "claim_id" in df else []
        payer_ids = df.get("payer_id", pd.Series(dtype="object")).dropna().astype(int).tolist()
        provider_ids = df.get("billing_provider_id", pd.Series(dtype="object")).dropna().astype(int).tolist()
        primary_cpts = df.get("primary_cpt", pd.Series(dtype="object")).fillna("").astype(str).tolist()
        primary_dxs = df.get("primary_dx", pd.Series(dtype="object")).fillna("").astype(str).tolist()
        primary_pos = df.get("primary_pos", pd.Series(dtype="object")).fillna("").astype(str).tolist()
        variants = df.get("service_variant", pd.Series(dtype="object")).fillna("").astype(str).tolist()

        hist = await history.load_patient_history_for_claims(session, claim_ids)
        prov = await provider_cat.load_provider_snapshot(session, provider_ids, payer_ids, primary_cpts)
        joint_snap = await joint.load_joint_snapshot(
            session, payer_ids, provider_ids, variants, primary_cpts, primary_dxs, primary_pos,
        )
        return hist, prov, joint_snap

    async def _maybe_load_lifecycle_snapshot(
        self, session: AsyncSession, df: pd.DataFrame,
    ) -> pd.DataFrame | None:
        """CR-120: load per-replacement original snapshot if lifecycle features
        are enabled on this builder. Returns None when ``include_lifecycle=False``
        so the assemble path skips the lifecycle.compute() call entirely (no
        cost, no behaviour change for pre-CR-120 bundles)."""
        if not self.include_lifecycle:
            return None
        from rcm.features.dataset import load_original_snapshots  # noqa: PLC0415
        claim_ids = df["claim_id"].dropna().astype(int).tolist() if "claim_id" in df else []
        if not claim_ids:
            return None
        return await load_original_snapshots(session, claim_ids)

    def _assemble(
        self,
        df: pd.DataFrame,
        *,
        history_snap: PatientHistorySnapshot,
        provider_snap: ProviderProfileSnapshot,
        joint_snap: JointEncoderSnapshot,
        encoded_frame: pd.DataFrame,
        fit_time: bool,
        safe_rates: pd.DataFrame | None = None,
        lifecycle_snap: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        # Pull common encoded-column slices to feed Cat A / C / D / H
        def _enc(col: str) -> pd.Series:
            if col in encoded_frame.columns:
                return encoded_frame[col].astype("float32")
            return pd.Series(0.0, index=df.index, dtype="float32")

        base_cols = base.compute(df)
        cov_cols = coverage.compute(
            df,
            ref=self.ref_lookup,
            payer_overall_denial=joint_snap.payer_overall,
            payer_taxonomy_encoded=None,  # not target-encoding payer_taxonomy yet
        )
        auth_cols = authorization.compute(df, ref=self.ref_lookup)
        clin_cols = clinical.compute(
            df,
            ref=self.ref_lookup,
            cpt_dx_denial_rate=joint_snap.cpt_dx,
            dx_chapter_encoded=None,
            cpt_category_encoded=None,
        )
        # CR-088: clinical.compute writes constant-0 placeholders for these two
        # target-encoded columns when the parameters are None. Drop them so the
        # ref-data-driven values from encoded_frame are not shadowed by the
        # placeholders in the final concat.
        clin_cols = clin_cols.drop(
            columns=["cpt_category_encoded", "primary_dx_chapter_encoded"],
            errors="ignore",
        )
        code_cols = coding.compute(
            df,
            ref=self.ref_lookup,
            cpt_frequency_ytd={},
        )
        timely_cols = timely.compute(df, ref=self.ref_lookup)
        doc_cols = documentation.compute(df, ref=self.ref_lookup)
        hist_cols = history.compute(df, snapshot=history_snap)
        prov_cols = provider_cat.compute(
            df,
            snapshot=provider_snap,
            ref=self.ref_lookup,
            billing_npi_encoded=None,
            rendering_npi_encoded=None,
            provider_taxonomy_encoded=None,
        )
        joint_cols = joint.compute(df, snapshot=joint_snap)
        rar_cols = rarity.compute(df, state=self.rarity_state)
        avail_cols = availability.compute(df, ref=self.ref_lookup)

        # Variant — dispatch by (service_variant, claim_subtype).
        # Unknown tuples → global_fallback (universal-only columns, 0-width Cat M)
        var_cols = self._dispatch_variant_block(df, history_snap)

        # CR-120: lifecycle category appended LAST so existing column ORDER
        # is preserved verbatim — pre-CR-120 bundles that don't enable
        # `include_lifecycle` see no change to their X matrix.
        parts = [base_cols, cov_cols, auth_cols, clin_cols, code_cols, timely_cols,
                 doc_cols, hist_cols, prov_cols, joint_cols, encoded_frame, rar_cols,
                 avail_cols, var_cols]
        if self.include_lifecycle:
            from rcm.features.categories import lifecycle as lifecycle_cat  # noqa: PLC0415
            snap = lifecycle_snap if lifecycle_snap is not None else pd.DataFrame()
            life_cols = lifecycle_cat.compute(df, snap)
            parts.append(life_cols)
        all_parts = pd.concat(parts, axis=1)

        # CR-107: override the 8 MV-backed denial-rate columns with leakage-safe
        # per-row values when safe_rates is provided. The MV-derived columns
        # above stay as the source of truth at predict time (production); only
        # train/val/held during training pass safe_rates.
        if safe_rates is not None and len(safe_rates) > 0:
            aligned = safe_rates.reindex(df.index)
            for col in _LEAKAGE_SAFE_FEATURE_NAMES:
                if col in all_parts.columns and col in aligned.columns:
                    all_parts[col] = aligned[col].astype("float32").fillna(0.0)

        expected = list(get_feature_columns(
            self.service_variant, self.claim_subtype,
            fall_back_to_global=True,
            include_lifecycle=self.include_lifecycle,
        ))

        # Backfill any missing columns with the registered default value
        for col in expected:
            if col not in all_parts.columns:
                spec = FEATURE_REGISTRY.get(col)
                default = float(spec.default_value) if spec else 0.0
                all_parts[col] = pd.Series(default, index=df.index)

        # Drop unexpected columns (e.g., source-named encoder cols that slip through)
        out = all_parts[expected]

        # M1 strict validation — enforces both presence and exact column order
        validate_feature_frame(
            out, self.service_variant, self.claim_subtype,
            fall_back_to_global=True,
            include_lifecycle=self.include_lifecycle,
        )
        return out

    def _dispatch_variant_block(
        self, df: pd.DataFrame, history_snap: PatientHistorySnapshot,
    ) -> pd.DataFrame:
        """Look up the Cat M block for this builder's (variant, subtype) tuple.

        Falls back to `global_fallback` (returns a 0-column DataFrame) when
        the tuple isn't registered — that's the safety net for unknown
        variants. The corresponding feature matrix has 108 universal columns
        only and is scored against the `_global` model.
        """
        key = (self.service_variant, self.claim_subtype)
        module = _VARIANT_DISPATCH.get(key, global_variant)

        # Modules that accept history_snapshot use it; others don't.
        # Healthcare, therapy, home_care, dental currently take it.
        if module is healthcare_variant:
            return module.compute(df, history_snapshot=history_snap)
        if module is therapy_variant:
            return module.compute(df, history_snapshot=history_snap)
        if module is home_care_variant:
            return module.compute(df, history_snapshot=history_snap)
        if module is dental_variant:
            return module.compute(df, history_snapshot=history_snap)
        return module.compute(df)

    def _effective_key(self) -> tuple[str, str]:
        """The actual variant/subtype key this builder will use after
        global-fallback resolution. Used by tests and the prediction layer."""
        if is_registered_variant(self.service_variant, self.claim_subtype):
            return (self.service_variant, self.claim_subtype)
        return ("_global", "_global")

    def _empty_matrix(self) -> pd.DataFrame:
        cols = list(get_feature_columns(
            self.service_variant, self.claim_subtype,
            fall_back_to_global=True,
            include_lifecycle=self.include_lifecycle,
        ))
        return pd.DataFrame({c: pd.Series(dtype="float32") for c in cols})


__all__ = ["FeatureArtifacts", "FeatureBuilder"]
