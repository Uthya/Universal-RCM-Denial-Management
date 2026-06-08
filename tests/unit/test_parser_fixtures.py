"""Parse each EDI fixture and assert key shape facts.

These tests don't touch the DB — they exercise parse_edi end-to-end.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from rcm.parsing import parse_edi
from rcm.parsing.envelope import EnvelopeError
from rcm.parsing.validators import run_all


FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


class Test837pHealthcareHealthy:
    @pytest.fixture
    def ctx(self):
        return parse_edi(_read("837p_healthcare_healthy.edi"), "837p_healthcare_healthy.edi")

    def test_file_metadata(self, ctx):
        assert ctx.file_type == "edi_837"
        assert ctx.service_variant == "837P"
        assert ctx.claim_subtype == "healthcare"
        assert ctx.implementation_guide == "005010X222A1"

    def test_one_claim(self, ctx):
        assert len(ctx.claims) == 1
        c = ctx.claims[0]
        assert c.claim_number == "HC-001"
        assert c.total_charge_amount == Decimal("150")
        assert c.service_from_date == date(2026, 6, 1)
        assert c.authorization_number == "AUTH12345"
        assert c.billing_provider_npi == "1234567890"
        assert c.rendering_provider_npi == "9876543210"
        assert c.patient_member_id == "MEMBER12345"   # self via SBR02=18
        assert c.payer_canonical_name == "AETNA"

    def test_lines_and_dx(self, ctx):
        c = ctx.claims[0]
        assert len(c.lines) == 1
        line = c.lines[0]
        assert line.procedure_code == "99213"
        assert line.modifier1 == "25"
        assert line.diagnosis_pointers == [1, 2]
        assert line.place_of_service == "11"
        assert len(c.diagnoses) == 2
        assert c.diagnoses[0].diagnosis_code == "I10"
        assert c.diagnoses[0].diagnosis_type == "ABK"

    @pytest.mark.asyncio
    async def test_no_validation_errors(self, ctx):
        await run_all(ctx)
        errors = [e for e in ctx.parse_errors if e.severity == "ERROR"]
        assert errors == []


class Test837pNoServiceDateDropsClaim:
    @pytest.mark.asyncio
    async def test_lesson_c3(self):
        ctx = parse_edi(_read("837p_healthcare_no_dtp472.edi"), "837p_healthcare_no_dtp472.edi")
        assert len(ctx.claims) == 1
        await run_all(ctx)
        c = ctx.claims[0]
        assert c.dropped, "Claim missing DTP*472 must be dropped (Lesson C3)"
        assert any("service_from_date" in r for r in c.drop_reasons)


class Test837pTherapy:
    @pytest.fixture
    def ctx(self):
        return parse_edi(_read("837p_therapy_pt_gp.edi"), "837p_therapy_pt_gp.edi")

    def test_subtype_is_therapy(self, ctx):
        # Routing sees GP modifier -> 'therapy'
        assert ctx.claims[0].claim_subtype == "therapy"

    def test_modifiers_captured(self, ctx):
        # Both lines carry GP modifier
        assert all(line.modifier1 == "GP" for line in ctx.claims[0].lines)

    def test_pwk_captured(self, ctx):
        assert len(ctx.claims[0].attachments) == 1
        att = ctx.claims[0].attachments[0]
        assert att.report_type_code == "PN"
        assert att.transmission_code == "EL"


class Test837pTransport:
    @pytest.fixture
    def ctx(self):
        return parse_edi(_read("837p_transport_ambulance.edi"), "837p_transport_ambulance.edi")

    def test_subtype_is_transport(self, ctx):
        # Primary CPT A0429 → transport
        assert ctx.claims[0].claim_subtype == "transport"

    def test_cr1_captured(self, ctx):
        c = ctx.claims[0]
        assert c.transport_cert is not None
        assert c.transport_cert.patient_weight_lbs == 180
        assert c.transport_cert.transport_miles == Decimal("12")
        assert c.transport_cert.transport_reason_code == "B"
        # CR103='N' → not emergent
        assert c.transport_cert.emergent is False

    def test_crc_certification_present(self, ctx):
        assert any(cert.certification_type == "ambulance"
                   for cert in ctx.claims[0].certifications)


class Test837iHomeCare:
    @pytest.fixture
    def ctx(self):
        return parse_edi(_read("837i_home_care.edi"), "837i_home_care.edi")

    def test_variant_and_subtype(self, ctx):
        assert ctx.service_variant == "837I"
        assert ctx.claims[0].claim_subtype == "home_care"

    def test_statement_period(self, ctx):
        c = ctx.claims[0]
        assert c.service_from_date == date(2026, 5, 1)
        assert c.service_to_date == date(2026, 5, 30)

    def test_revenue_codes_captured(self, ctx):
        codes = sorted(line.revenue_code for line in ctx.claims[0].lines if line.revenue_code)
        assert codes == ["0551", "0571", "0581"]

    def test_homebound_certification(self, ctx):
        c = ctx.claims[0]
        assert c.home_care_episode is not None
        assert c.home_care_episode.homebound_certified is True


class Test837dDental:
    @pytest.fixture
    def ctx(self):
        return parse_edi(_read("837d_dental_simple.edi"), "837d_dental_simple.edi")

    def test_variant_and_subtype(self, ctx):
        assert ctx.service_variant == "837D"
        assert ctx.claims[0].claim_subtype == "dental"

    def test_too_attached_to_line(self, ctx):
        line = ctx.claims[0].lines[0]
        assert line.procedure_code == "D2391"
        assert line.tooth_number == "14"
        # TOO03 composite "M:O:D" → concatenated to "MOD"
        assert line.tooth_surfaces == "MOD"


class Test835Remittance:
    @pytest.fixture
    def ctx(self):
        return parse_edi(_read("835_healthy_full.edi"), "835_healthy_full.edi")

    def test_file_type(self, ctx):
        assert ctx.file_type == "edi_835"

    def test_two_remittances(self, ctx):
        assert len(ctx.remittances) == 2

    def test_paid_claim(self, ctx):
        r = ctx.remittances[0]
        assert r.claim_number == "HC-001"
        assert r.claim_status_code == "1"
        assert r.paid_amount == Decimal("150")
        assert r.remittance_date == date(2026, 6, 15)  # DTM*050 OR DTM*405 fallback

    def test_denied_claim(self, ctx):
        r = ctx.remittances[1]
        assert r.claim_number == "HC-NO-DTP"
        assert r.claim_status_code == "4"
        assert r.paid_amount == Decimal("0")
        assert len(r.adjustments) == 1
        adj = r.adjustments[0]
        assert adj.adjustment_group_code == "CO"
        assert adj.adjustment_reason_code == "45"
        assert adj.adjustment_amount == Decimal("200")
        assert len(r.remark_codes) == 1
        assert r.remark_codes[0].remark_code == "N362"

    def test_payer_captured(self, ctx):
        assert any(p.canonical_name == "AETNA" for p in ctx.payers)


class TestEdgeCases:
    def test_no_isa_raises(self):
        with pytest.raises(EnvelopeError, match="No ISA"):
            parse_edi(_read("edge_no_isa.edi"), "edge_no_isa.edi")


class TestRawSegmentsAlwaysPersisted:
    def test_every_segment_recorded(self):
        ctx = parse_edi(_read("837p_healthcare_healthy.edi"), "x.edi")
        # All non-empty segments produce a RawSegmentRec
        assert len(ctx.raw_segments) > 10
        # ISA and GS are typically silently handled, so status should be handled
        statuses = {rs.handler_status for rs in ctx.raw_segments}
        assert "handled" in statuses
        # Unhandled segments (N3/N4/etc.) get 'skipped_unhandled'
        unhandled_segs = [rs for rs in ctx.raw_segments if rs.handler_status == "skipped_unhandled"]
        assert all(rs.raw_segment_text for rs in unhandled_segs)  # never empty raw text
