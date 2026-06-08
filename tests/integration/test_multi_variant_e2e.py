"""Multi-variant feature-builder E2E.

Confirms the system can ingest + score claims of ANY variant — not just
837P healthcare. For each variant we:

    1. Seed a single representative claim via parse_and_save
    2. Refresh mv_claim_labels (needs a remittance for the label)
    3. Run FeatureBuilder.transform → assert column count + canonical order
    4. Verify global fallback for an unknown subtype

Skipped unless RCM_INTEGRATION_DSN is set. Cleans up after itself.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


pytestmark = pytest.mark.skipif(
    "RCM_INTEGRATION_DSN" not in os.environ,
    reason="RCM_INTEGRATION_DSN not set — multi-variant E2E skipped",
)


_SEED_TAG = "mv-e2e"


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
    await session.execute(
        text("DELETE FROM edi_files WHERE file_name LIKE :pfx"),
        {"pfx": f"{_SEED_TAG}%"},
    )
    await session.commit()


# ----------------------------------------------------------------------
# Synthetic EDI generators per variant
# ----------------------------------------------------------------------

def _build_837p_therapy_edi(seq: int) -> bytes:
    edi = "~".join([
        "ISA*00*          *00*          *ZZ*MVE2E          *ZZ*MVE2ERCV       *260601*1200*^*00501*"
        f"{seq+100:09d}*0*P*:",
        f"GS*HC*MVE2E*MVE2ERCV*20260601*1200*{seq+1}*X*005010X222A1",
        f"ST*837*{seq+1:04d}*005010X222A1",
        "BHT*0019*00*MV*20260601*1200*CH",
        "HL*1**20*1",
        f"NM1*85*2*PT GROUP*****XX*1112223334",
        "HL*2*1*22*0",
        "SBR*P*18*GRP*******CI",
        f"NM1*IL*1*PAT{seq:03d}*JOE****MI*MEMTH{seq:05d}",
        "NM1*PR*2*MEDICARE*****PI*MEDIID",
        f"CLM*MVE2E-TH-{seq:05d}*120***11:B:1*Y*A*Y*Y",
        "HI*ABK:M5450",
        f"DTP*472*D8*2026060{(seq % 7) + 1}",
        "SV1*HC:97110:GP*60*UN*2*11**1",
        f"SE*11*{seq+1:04d}",
        f"GE*1*{seq+1}",
        f"IEA*1*{seq+100:09d}",
    ]) + "~"
    return edi.encode("utf-8")


def _build_837p_transport_edi(seq: int) -> bytes:
    edi = "~".join([
        "ISA*00*          *00*          *ZZ*MVE2E          *ZZ*MVE2ERCV       *260601*1200*^*00501*"
        f"{seq+200:09d}*0*P*:",
        f"GS*HC*MVE2E*MVE2ERCV*20260601*1200*{seq+200}*X*005010X222A1",
        f"ST*837*{seq+1:04d}*005010X222A1",
        "BHT*0019*00*MV*20260601*1200*CH",
        "HL*1**20*1",
        "NM1*85*2*CITY EMS*****XX*5556667770",
        "HL*2*1*22*0",
        "SBR*P*18*GRP*******MC",
        f"NM1*IL*1*PAT{seq:03d}*BOB****MI*MEMEMS{seq:05d}",
        "NM1*PR*2*MEDICAID NY*****PI*NYMCD",
        f"CLM*MVE2E-TR-{seq:05d}*450***41:B:1*Y*A*Y*Y",
        "HI*ABK:I639",
        f"DTP*472*D8*2026060{(seq % 7) + 1}",
        "CR1*LB*180*N*B*DH*12",
        "CRC*07*Y*04",
        "SV1*HC:A0429*450*UN*1",
        f"SE*13*{seq+1:04d}",
        f"GE*1*{seq+200}",
        f"IEA*1*{seq+200:09d}",
    ]) + "~"
    return edi.encode("utf-8")


def _build_837i_home_care_edi(seq: int) -> bytes:
    edi = "~".join([
        "ISA*00*          *00*          *ZZ*MVE2E          *ZZ*MVE2ERCV       *260601*1200*^*00501*"
        f"{seq+300:09d}*0*P*:",
        f"GS*HC*MVE2E*MVE2ERCV*20260601*1200*{seq+300}*X*005010X223A2",
        f"ST*837*{seq+1:04d}*005010X223A2",
        "BHT*0019*00*MV*20260601*1200*CH",
        "HL*1**20*1",
        "NM1*85*2*HOME HEALTH AGENCY*****XX*7778889990",
        "HL*2*1*22*0",
        "SBR*P*18*GRP*******MA",
        f"NM1*IL*1*PAT{seq:03d}*ALICE****MI*MEMHHA{seq:05d}",
        "NM1*PR*2*MEDICARE*****PI*MEDIID",
        f"CLM*MVE2E-HC-{seq:05d}*1450***32:B:1*Y*A*Y*Y",
        "HI*ABK:I509",
        "DTP*434*RD8*20260501-20260530",
        "CRC*75*Y*65",
        "SV2*0551*HC:G0299*450*UN*5",
        "SV2*0571*HC:G0156*250*UN*3",
        f"SE*13*{seq+1:04d}",
        f"GE*1*{seq+300}",
        f"IEA*1*{seq+300:09d}",
    ]) + "~"
    return edi.encode("utf-8")


def _build_837d_dental_edi(seq: int) -> bytes:
    edi = "~".join([
        "ISA*00*          *00*          *ZZ*MVE2E          *ZZ*MVE2ERCV       *260601*1200*^*00501*"
        f"{seq+400:09d}*0*P*:",
        f"GS*HC*MVE2E*MVE2ERCV*20260601*1200*{seq+400}*X*005010X224A2",
        f"ST*837*{seq+1:04d}*005010X224A2",
        "BHT*0019*00*MV*20260601*1200*CH",
        "HL*1**20*1",
        "NM1*85*2*DENTAL CARE GROUP*****XX*2223334445",
        "HL*2*1*22*0",
        "SBR*P*18*GRP*******CI",
        f"NM1*IL*1*PAT{seq:03d}*GARY****MI*MEMDDS{seq:05d}",
        "NM1*PR*2*DELTA DENTAL*****PI*DELTA1",
        f"CLM*MVE2E-DDS-{seq:05d}*250***11:B:1*Y*A*Y*Y",
        "HI*ABK:K021",
        f"DTP*472*D8*2026060{(seq % 7) + 1}",
        "SV3*AD:D2391*250*11***1",
        "TOO*JP*14*M:O:D",
        f"SE*11*{seq+1:04d}",
        f"GE*1*{seq+400}",
        f"IEA*1*{seq+400:09d}",
    ]) + "~"
    return edi.encode("utf-8")


def _build_835_remit_edi(claim_number: str, denied: bool, seq: int) -> bytes:
    status = "4" if denied else "1"
    paid = "0" if denied else "100"
    edi = "~".join([
        "ISA*00*          *00*          *ZZ*PAYR           *ZZ*PROV           *260615*1300*^*00501*"
        f"{seq+800:09d}*0*P*:",
        f"GS*HP*PAYR*PROV*20260615*1300*{seq+800}*X*005010X221A1",
        f"ST*835*{seq+1:04d}",
        "BPR*I*100*C*ACH*CCP*01*111*DA*222*333**01*111*DA*222*20260615",
        f"TRN*1*EOB{seq:05d}*111",
        "DTM*405*20260615",
        "N1*PR*MEDICARE",
        "N1*PE*PROVIDER*XX*1112223334",
        "LX*1",
        f"CLP*{claim_number}*{status}*100*{paid}*0*12*EOB{seq:05d}",
        "DTM*050*20260615",
        f"SE*10*{seq+1:04d}",
        f"GE*1*{seq+800}",
        f"IEA*1*{seq+800:09d}",
    ]) + "~"
    return edi.encode("utf-8")


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------

class TestMultiVariantE2E:
    """Every claim type produces a feature matrix of the right shape, with
    canonical column order, including the global fallback."""

    @pytest.mark.asyncio
    async def test_each_variant_routes_to_its_block(self, session: AsyncSession):
        from rcm.features.builder import FeatureBuilder
        from rcm.features.dataset import load_training_corpus
        from rcm.features.registry import (
            FEATURE_COLUMNS_DENTAL,
            FEATURE_COLUMNS_HEALTHCARE,
            FEATURE_COLUMNS_HOME_CARE,
            FEATURE_COLUMNS_THERAPY,
            FEATURE_COLUMNS_TRANSPORT,
            validate_feature_frame,
        )
        from rcm.parsing import parse_and_save

        await _cleanup(session)

        # ---- Seed one claim per variant + a matching denied remittance ----
        cases = [
            # (build_fn, label_claim_number_template, variant_str, expected_cols)
            (_build_837p_therapy_edi,    "MVE2E-TH-{:05d}",  "837P", "therapy",   FEATURE_COLUMNS_THERAPY),
            (_build_837p_transport_edi,  "MVE2E-TR-{:05d}",  "837P", "transport", FEATURE_COLUMNS_TRANSPORT),
            (_build_837i_home_care_edi,  "MVE2E-HC-{:05d}",  "837I", "home_care", FEATURE_COLUMNS_HOME_CARE),
            (_build_837d_dental_edi,     "MVE2E-DDS-{:05d}", "837D", "dental",    FEATURE_COLUMNS_DENTAL),
        ]

        # Seed 6 claims per variant so the encoder cv=5 fold split has data
        # and rarity vocab has variety
        rows_per_variant = 6
        for case_idx, (build, cn_tpl, variant, subtype, _) in enumerate(cases):
            for r in range(rows_per_variant):
                seq = case_idx * 100 + r
                edi_bytes = build(seq)
                await parse_and_save(
                    session, edi_bytes, f"{_SEED_TAG}-{subtype}-{seq}.edi",
                )
                cn = cn_tpl.format(seq)
                # Alternate denied/paid so labels are balanced enough for fit
                denied = (r % 2 == 0)
                remit_bytes = _build_835_remit_edi(cn, denied=denied, seq=seq + 5000)
                await parse_and_save(
                    session, remit_bytes, f"{_SEED_TAG}-remit-{subtype}-{seq}.edi",
                )

        await session.execute(text("REFRESH MATERIALIZED VIEW mv_claim_labels"))
        await session.commit()

        # ---- For each variant, load + build features + assert shape ----
        for _, _, variant, subtype, expected_cols in cases:
            df = await load_training_corpus(
                session, service_variant=variant, claim_subtype=subtype,
            )
            assert len(df) >= 1, f"No labelled rows for ({variant},{subtype})"

            builder = FeatureBuilder(service_variant=variant, claim_subtype=subtype)
            artifacts = await builder.fit_transform(session, df, df["denied"])

            assert list(artifacts.features.columns) == list(expected_cols), \
                f"Column drift for ({variant},{subtype})"
            assert len(artifacts.features) == len(df)
            validate_feature_frame(artifacts.features, variant, subtype)

        await _cleanup(session)

    @pytest.mark.asyncio
    async def test_unknown_variant_falls_back_to_global(self, session: AsyncSession):
        from rcm.features.builder import FeatureBuilder
        from rcm.features.registry import FEATURE_COLUMNS_GLOBAL

        # Build a synthetic in-memory frame for an unknown variant — no DB needed
        import pandas as pd
        from datetime import date
        df = pd.DataFrame([{
            "claim_id": 999, "service_variant": "837Z", "claim_subtype": "mystery",
            "claim_number": "GLOBAL-1", "payer_id": None, "patient_id": None,
            "subscriber_id": None, "billing_provider_id": None,
            "rendering_provider_id": None, "referring_provider_id": None,
            "total_charge_amount": 100.0, "facility_type_code": None,
            "frequency_code": "1", "service_from_date": date(2026, 6, 1),
            "service_to_date": date(2026, 6, 1), "submission_date": date(2026, 6, 5),
            "authorization_number": None, "referral_number": None,
            "previous_payer_claim_control_no": None, "variant_data": {},
            "payer_canonical_name": None, "payer_taxonomy": None, "patient_dob": None,
            "patient_gender": None, "billing_provider_taxonomy": None,
            "billing_provider_state": None, "billing_provider_npi": None,
            "rendering_provider_npi": None, "subscriber_cob": None,
            "subscriber_relationship": None, "subscriber_group": None,
            "denied": 0, "primary_cpt": "99213", "primary_dx": "I10",
            "primary_dx_type": "ABK", "primary_pos": "11",
            "procedure_codes": ["99213"], "modifiers": [],
            "revenue_codes": [], "hipps_codes": [], "tooth_numbers": [],
            "ndc_drug_codes": [], "lines_billed_sum": 100.0,
            "lines_units_sum": 1.0, "claim_lines_count": 1,
            "diagnoses_count": 1, "diagnoses": [{"code": "I10", "type": "ABK"}],
            "has_paperwork": False, "attachment_types": [],
            "has_certification": False, "cert_types": [],
            "amounts": {}, "home_care_episode": None, "transport_cert": None,
        }]).set_index("claim_id", drop=False)

        builder = FeatureBuilder(service_variant="837Z", claim_subtype="mystery")
        artifacts = await builder.fit_transform(session, df, df["denied"])

        # Global fallback: 108 universal columns, NO Cat M
        assert list(artifacts.features.columns) == list(FEATURE_COLUMNS_GLOBAL)
        assert len(artifacts.features.columns) == 108
        assert builder._effective_key() == ("_global", "_global")
