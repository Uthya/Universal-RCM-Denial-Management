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

    # ------------------------------------------------------------------
    # Training entry point
    # ------------------------------------------------------------------
    async def fit_transform(
        self, session: AsyncSession, df: pd.DataFrame, y: pd.Series,
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
        )

        return FeatureArtifacts(
            features=X, encoder=self.encoder, rarity_state=self.rarity_state,
            ref_lookup=self.ref_lookup,
        )

    # ------------------------------------------------------------------
    # Prediction entry point
    # ------------------------------------------------------------------
    async def transform(self, session: AsyncSession, df: pd.DataFrame) -> pd.DataFrame:
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
        df_enriched = self._attach_ref_categoricals(df)
        enc_frame = encoded.transform(df_enriched, self.encoder)
        X = self._assemble(
            df,
            history_snap=hist_snap,
            provider_snap=prov_snap,
            joint_snap=joint_snap,
            encoded_frame=enc_frame,
            fit_time=False,
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

    def _assemble(
        self,
        df: pd.DataFrame,
        *,
        history_snap: PatientHistorySnapshot,
        provider_snap: ProviderProfileSnapshot,
        joint_snap: JointEncoderSnapshot,
        encoded_frame: pd.DataFrame,
        fit_time: bool,
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
            frequency_code_encoded=None,
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

        # Concatenate in registry order
        all_parts = pd.concat(
            [base_cols, cov_cols, auth_cols, clin_cols, code_cols, timely_cols,
             doc_cols, hist_cols, prov_cols, joint_cols, encoded_frame, rar_cols,
             avail_cols, var_cols],
            axis=1,
        )

        expected = list(get_feature_columns(
            self.service_variant, self.claim_subtype, fall_back_to_global=True,
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
            self.service_variant, self.claim_subtype, fall_back_to_global=True,
        ))
        return pd.DataFrame({c: pd.Series(dtype="float32") for c in cols})


__all__ = ["FeatureArtifacts", "FeatureBuilder"]
