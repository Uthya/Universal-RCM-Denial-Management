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
