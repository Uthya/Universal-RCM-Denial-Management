"""Handler registry + dispatch loop.

Lesson: handlers may raise *any* exception (KeyError, TypeError, IndexError,
ValueError, etc.). v1 only caught ValueError/IndexError and crashed the whole
parse on the others. We catch a broad `Exception` here and record it as a
parse_error + ParseEvent so the rest of the file still processes.
"""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rcm.parsing.context import ParseContext

logger = logging.getLogger(__name__)


# Handler signature:
#   handler(elements, raw_segment_text, ctx)
HandlerFn = Callable[[Sequence[str], str, "ParseContext"], None]


# (transaction_set, segment_name) → handler.  '*' = any variant.
HANDLER_REGISTRY: dict[tuple[str, str], HandlerFn | None] = {}


# Segments we explicitly skip without warning (envelope handled outside the loop)
_SILENT_SKIP: frozenset[tuple[str, str]] = frozenset({
    ("*", "ISA"),
    ("*", "GS"),
    ("*", "ST"),
    ("*", "SE"),
    ("*", "GE"),
    ("*", "IEA"),
    ("*", "BHT"),     # transaction set header — purpose is captured separately
    ("*", "LE"),      # loop terminator
    ("*", "LS"),      # loop separator
    ("*", "LX"),      # service-line loop assigned-number — structural; SV1/SV2/SV3 carry the payload
    ("835", "BPR"),   # 835 payment information — financial, not claim-level
    ("835", "TRN"),   # 835 reassociation trace
    ("835", "PLB"),   # 835 provider adjustment
    ("835", "TS3"),   # 835 provider summary
    ("835", "TS2"),   # 835 provider supplemental summary
    ("835", "CUR"),   # 835 currency
})


def register_handler(
    transaction_set: str,
    segment_name: str,
    handler: HandlerFn | None,
) -> None:
    """Register a handler. None means 'silently ignore this segment'."""
    HANDLER_REGISTRY[(transaction_set, segment_name.upper())] = handler


def register_silent(transaction_set: str, segment_name: str) -> None:
    register_handler(transaction_set, segment_name, None)


def get_handler(transaction_set: str, segment_name: str) -> tuple[HandlerFn | None, bool]:
    """Look up `(handler, found)`.

    Returns `(None, True)` when registered as silent-skip.
    Returns `(None, False)` when no handler exists at all (unhandled).
    """
    seg = segment_name.upper()
    key = (transaction_set, seg)
    if key in HANDLER_REGISTRY:
        return HANDLER_REGISTRY[key], True
    if ("*", seg) in HANDLER_REGISTRY:
        return HANDLER_REGISTRY[("*", seg)], True
    if key in _SILENT_SKIP or ("*", seg) in _SILENT_SKIP:
        return None, True
    return None, False


def dispatch_segment(
    transaction_set: str,
    segment_name: str,
    elements: Sequence[str],
    raw_segment_text: str,
    ctx: "ParseContext",
) -> str:
    """Dispatch one segment. Returns the handler_status string for the
    raw_segments persistence row: 'handled', 'skipped_unhandled', or 'parse_error'.

    Catches *every* exception — a bad handler must not poison the whole parse.
    """
    handler, found = get_handler(transaction_set, segment_name)

    if not found:
        ctx.record_unhandled(segment_name)
        ctx.add_event(
            "segment_skipped",
            segment_name=segment_name,
            details={"reason": "no_handler", "variant": transaction_set},
        )
        return "skipped_unhandled"

    if handler is None:
        # Silent-skip (envelope etc.) — no event noise
        return "handled"

    try:
        handler(elements, raw_segment_text, ctx)
        # NOTE: "segment_handled" events are deliberately NOT emitted here
        # (would be one event per segment — 9× per claim at parse time).
        # raw_segments.handler_status='handled' already records the same info
        # without the per-segment allocation. Performance-critical path:
        # PARSER-STRESS-001 showed this dominated parse time at ~75%.
        return "handled"
    except Exception as exc:  # noqa: BLE001 — explicitly broad
        err_msg = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "Handler %s/%s raised at position %d: %s",
            transaction_set, segment_name, ctx.segment_position, err_msg,
        )
        ctx.add_event(
            "parse_error",
            segment_name=segment_name,
            details={
                "exception": type(exc).__name__,
                "message": str(exc),
                "traceback_short": traceback.format_exc(limit=3),
            },
        )
        ctx.add_error(
            segment=segment_name,
            field="handler",
            message=f"Handler failure: {err_msg}",
            severity="ERROR",
            validator="parser",
        )
        return "parse_error"
