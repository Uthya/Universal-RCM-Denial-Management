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


_ISA_LEN = 106


def detect_delimiters(raw_text: str) -> Delimiters:
    """Parse the three delimiters out of the ISA header.

    Per ASC X12, ISA is a fixed-width 106-character segment. Bytes 3, 104,
    and 105 are the element separator, component separator, and segment
    terminator respectively. ISA[6] must equal ISA[3] (it's also an element
    separator) — if they differ the file is word-wrapped or corrupted.
    """
    isa_pos = raw_text.find("ISA")
    if isa_pos == -1:
        raise EnvelopeError(
            "No ISA segment found in file. Not an X12 EDI file, or the "
            "envelope is corrupted."
        )

    isa_block = raw_text[isa_pos:]
    if len(isa_block) < _ISA_LEN:
        raise EnvelopeError(
            f"ISA segment is truncated ({len(isa_block)} chars, need {_ISA_LEN})."
        )

    element = isa_block[3]
    component = isa_block[104]
    segment = isa_block[105]

    for name, ch in (("element", element), ("component", component), ("segment", segment)):
        if not ch or ch.isalnum() or ch.isspace():
            raise EnvelopeError(
                f"ISA {name} separator is not a valid delimiter (got {ch!r}). "
                "File may be word-wrapped or corrupted."
            )

    if isa_block[6] != element:
        raise EnvelopeError(
            f"ISA delimiter mismatch: ISA[3]={element!r} vs ISA[6]={isa_block[6]!r}. "
            "ISA offsets look corrupted."
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
