"""4-tier validator chain.

    Tier 1 — structural (X12 envelope)
    Tier 2 — implementation guide (required segments per variant)
    Tier 3 — payer-specific (data-driven from payer_policies)
    Tier 4 — business rules

Each validator appends `ValidationError` entries to `ctx.parse_errors` with
`severity='ERROR'` to mark the claim as dropped, or `'WARNING'/'INFO'` for
diagnostic-only signals.

`run_all` is the public entry point and is called by `parser.parse_edi`
after the segment-dispatch loop completes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rcm.parsing.validators.tier1_structural import validate_tier1
from rcm.parsing.validators.tier2_ig import validate_tier2
from rcm.parsing.validators.tier3_payer import validate_tier3
from rcm.parsing.validators.tier4_business import validate_tier4

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from rcm.parsing.context import ParseContext


async def run_all(ctx: "ParseContext", session: "AsyncSession | None" = None) -> None:
    """Run all four validator tiers in order.

    Tier 3 needs a DB session to look up payer_policies; if `session` is
    None it's silently skipped (useful for parser-only tests).
    """
    validate_tier1(ctx)
    validate_tier2(ctx)
    if session is not None:
        await validate_tier3(ctx, session)
    validate_tier4(ctx)
    _mark_dropped_from_errors(ctx)


def _mark_dropped_from_errors(ctx: "ParseContext") -> None:
    """Promote ERROR-severity validator entries into `claim.dropped=True`.

    Iterates parse_errors, and for each ERROR with an object_index that
    matches a claim, sets that claim's `dropped=True` and appends the
    reason. Same logic applies to remittances.
    """
    err_by_claim: dict[int, list[str]] = {}
    err_by_remit: dict[int, list[str]] = {}

    for e in ctx.parse_errors:
        if e.severity != "ERROR":
            continue
        msg = f"{e.segment}.{e.field}: {e.message}"
        # Look for an object_index match — claims are indexed 0..N-1, same for remits.
        # We disambiguate by `segment`: CLP-related errors → remit, otherwise → claim.
        if e.segment in {"CLP", "CAS", "LQ", "SVC", "MIA", "MOA"}:
            err_by_remit.setdefault(e.object_index, []).append(msg)
        else:
            err_by_claim.setdefault(e.object_index, []).append(msg)

    for i, reasons in err_by_claim.items():
        if 0 <= i < len(ctx.claims):
            ctx.claims[i].dropped = True
            ctx.claims[i].drop_reasons.extend(reasons)

    for i, reasons in err_by_remit.items():
        if 0 <= i < len(ctx.remittances):
            ctx.remittances[i].dropped = True
            ctx.remittances[i].drop_reasons.extend(reasons)


__all__ = [
    "run_all",
    "validate_tier1",
    "validate_tier2",
    "validate_tier3",
    "validate_tier4",
]
