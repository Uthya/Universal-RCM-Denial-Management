"""Per-category feature computers.

Each module exposes a `compute(df, *, …) -> pd.DataFrame` returning a frame
indexed identically to the input (claim_id), with one column per spec'd
feature. The builder calls each in canonical order and concatenates.

Categories with external data dependencies (Cat G history, Cat H provider,
Cat I joint encoders) take an optional ``mv_lookup`` argument carrying
the materialized-view extracts; in offline/test paths the lookup can be
None and features fall back to safe defaults.

Categories with reference-data dependencies (Cat B, C, D, E, F partially,
Z always) take an optional ``ref_data`` argument carrying a snapshot of
procedure_codes.metadata, ncci_edits, etc. Missing entries → safe default.
"""

from rcm.features.categories import (
    authorization,
    availability,
    base,
    clinical,
    coding,
    coverage,
    documentation,
    encoded,
    history,
    joint,
    provider,
    rarity,
    timely,
)

__all__ = [
    "authorization",
    "availability",
    "base",
    "clinical",
    "coding",
    "coverage",
    "documentation",
    "encoded",
    "history",
    "joint",
    "provider",
    "rarity",
    "timely",
]
