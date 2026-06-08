"""Per-segment handler registry + dispatcher.

Handlers are looked up by `(transaction_set, segment_name)` with a `'*'`
fallback for handlers that apply across variants.
"""

from rcm.parsing.dispatchers.base import (
    HANDLER_REGISTRY,
    HandlerFn,
    dispatch_segment,
    get_handler,
    register_handler,
)

__all__ = [
    "HANDLER_REGISTRY",
    "HandlerFn",
    "dispatch_segment",
    "get_handler",
    "register_handler",
]
