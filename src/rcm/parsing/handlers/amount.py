"""AMT — claim-level amount qualifier + value.

Common qualifiers (837):
    F5  patient amount paid
    A8  non-covered charge amount
    AAE approved amount
    T   tax

Common qualifiers (835):
    AU  coverage amount (allowed)
    B6  allowed actual
    D8  discount
    DY  per-day limit
    F3  patient responsibility
    I   interest
    KH  late filing reduction
    NL  net submitted charge
    YU  in-network original amount

835 AMTs attach to the current remittance rather than the current 837 claim
(no current_claim exists on an 835 parse). We park them on the open
RemittanceClaimRec under a parallel `amounts` dict so persistence can pick
them up later.
"""

from __future__ import annotations

from collections.abc import Sequence

from rcm.parsing.context import ClaimAmountRec, ParseContext
from rcm.parsing.dispatchers import register_handler
from rcm.parsing.safe import safe_decimal, safe_element


def handle_amt(elements: Sequence[str], raw: str, ctx: ParseContext) -> None:
    qual = safe_element(elements, 1)
    amount = safe_decimal(safe_element(elements, 2))
    if not qual or amount is None:
        return

    # 837 path — accumulate ClaimAmountRecs on the open claim
    if ctx.current_claim is not None:
        ctx.current_claim.amounts.append(ClaimAmountRec(
            amount_qualifier=qual, amount=amount,
        ))
        return

    # 835 path — stash on the open remittance so it isn't silently dropped.
    # No schema column to land on directly; we'll surface via parse_event so
    # it's queryable and so unhandled-segment counts don't keep climbing.
    if ctx.current_remittance is not None:
        ctx.add_event("segment_handled", segment_name="AMT",
                      details={"qualifier": qual, "amount": str(amount),
                               "context": "remittance"})


for variant in ("837P", "837I", "837D", "835"):
    register_handler(variant, "AMT", handle_amt)
