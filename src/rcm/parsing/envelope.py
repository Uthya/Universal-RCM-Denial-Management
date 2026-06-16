"""ISA / GS / ST envelope handling and the delimiter detection that everything
downstream depends on.

Lessons baked in:
* **29c0fd4** — ISA hardening: delimiters must be non-alphanumeric, non-whitespace;
  ISA[3] == ISA[6]; raise if ISA segment isn't exactly 106 chars.
* **P2** — encoding fallback chain (`utf-8-sig` → `utf-8` → `cp1252` → `latin-1`)
  with an ISA-presence sanity check; latin-1 alone silently passes on garbage.
"""

from __future__ import annotations

from dataclasses import dataclass


class EnvelopeError(ValueError):
    """Raised when the EDI envelope can't be parsed.

    Callers should map this to HTTP 400; the message is safe to surface to
    users (no PHI, just structural info)."""


@dataclass(frozen=True, slots=True)
class Delimiters:
    """Three character-level separators the ISA segment publishes.

    `element`   — separates elements within a segment (typically '*')
    `component` — separates sub-elements within a composite element (':')
    `segment`   — terminates a segment ('~' / '\\n' / '\\r\\n')
    """

    element: str = "*"
    component: str = ":"
    segment: str = "~"


_ISA_LEN = 106                # X12 spec: ISA + 106 bytes including terminator
_ISA_MIN_SCAN_LEN = 80        # Lower bound — even maximally-short ISA fields can't pack 16
                              # element separators + ISA16 + terminator into less than this.
_ISA_ELEMENT_COUNT = 16       # ISA has 16 fields (ISA01..ISA16) -> 16 element separators


def detect_delimiters(raw_text: str) -> Delimiters:
    """Parse the three delimiters out of the ISA header.

    Per ASC X12 the ISA segment is fixed-width 106 chars and the delimiters
    live at hard-coded offsets (3, 104, 105). Some real-world generators
    short-pad ISA13 (Interchange Control Number) — the spec says 9 chars
    zero-padded, but a 4-char value is common in the wild. That shortens
    the ISA by 5 bytes and breaks fixed-offset reads (CR-059 / UQ10K
    dataset hit this).

    We tolerate variable-length ISA fields by scanning for the 16 element
    separators that ISA01..ISA16 imply. The byte after the 16th separator
    is ISA16 (component separator); the byte after that is the segment
    terminator. Genuine corruption (no ISA prefix, fewer than 16 element
    separators, alphanumeric/whitespace delimiters) is still rejected.
    """
    isa_pos = raw_text.find("ISA")
    if isa_pos == -1:
        raise EnvelopeError(
            "No ISA segment found in file. Not an X12 EDI file, or the "
            "envelope is corrupted."
        )

    isa_block = raw_text[isa_pos:]
    if len(isa_block) < _ISA_MIN_SCAN_LEN:
        raise EnvelopeError(
            f"ISA segment is truncated ({len(isa_block)} chars, need at least "
            f"{_ISA_MIN_SCAN_LEN})."
        )

    # ISA01 starts at byte 3, so isa_block[3] is the first element separator.
    element = isa_block[3]
    if not element or element.isalnum() or element.isspace():
        raise EnvelopeError(
            f"ISA element separator is not a valid delimiter (got {element!r}). "
            "File may be word-wrapped or corrupted."
        )

    # Scan forward, count occurrences of `element`. The 16th match marks the
    # boundary between ISA15 and ISA16.
    sep_positions: list[int] = []
    for i in range(3, len(isa_block)):
        if isa_block[i] == element:
            sep_positions.append(i)
            if len(sep_positions) == _ISA_ELEMENT_COUNT:
                break

    if len(sep_positions) < _ISA_ELEMENT_COUNT:
        raise EnvelopeError(
            f"ISA element separator {element!r} appears only "
            f"{len(sep_positions)} times — expected {_ISA_ELEMENT_COUNT}. "
            "ISA offsets look corrupted."
        )

    isa16_pos = sep_positions[-1] + 1     # ISA16 (component separator)
    seg_term_pos = isa16_pos + 1          # segment terminator immediately after
    if seg_term_pos >= len(isa_block):
        raise EnvelopeError(
            "ISA truncated before component separator + segment terminator. "
            "ISA offsets look corrupted."
        )

    component = isa_block[isa16_pos]
    segment = isa_block[seg_term_pos]

    for name, ch in (("component", component), ("segment", segment)):
        if not ch or ch.isalnum() or ch.isspace():
            raise EnvelopeError(
                f"ISA {name} separator is not a valid delimiter (got {ch!r}). "
                "File may be word-wrapped or corrupted."
            )

    return Delimiters(element=element, component=component, segment=segment)


def decode_edi(raw_bytes: bytes) -> str:
    """Decode raw bytes to text with a sane fallback chain.

    Lesson P2: latin-1 never raises and so silently accepts garbage; we use
    it only as a last resort AND require the ISA marker to appear in the
    first kilobyte before returning. UTF-16 / UTF-32 files are not auto-
    detected (rare in EDI); they fail the ISA sanity check and raise.
    """
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            text = raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "ISA" in text[:1024]:
            return text

    text = raw_bytes.decode("latin-1")
    if "ISA" not in text[:1024]:
        raise EnvelopeError(
            "Could not locate ISA segment after decoding attempts (utf-8-sig, "
            "utf-8, cp1252, latin-1). File may be UTF-16/UTF-32 or not EDI."
        )
    return text


def tokenize(raw_text: str, segment_terminator: str) -> list[str]:
    """Split raw text into individual segment strings, stripping whitespace.

    Empty segments (e.g. trailing newline after a final terminator) are
    skipped. The returned list preserves segment order.
    """
    return [seg.strip() for seg in raw_text.split(segment_terminator) if seg.strip()]


def count_isa_blocks(raw_text: str) -> int:
    """How many ISA interchange envelopes does this file contain?

    v1 silently parsed only the first when there were multiple. v2 surfaces
    this so the caller can reject (preferred) or loop over each.
    """
    count = 0
    pos = 0
    while True:
        i = raw_text.find("ISA", pos)
        if i == -1:
            return count
        # Skip occurrences inside data (e.g., "VISA" payer name) by requiring
        # the next ISA to be far enough past the previous one to fit a complete
        # interchange. A heuristic of 106 chars (one ISA block) is enough.
        count += 1
        pos = i + _ISA_LEN
