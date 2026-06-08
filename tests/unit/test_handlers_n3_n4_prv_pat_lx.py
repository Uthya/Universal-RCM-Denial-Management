"""Tests for the address (N3/N4), provider taxonomy (PRV), patient (PAT)
and structural (LX) segment handlers added in CR-049.

Each test parses an inline EDI snippet and asserts:
    * The segment was recognised (handler_status != 'skipped_unhandled').
    * The side-effect on ctx (address dict, taxonomy_code, etc.) is correct.
"""

from __future__ import annotations

from rcm.parsing import parse_edi
from rcm.parsing.dispatchers import get_handler


# ---------------------------------------------------------------------------
# Registry checks — confirm every variant + segment combo has a handler.
# ---------------------------------------------------------------------------

class TestHandlerRegistry:
    def test_n3_registered_for_all_837(self):
        for v in ("837P", "837I", "837D"):
            handler, found = get_handler(v, "N3")
            assert found and handler is not None, f"N3 missing for {v}"

    def test_n4_registered_for_all_837(self):
        for v in ("837P", "837I", "837D"):
            handler, found = get_handler(v, "N4")
            assert found and handler is not None, f"N4 missing for {v}"

    def test_prv_registered_for_all_837(self):
        for v in ("837P", "837I", "837D"):
            handler, found = get_handler(v, "PRV")
            assert found and handler is not None, f"PRV missing for {v}"

    def test_pat_registered_for_all_837(self):
        for v in ("837P", "837I", "837D"):
            handler, found = get_handler(v, "PAT")
            assert found and handler is not None, f"PAT missing for {v}"

    def test_lx_silent_skip_universal(self):
        # LX is in _SILENT_SKIP under "*" — found=True, handler=None.
        for v in ("837P", "837I", "837D", "835"):
            handler, found = get_handler(v, "LX")
            assert found, f"LX not recognised for {v}"
            assert handler is None, f"LX should be silent-skip, got handler for {v}"


# ---------------------------------------------------------------------------
# End-to-end parse — minimal 837P with the new segments.
# ---------------------------------------------------------------------------

_MINIMAL_837P_WITH_ADDRESSES = (
    "ISA*00*          *00*          *ZZ*SUBMITTER      *ZZ*RECEIVER       *260101*1200*^*00501*000000001*0*P*:~"
    "GS*HC*SUBMITTER*RECEIVER*20260101*1200*1*X*005010X222A1~"
    "ST*837*0001*005010X222A1~"
    "BHT*0019*00*0001*20260101*1200*CH~"
    "NM1*41*2*SUBMITTER CO*****46*S001~"
    "NM1*40*2*RECEIVER CO*****46*R001~"
    "HL*1**20*1~"
    "PRV*BI*PXC*207Q00000X~"
    "NM1*85*2*BILLING CLINIC*****XX*1234567890~"
    "N3*123 MAIN STREET*SUITE 200~"
    "N4*BOSTON*MA*02101~"
    "HL*2*1*22*0~"
    "SBR*P*18*GROUP01*****CI~"
    "NM1*IL*1*DOE*JOHN****MI*MEMBER12345~"
    "N3*456 PATIENT WAY~"
    "N4*BOSTON*MA*02102~"
    "DMG*D8*19800101*M~"
    "NM1*PR*2*AETNA*****PI*AETNA01~"
    "CLM*HC-001*150***11:B:1*Y*A*Y*Y~"
    "HI*ABK:I10*ABF:E11~"
    "DTP*472*D8*20260601~"
    "NM1*82*1*SMITH*JANE****XX*9876543210~"
    "PRV*PE*PXC*208D00000X~"
    "N3*789 RENDERING BLVD~"
    "N4*BOSTON*MA*02103~"
    "PAT*19******01*180~"
    "LX*1~"
    "SV1*HC:99213:25*150*UN*1***1:2~"
    "DTP*472*D8*20260601~"
    "SE*22*0001~"
    "GE*1*1~"
    "IEA*1*000000001~"
)


class TestEndToEndParse:
    def setup_method(self):
        self.ctx = parse_edi(_MINIMAL_837P_WITH_ADDRESSES, "test.edi")

    def test_no_skipped_unhandled_for_target_segments(self):
        skipped = self.ctx.unhandled_segment_counts
        for seg in ("N3", "N4", "PRV", "PAT", "LX"):
            assert skipped.get(seg, 0) == 0, (
                f"{seg} was reported skipped_unhandled ({skipped.get(seg)} times) "
                f"but should be handled."
            )

    def test_raw_segments_marked_handled(self):
        handled_status = {seg.segment_name: seg.handler_status
                          for seg in self.ctx.raw_segments}
        for seg in ("N3", "N4", "PRV", "PAT", "LX"):
            assert seg in handled_status, f"{seg} missing from raw_segments"
            assert handled_status[seg] == "handled", (
                f"{seg} handler_status={handled_status[seg]} (expected 'handled')"
            )

    def test_billing_provider_state_and_taxonomy_persisted(self):
        billing = next(
            p for p in self.ctx.providers if p.provider_type == "billing"
        )
        assert billing.state == "MA"
        assert billing.taxonomy_code == "207Q00000X"

    def test_rendering_provider_state_and_taxonomy_persisted(self):
        rendering = next(
            p for p in self.ctx.providers if p.provider_type == "rendering"
        )
        assert rendering.state == "MA"
        assert rendering.taxonomy_code == "208D00000X"

    def test_addresses_attached_to_claim_variant_data(self):
        claim = self.ctx.claims[0]
        addrs = claim.variant_data.get("addresses", {})
        # Billing-provider address should be on the claim.
        assert "billing_provider" in addrs
        assert addrs["billing_provider"]["state"] == "MA"
        assert addrs["billing_provider"]["zip"] == "02101"
        assert addrs["billing_provider"]["street1"] == "123 MAIN STREET"
        assert addrs["billing_provider"]["street2"] == "SUITE 200"
        # Rendering provider address.
        assert addrs.get("rendering_provider", {}).get("zip") == "02103"

    def test_pat_relationship_and_weight_on_claim(self):
        claim = self.ctx.claims[0]
        patient_bucket = claim.variant_data.get("patient", {})
        assert patient_bucket.get("relationship_to_subscriber") == "19"
        assert patient_bucket.get("weight_unit") == "01"
        assert patient_bucket.get("weight") == 180.0

    def test_segment_handled_event_for_n4(self):
        n4_events = [e for e in self.ctx.parse_events
                     if e.event_type == "segment_handled" and e.segment_name == "N4"]
        # Three N4 segments: billing, subscriber, rendering provider.
        assert len(n4_events) == 3
        labels = sorted(e.details.get("label") for e in n4_events)
        assert "billing_provider" in labels
        assert "rendering_provider" in labels

    def test_lx_does_not_emit_segment_handled_event(self):
        # LX is silent-skip; we don't want it polluting parse_events.
        lx_events = [e for e in self.ctx.parse_events if e.segment_name == "LX"]
        assert lx_events == []
