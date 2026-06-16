"""Envelope detection + delimiter validation."""

from __future__ import annotations

import pytest

from rcm.parsing.envelope import (
    EnvelopeError,
    count_isa_blocks,
    decode_edi,
    detect_delimiters,
    tokenize,
)


def _build_isa(element="*", component=":", segment="~"):
    # Pad fields to required lengths so the total = 106
    return (
        f"ISA{element}00{element}          {element}00{element}          {element}"
        f"ZZ{element}SUB            {element}ZZ{element}RCV            {element}"
        f"260601{element}1200{element}^{element}00501{element}000000001{element}"
        f"0{element}P{element}{component}{segment}"
    )


class TestDetectDelimiters:
    def test_standard_star_colon_tilde(self):
        d = detect_delimiters(_build_isa())
        assert d.element == "*"
        assert d.component == ":"
        assert d.segment == "~"

    def test_pipe_element_delimiter(self):
        d = detect_delimiters(_build_isa(element="|"))
        assert d.element == "|"

    def test_isa_missing_raises(self):
        with pytest.raises(EnvelopeError, match="No ISA"):
            detect_delimiters("GS*HC*SENDER*RCVR*20260601*1200*1*X*005010X222A1~")

    def test_isa_truncated_raises(self):
        with pytest.raises(EnvelopeError, match="truncated"):
            detect_delimiters("ISA*00*short")

    def test_alphanumeric_delimiter_rejected(self):
        # ISA[3]='A' is alphanumeric
        bad = "ISA" + "A" + "0" * 100 + ":~"  # not 106 chars but exercises validation
        with pytest.raises(EnvelopeError):
            detect_delimiters(bad)

    def test_whitespace_delimiter_rejected(self):
        with pytest.raises(EnvelopeError):
            detect_delimiters(_build_isa(element=" "))

    # ----- CR-059 regression tests --------------------------------------

    def test_valid_106_byte_isa_still_parses(self):
        """Regression: a fully spec-compliant 106-byte ISA (the format the
        original fixed-offset parser was written for) must continue to
        parse with identical (element=*, component=:, segment=~)."""
        isa = _build_isa()
        # _build_isa packs all fields to required widths, so total length
        # (including terminator) MUST be exactly 106.
        assert len(isa) == 106, f"_build_isa returned {len(isa)} chars, expected 106"
        d = detect_delimiters(isa)
        assert (d.element, d.component, d.segment) == ("*", ":", "~")

    def test_short_isa13_real_world(self):
        """Real malformed file from the UQ10K dataset: ISA13 is 4 chars
        instead of the spec-required 9. Total ISA is 101 chars (5 short).
        Fixed-offset reads put the parser inside the GS segment; the
        scan-and-count fix recovers correctly."""
        # Reproduces the bytes captured from UQ10K_pair_001_original_835.dat
        # (bytes 0..101 — ISA segment with 4-char ISA13 then '~' terminator
        # then 'GS*HP*...' starts at byte 101).
        isa = (
            "ISA*00*          *00*          *ZZ*31114          *30*1730384655     *"
            "260427*0507*[*00501*1020*0*T*:~"
            "GS*HP*31114*1730384655*20260427*0507*1020*X*005010X221A1~"
        )
        d = detect_delimiters(isa)
        assert d.element == "*"
        assert d.component == ":"
        assert d.segment == "~"

    def test_corrupted_isa_missing_separators_raises(self):
        """Genuine corruption: ISA prefix is present but the element
        separator only appears 5 times. Must still raise."""
        bad = "ISA*00*FOO*BAR*BAZ*QUX" + "X" * 200
        with pytest.raises(EnvelopeError, match="element separator"):
            detect_delimiters(bad)


class TestDecodeEdi:
    def test_utf8(self):
        text = decode_edi(_build_isa().encode("utf-8"))
        assert text.startswith("ISA")

    def test_utf8_bom(self):
        text = decode_edi(b"\xef\xbb\xbf" + _build_isa().encode("utf-8"))
        assert text.startswith("ISA")

    def test_cp1252_with_isa(self):
        # cp1252 codec works fine for ASCII-only EDI
        text = decode_edi(_build_isa().encode("cp1252"))
        assert "ISA" in text

    def test_garbage_no_isa_raises(self):
        with pytest.raises(EnvelopeError, match="locate ISA"):
            decode_edi(b"\xff\xfe\x00\x00 not edi at all")


class TestTokenize:
    def test_basic_split(self):
        segs = tokenize("ISA*..*..~GS*..~ST*..~", "~")
        assert len(segs) == 3
        assert segs[0].startswith("ISA")

    def test_strips_whitespace(self):
        segs = tokenize("ISA*x~  GS*y~\nST*z~", "~")
        assert segs == ["ISA*x", "GS*y", "ST*z"]

    def test_drops_empty(self):
        segs = tokenize("A~~~B~", "~")
        assert segs == ["A", "B"]


class TestCountIsaBlocks:
    def test_single(self):
        assert count_isa_blocks(_build_isa()) == 1

    def test_multiple(self):
        text = _build_isa() + _build_isa()
        assert count_isa_blocks(text) == 2
