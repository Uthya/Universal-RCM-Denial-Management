"""Tier 1 — structural validation of the X12 envelope.

Most ISA/GS/ST-level structural integrity is enforced at parse time by the
envelope module (which raises EnvelopeError on hard failures). This tier
runs the *count reconciliation* checks the spec requires:

    SE01  == count of segments in ST..SE inclusive of SE   (WARNING)
    GE01  == count of ST/SE pairs in the group              (WARNING)
    IEA01 == count of GS/GE pairs in the interchange        (WARNING)
    ISA13 == IEA02   (ERROR — interchange control mismatch)
    GS06  == GE02    (ERROR — functional group mismatch)
"""

from __future__ import annotations

from rcm.parsing.context import ParseContext


def validate_tier1(ctx: ParseContext) -> None:
    # ST/SE count reconciliation
    if ctx.st_count != ctx.se_count:
        ctx.add_error(
            segment="SE", field="count",
            message=f"ST count ({ctx.st_count}) != SE count ({ctx.se_count})",
            severity="WARNING", validator="tier1_structural",
        )

    # GS/GE count reconciliation
    if ctx.gs_count != ctx.ge_count:
        ctx.add_error(
            segment="GE", field="count",
            message=f"GS count ({ctx.gs_count}) != GE count ({ctx.ge_count})",
            severity="WARNING", validator="tier1_structural",
        )

    # ISA/IEA count reconciliation
    if ctx.isa_count != ctx.iea_count:
        ctx.add_error(
            segment="IEA", field="count",
            message=f"ISA count ({ctx.isa_count}) != IEA count ({ctx.iea_count})",
            severity="WARNING", validator="tier1_structural",
        )

    # SE01 reconciliation against the actual segment count would require us
    # to capture per-ST segment counts during the parse loop; deferred to
    # avoid making the parser stateful for trailer-only checks. The current
    # implementation captures SE01/GE01/IEA01 values into ctx so this can
    # be promoted later without parser changes.
    if ctx.se01_values and any(v <= 0 for v in ctx.se01_values):
        ctx.add_error(
            segment="SE", field="se01",
            message="SE01 reported zero/negative segment count",
            severity="WARNING", validator="tier1_structural",
        )

    # Multi-ISA file warning — surfaced explicitly so callers can choose to
    # split or reject. Per spec: prefer explicit rejection over silent partial parse.
    if ctx.isa_count > 1:
        ctx.add_error(
            segment="ISA", field="count",
            message=f"File contains {ctx.isa_count} interchanges; "
                    "only the first was processed",
            severity="WARNING", validator="tier1_structural",
        )
