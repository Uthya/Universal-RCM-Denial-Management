"""Null-safe extractors + the CAS triplet parser.

`safe_date` emits a WARN on non-empty input that fails to parse (Lesson P3).
`safe_*` never substitutes a placeholder — they return None so the validator
can decide whether to drop the claim.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Primitive extractors
# ---------------------------------------------------------------------------

def safe_element(elements: Sequence[str], index: int, default: str = "") -> str:
    """Return element at `index` or `default` if out-of-range / blank."""
    if 0 <= index < len(elements):
        v = elements[index].strip()
        if v:
            return v
    return default


def safe_decimal(value: str | None) -> Decimal | None:
    """Parse a string to Decimal, returning None on failure or empty input.

    No WARN log here — empty values are normal in EDI (e.g., optional units).
    """
    if not value:
        return None
    v = value.strip()
    if not v:
        return None
    try:
        return Decimal(v)
    except InvalidOperation:
        logger.warning("safe_decimal: could not parse %r as Decimal", v)
        return None


def safe_int(value: str | None) -> int | None:
    if not value:
        return None
    v = value.strip()
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        logger.warning("safe_int: could not parse %r as int", v)
        return None


def safe_date(date_str: str | None) -> date | None:
    """Parse a CCYYMMDD date string.

    Returns None on empty/missing input (silent), or on malformed non-empty
    input (Lesson P3 — emits WARN with the offending value).
    NEVER returns date.today() as a placeholder (Lesson C3).
    """
    if not date_str:
        return None
    s = date_str.strip()
    if not s:
        return None
    if len(s) != 8 or not s.isdigit():
        logger.warning("safe_date: non-CCYYMMDD input rejected (got %r len=%d)", s, len(s))
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        logger.warning("safe_date: malformed CCYYMMDD value rejected (got %r)", s)
        return None


def safe_date_range(date_str: str | None, format_qualifier: str | None) -> tuple[date | None, date | None]:
    """Parse a DTP date respecting DTP02 format qualifier.

    Returns (start, end). For a single date, both elements are the same.
    'RD8' qualifier uses 'YYYYMMDD-YYYYMMDD' format; 'D8' uses a single date.
    """
    if not date_str:
        return None, None
    s = date_str.strip()
    if not s:
        return None, None
    qual = (format_qualifier or "").strip().upper()
    if qual == "RD8" and "-" in s:
        a, b = s.split("-", 1)
        return safe_date(a), safe_date(b)
    d = safe_date(s)
    return d, d


# ---------------------------------------------------------------------------
# CAS triplet parsing — port forward from v1 with explicit stride disambiguation
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CasTriplet:
    """One (group_code, reason_code, amount, quantity) tuple from CAS.

    group_code is carried at the CAS level (CAS01) and copied onto each
    triplet for downstream convenience.
    """

    group_code: str
    reason_code: str
    amount: Decimal
    quantity: Decimal | None


@dataclass(frozen=True, slots=True)
class CasParseResult:
    triplets: tuple[CasTriplet, ...]
    stride: int                    # 2 or 3
    complete: bool                 # False if a triplet parse failed mid-way
    warnings: tuple[str, ...]      # human-readable, safe to log


def _strip_trailing_empties(items: list[str]) -> list[str]:
    while items and not items[-1].strip():
        items.pop()
    return items


def _parse_cas_triplets(elements: Sequence[str]) -> CasParseResult:
    """Parse a CAS segment's triplets, handling the spec stride-3 form and
    the non-spec compact stride-2 form some clearinghouses emit.

    Stride-3 is the X12 spec: each triplet is (reason, amount, quantity).
    Stride-2 is a compact non-spec form some payers emit: (reason, amount)
    with quantity dropped entirely.

    Disambiguation: count meaningful (non-blank) elements after CAS01. If
    there are no internal empties AND the effective count is 1 mod 3, the
    file is using stride-2; we try that first and emit a WARNING. Otherwise
    use stride-3.
    """
    if len(elements) < 4:
        return CasParseResult(triplets=(), stride=3, complete=False,
                              warnings=("CAS too short to contain a triplet",))

    group_code = safe_element(elements, 1)
    if not group_code:
        return CasParseResult(triplets=(), stride=3, complete=False,
                              warnings=("CAS01 group code missing",))

    body = _strip_trailing_empties(list(elements[2:]))
    if not body:
        return CasParseResult(triplets=(), stride=3, complete=False,
                              warnings=("CAS has no triplet body",))

    has_internal_empty = any(not body[i].strip() for i in range(len(body) - 1))
    effective_count = len(body)

    # Try stride-2 only when the spec stride-3 obviously can't fit
    warnings: list[str] = []
    stride = 3
    if not has_internal_empty and effective_count % 3 == 1 and effective_count % 2 == 0:
        stride = 2
        warnings.append(
            f"CAS uses compact stride-2 form (non-spec); element count={effective_count}"
        )
    elif effective_count % 3 != 0 and effective_count % 2 == 0 and not has_internal_empty:
        # Length divides by 2 cleanly, doesn't by 3 — likely stride-2
        stride = 2
        warnings.append(
            f"CAS uses compact stride-2 form (non-spec); element count={effective_count}"
        )

    triplets: list[CasTriplet] = []
    i = 0
    complete = True
    while i + stride - 1 < len(body):
        reason = body[i].strip()
        amount = safe_decimal(body[i + 1])
        quantity = safe_decimal(body[i + 2]) if stride == 3 else None

        if not reason or amount is None:
            warnings.append(
                f"CAS triplet at position {i} could not be parsed "
                f"(reason={reason!r}, amount={body[i + 1]!r})"
            )
            complete = False
            break

        triplets.append(CasTriplet(
            group_code=group_code,
            reason_code=reason,
            amount=amount,
            quantity=quantity,
        ))
        i += stride

    if i < len(body):
        # Leftover elements past the last full triplet
        warnings.append(
            f"CAS has {len(body) - i} trailing element(s) past the last triplet"
        )
        complete = False

    return CasParseResult(
        triplets=tuple(triplets),
        stride=stride,
        complete=complete,
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# Composite helpers
# ---------------------------------------------------------------------------

def split_composite(value: str, component_sep: str) -> list[str]:
    """Split a composite element into its sub-elements.

    Returns an empty list for empty input.
    """
    if not value:
        return []
    return [p.strip() for p in value.split(component_sep)]


def composite_at(parts: Sequence[str], index: int, default: str = "") -> str:
    """Get a sub-element from a previously-split composite."""
    if 0 <= index < len(parts):
        v = parts[index].strip()
        if v:
            return v
    return default
