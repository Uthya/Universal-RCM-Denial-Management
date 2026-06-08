"""AMT — claim-level amount qualifier + value.

Common qualifiers:
    F5  patient amount paid
    A8  non-covered charge amount
    AAE approved amount
    T   tax
"""

from __future__ import annotations

from collections.abc import Sequence

from rcm.parsing.context import ClaimAmountRec, ParseContext
from rcm.parsing.dispatchers import register_handler
from rcm.parsing.safe import safe_decimal, safe_element


def handle_amt(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    if ctx.current_claim is None:
        return
    qual = safe_element(elements, 1)
    amount = safe_decimal(safe_element(elements, 2))
    if not qual or amount is None:
        return
    ctx.current_claim.amounts.append(ClaimAmountRec(amount_qualifier=qual, amount=amount))


for variant in ("837P", "837I", "837D"):
    register_handler(variant, "AMT", handle_amt)
