"""NTE — note / special instruction.

NTE01 codes most often seen:
    ADD  additional info
    CER  certification narrative
    DGN  diagnosis description
"""

from __future__ import annotations

from collections.abc import Sequence

from rcm.parsing.context import ParseContext
from rcm.parsing.dispatchers import register_handler
from rcm.parsing.safe import safe_element


def handle_nte(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    if ctx.current_claim is None:
        return
    ref = safe_element(elements, 1)
    text = safe_element(elements, 2)
    if not text:
        return
    ctx.current_claim.variant_data.setdefault("notes", []).append({
        "ref": ref,
        "text": text,
    })


for variant in ("837P", "837I", "837D"):
    register_handler(variant, "NTE", handle_nte)
