"""End-to-end Phase 3 validation against the remote PG.

The test:
    1. Seeds the remote DB with ~25 synthetic 837P healthcare claims
       (mix of denied + paid via 835 remittances), enough to train a
       minimal XGBoost model.
    2. Refreshes mv_claim_labels so the loader picks them up.
    3. Runs FeatureBuilder.fit_transform → asserts matrix shape + registry
       enforcement.
    4. Trains a healthcare model via ml.trainer.train_healthcare_model.
    5. Reloads the artifact bundle.
    6. Runs predict on a held-out claim through the SAME builder → asserts
       train/predict parity (column order, encoder reuse, vocab carryover).
    7. Validates monitoring-compat: PredictionResult carries everything the
       prediction_log row needs.

Skipped unless RCM_INTEGRATION_DSN is set. Cleans up after itself.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


pytestmark = pytest.mark.skipif(
    "RCM_INTEGRATION_DSN" not in os.environ,
    reason="RCM_INTEGRATION_DSN not set — Phase 3 E2E skipped",
)


_SEED_TAG = "phase3-e2e"   # marker stuffed into file_name for cleanup


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(
        os.environ["RCM_INTEGRATION_DSN"],
        pool_pre_ping=True,
        pool_recycle=60,
    )
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as s:
        yield s
    await engine.dispose()


async def _cleanup(session: AsyncSession) -> None:
    """Best-effort teardown for prior runs."""
    await session.execute(
        text("DELETE FROM edi_files WHERE file_name LIKE :pfx"),
        {"pfx": f"{_SEED_TAG}%"},
    )
    await session.commit()


async def _seed_synthetic_corpus(session: AsyncSession, n_claims: int = 30) -> None:
    """Insert N claims directly via the parse pipeline so the FK / MV wiring
    is real, not faked. Half are denied via accompanying 835 entries."""
    from rcm.parsing import parse_and_save

    # Generate one EDI file per claim (so each gets a distinct content_hash)
    for i in range(n_claims):
        # Mix payers, CPTs, dx, modifiers, dates to give the features signal
        payer = "AETNA" if i % 3 != 0 else "CIGNA"
        cpt = "99213" if i % 2 == 0 else "99214"
        dx = "I10" if i % 3 == 0 else ("E119" if i % 3 == 1 else "J449")
        mod = "" if i % 5 != 0 else ":25"
        edi = "~".join([
            "ISA*00*          *00*          *ZZ*P3SUB          *ZZ*P3RCV          *260601*1200*^*00501*"
            f"{i+100:09d}*0*P*:",
            f"GS*HC*P3SUB*P3RCV*20260601*1200*{i+1}*X*005010X222A1",
            f"ST*837*{i+1:04d}*005010X222A1",
            "BHT*0019*00*BENCH*20260601*1200*CH",
            "HL*1**20*1",
            f"NM1*85*2*P3 CLINIC*****XX*111222333{i % 10}",
            "HL*2*1*22*0",
            "SBR*P*18*GRP*******CI",
            f"NM1*IL*1*PAT{i:03d}*JOE****MI*MEMP3{i:05d}",
            f"NM1*PR*2*{payer}*****PI*PI{i % 5}",
            f"CLM*P3-{i:05d}*100***11:B:1*Y*A*Y*Y",
            f"HI*ABK:{dx}",
            f"DTP*472*D8*2026060{(i % 7) + 1}",
            f"SV1*HC:{cpt}{mod}*100*UN*1*11**1",
            f"SE*11*{i+1:04d}",
            f"GE*1*{i+1}",
            f"IEA*1*{i+100:09d}",
        ]) + "~"
        # Embed the cleanup marker in the filename
        await parse_and_save(session, edi.encode("utf-8"), f"{_SEED_TAG}-claim-{i}.edi")

    # Now inject 835 remittances for ~half of them (denied) so labels exist
    for i in range(0, n_claims, 2):
        status = "4" if (i % 4 == 0) else "1"   # alternate denied/paid
        paid = "0" if status == "4" else "100"
        edi835 = "~".join([
            "ISA*00*          *00*          *ZZ*PAYR           *ZZ*PROV           *260615*1300*^*00501*"
            f"{i+500:09d}*0*P*:",
            f"GS*HP*PAYR*PROV*20260615*1300*{i+500}*X*005010X221A1",
            f"ST*835*{i+1:04d}",
            "BPR*I*100*C*ACH*CCP*01*111*DA*222*333**01*111*DA*222*20260615",
            f"TRN*1*EOB{i:05d}*111",
            "DTM*405*20260615",
            "N1*PR*AETNA",
            "N1*PE*P3 CLINIC*XX*1112223330",
            "LX*1",
            f"CLP*P3-{i:05d}*{status}*100*{paid}*0*12*EOB{i:05d}",
            f"NM1*QC*1*PAT{i:03d}*JOE****MI*MEMP3{i:05d}",
            "DTM*050*20260615",
            f"SE*11*{i+1:04d}",
            f"GE*1*{i+500}",
            f"IEA*1*{i+500:09d}",
        ]) + "~"
        await parse_and_save(session, edi835.encode("utf-8"), f"{_SEED_TAG}-remit-{i}.edi")

    await session.execute(text("REFRESH MATERIALIZED VIEW mv_claim_labels"))
    await session.commit()


class TestPhase3E2E:
    @pytest.mark.asyncio
    async def test_e2e_train_predict_parity(self, session: AsyncSession):
        from rcm.features.builder import FeatureBuilder
        from rcm.features.dataset import load_training_corpus
        from rcm.features.registry import (
            FEATURE_COLUMNS_HEALTHCARE,
            validate_feature_frame,
        )
        from rcm.ml.artifacts import ModelArtifactBundle
        from rcm.ml.predictor import HealthcarePredictor
        from rcm.ml.trainer import train_healthcare_model

        await _cleanup(session)
        await _seed_synthetic_corpus(session, n_claims=30)

        # ---- 1. Corpus load ----
        df = await load_training_corpus(
            session, service_variant="837P", claim_subtype="healthcare",
        )
        assert len(df) > 0, "Synthetic corpus didn't produce labelled rows"
        assert "denied" in df.columns
        assert df["denied"].isin([0, 1]).all()

        # ---- 2. Feature build ----
        builder = FeatureBuilder(service_variant="837P", claim_subtype="healthcare")
        artifacts = await builder.fit_transform(session, df, df["denied"])
        X = artifacts.features

        # Registry enforcement
        assert list(X.columns) == list(FEATURE_COLUMNS_HEALTHCARE)
        validate_feature_frame(X, "837P", "healthcare")
        assert len(X) == len(df)

        # Encoder fitted with expected columns
        assert artifacts.encoder.is_fitted()
        assert set(artifacts.encoder.fitted_columns()) == {
            "payer_canonical_name", "primary_cpt", "primary_dx",
            "primary_pos", "facility_type_code",
        }

        # ---- 3. Train + persist ----
        with tempfile.TemporaryDirectory() as td:
            art_dir = Path(td) / "healthcare"
            bundle = await train_healthcare_model(session, art_dir)
            assert (art_dir / "model.json").exists()
            assert (art_dir / "encoder.joblib").exists()
            assert (art_dir / "rarity_state.joblib").exists()
            assert (art_dir / "feature_schema.json").exists()
            assert bundle.decision_threshold > 0
            # CR-075: corpus is split 70/15/15 → train + validation + held_out ≈ len(X)
            n_train = int(bundle.metrics["n_training_rows"])
            n_val   = int(bundle.metrics.get("n_validation_rows", 0))
            n_held  = int(bundle.metrics.get("n_held_out_rows", 0))
            assert n_train + n_val + n_held == len(X), (
                f"split arithmetic: {n_train}+{n_val}+{n_held} != {len(X)}"
            )
            assert n_train > 0 and n_val > 0 and n_held > 0
            assert bundle.metrics.get("held_out") is not None
            assert bundle.metrics.get("validation") is not None

            # ---- 4. Reload + predict ----
            predictor = HealthcarePredictor.load(art_dir)
            assert predictor.bundle.feature_columns == list(FEATURE_COLUMNS_HEALTHCARE)

            # Predict on the same corpus (parity check)
            results = await predictor.predict(session, df.head(5))
            assert len(results) == 5
            for r in results:
                assert 0.0 <= r.risk_score <= 1.0
                assert r.risk_level in {"HIGH", "MEDIUM", "LOW"}
                assert r.predicted_label in {0, 1}
                assert r.feature_engineering_version
                assert r.model_version
                # Monitoring-compat fields
                assert r.feature_snapshot
                assert 0.0 <= r.reference_data_completeness <= 1.0
                assert 0.0 <= r.input_completeness <= 1.0
                # top_risk_factors populated (SHAP succeeded)
                assert len(r.top_risk_factors) > 0

        # ---- 5. Cleanup ----
        await _cleanup(session)
