"""EDI parsing layer — 837P/I/D + 835.

Public surface:
    parse_edi(raw_text, file_name, parser_version) -> ParseContext
    save_parse_context(session, ctx) -> EdiFile
    parse_and_save(session, raw_bytes, file_name) -> EdiFile

Internals:
    envelope     — ISA/GS/ST envelope detection + delimiter validation
    safe         — null-safe element/decimal/date extractors + CAS triplet parser
    context      — ParseContext dataclass shared across all handlers
    routing      — variant + subtype detection
    dispatchers  — per-transaction-set dispatch (837P/I/D/835)
    handlers     — one module per segment family
    validators   — 4-tier validator chain
    persistence  — bulk Core insert layer (decoupled from parse)
"""

from rcm.parsing.context import ParseContext, ParseEvent, ValidationError
from rcm.parsing.envelope import Delimiters, decode_edi, detect_delimiters, tokenize
from rcm.parsing.parser import parse_and_save, parse_edi
from rcm.parsing.persistence import save_parse_context

__all__ = [
    "Delimiters",
    "ParseContext",
    "ParseEvent",
    "ValidationError",
    "decode_edi",
    "detect_delimiters",
    "parse_and_save",
    "parse_edi",
    "save_parse_context",
    "tokenize",
]
