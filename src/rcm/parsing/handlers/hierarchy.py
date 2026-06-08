"""HL (hierarchical level) + SBR (subscriber).

837I uses HL extensively (billing → subscriber → patient). 837P uses SBR for
COB sequencing. We track the current HL level in ctx so a subsequent NM1
knows whether it's a subscriber or patient.
"""

from __future__ import annotations

from collections.abc import Sequence

from rcm.parsing.context import ParseContext
from rcm.parsing.dispatchers import register_handler
from rcm.parsing.safe import safe_element


def handle_hl(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    # HL03 = Hierarchical Level Code (20=info source/billing, 22=subscriber, 23=patient)
    ctx.current_hl_level = safe_element(elements, 3)


def handle_sbr(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    # SBR01 = COB sequence (P/S/T); SBR02 = relationship (18=self, etc.);
    # SBR03 = group number; SBR06 = COB indicator.
    ctx.current_subscriber_cob_position = safe_element(elements, 1) or None
    ctx.current_subscriber_relationship = safe_element(elements, 2) or None
    ctx.current_subscriber_group_number = safe_element(elements, 3) or None


for variant in ("837P", "837I", "837D"):
    register_handler(variant, "HL", handle_hl)
    register_handler(variant, "SBR", handle_sbr)
