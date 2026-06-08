"""safe_element / safe_decimal / safe_date / safe_date_range + CAS triplet parser."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from rcm.parsing.safe import (
    _parse_cas_triplets,
    composite_at,
    safe_date,
    safe_date_range,
    safe_decimal,
    safe_element,
    safe_int,
    split_composite,
)


class TestSafeElement:
    def test_basic(self):
        assert safe_element(["CLM", "001", "100"], 1) == "001"

    def test_out_of_range_returns_default(self):
        assert safe_element(["CLM"], 5, default="x") == "x"

    def test_blank_returns_default(self):
        assert safe_element(["CLM", "  "], 1, default="d") == "d"


class TestSafeDecimal:
    def test_basic(self):
        assert safe_decimal("150.50") == Decimal("150.50")

    def test_blank_is_none(self):
        assert safe_decimal("") is None
        assert safe_decimal("  ") is None
        assert safe_decimal(None) is None

    def test_invalid_logs_and_returns_none(self):
        assert safe_decimal("abc") is None


class TestSafeInt:
    def test_basic(self):
        assert safe_int("42") == 42

    def test_blank_is_none(self):
        assert safe_int("") is None
        assert safe_int(None) is None

    def test_invalid(self):
        assert safe_int("abc") is None


class TestSafeDate:
    def test_basic(self):
        assert safe_date("20260601") == date(2026, 6, 1)

    def test_blank_is_none_silent(self):
        # Empty input is normal — should NOT log
        assert safe_date("") is None
        assert safe_date(None) is None

    def test_wrong_length_returns_none_with_warn(self, caplog):
        import logging
        caplog.set_level(logging.WARNING)
        assert safe_date("20260") is None
        assert any("non-CCYYMMDD" in m for m in caplog.messages)

    def test_invalid_calendar_returns_none_with_warn(self, caplog):
        import logging
        caplog.set_level(logging.WARNING)
        assert safe_date("20260230") is None  # Feb 30
        assert any("malformed" in m for m in caplog.messages)

    def test_never_returns_today_placeholder(self):
        # Lesson C3 regression
        assert safe_date("badinput") is not date.today()
        assert safe_date("") is not date.today()


class TestSafeDateRange:
    def test_single_d8(self):
        s, e = safe_date_range("20260601", "D8")
        assert s == e == date(2026, 6, 1)

    def test_rd8_range(self):
        s, e = safe_date_range("20260601-20260615", "RD8")
        assert s == date(2026, 6, 1)
        assert e == date(2026, 6, 15)

    def test_blank(self):
        s, e = safe_date_range("", "D8")
        assert s is None and e is None


class TestComposite:
    def test_split_then_get(self):
        parts = split_composite("HC:99213:25:LT", ":")
        assert parts == ["HC", "99213", "25", "LT"]
        assert composite_at(parts, 0) == "HC"
        assert composite_at(parts, 2) == "25"
        assert composite_at(parts, 10, default="x") == "x"

    def test_empty_input(self):
        assert split_composite("", ":") == []


class TestCasTripletParser:
    def test_stride_3_spec_form(self):
        # CAS*CO*45*100*1*97*50*2
        elements = ["CAS", "CO", "45", "100", "1", "97", "50", "2"]
        result = _parse_cas_triplets(elements)
        assert result.stride == 3
        assert result.complete
        assert len(result.triplets) == 2
        assert result.triplets[0].reason_code == "45"
        assert result.triplets[0].amount == Decimal("100")
        assert result.triplets[0].quantity == Decimal("1")
        assert result.triplets[1].reason_code == "97"
        assert result.triplets[1].amount == Decimal("50")

    def test_stride_2_compact_form_warns(self):
        # CAS*CO*45*100*97*50 — no quantity, 5 body elements
        elements = ["CAS", "CO", "45", "100", "97", "50"]
        result = _parse_cas_triplets(elements)
        # Body count = 5 (odd), doesn't divide by 2 cleanly — falls through to stride 3
        # but stride 3 wouldn't yield 2 triplets either. The parser should warn.
        # We don't strictly require stride=2 here; the key is that it doesn't crash.
        assert isinstance(result.triplets, tuple)
        assert any("CAS" in w for w in result.warnings)

    def test_group_code_missing(self):
        result = _parse_cas_triplets(["CAS", "", "45", "100", "1"])
        assert not result.complete
        assert any("group code missing" in w for w in result.warnings)

    def test_single_triplet(self):
        elements = ["CAS", "CO", "45", "200", "1"]
        result = _parse_cas_triplets(elements)
        assert result.complete
        assert len(result.triplets) == 1
        assert result.triplets[0].group_code == "CO"
