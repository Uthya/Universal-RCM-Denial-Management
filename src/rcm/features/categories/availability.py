"""Category Z — reference-data availability + completeness.

Emits a boolean per ref table indicating whether the lookup for THIS claim's
primary CPT / payer / payer × CPT / etc. actually found data, plus a
`reference_data_completeness` score (mean of the boolean flags).

These columns are themselves features so the model can learn "predictions
made against incomplete reference data are less reliable" — useful both as
a model signal and as a monitoring axis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from rcm.features.categories._helpers import _has_str


@dataclass
class RefDataLookup:
    """Snapshot of reference tables fetched at training/predict time.

    Each dict is keyed by the primary lookup key:
        procedure_metadata[cpt]            -> dict with annual_limit / requires_pwk / ...
        ncci_pairs                         -> frozenset[tuple[c1, c2]]
        lcd_coverage[cpt]                  -> dict with covered_dx_codes / state / ...
        payer_policies_by_payer[payer]     -> list of dicts (policy_type, applies_to_codes, structured_rule, ...)
        dx_chapter[dx_code]                -> str (ICD chapter)
        dx_severity[dx_code]               -> float
    """
    procedure_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    ncci_pairs: frozenset[tuple[str, str]] = field(default_factory=frozenset)
    lcd_coverage: dict[str, dict[str, Any]] = field(default_factory=dict)
    payer_policies_by_payer: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    dx_chapter: dict[str, str] = field(default_factory=dict)
    dx_severity: dict[str, float] = field(default_factory=dict)
    # When all maps are empty we know the entire reference layer hasn't been loaded.
    @property
    def is_empty(self) -> bool:
        return not any([
            self.procedure_metadata, self.ncci_pairs, self.lcd_coverage,
            self.payer_policies_by_payer, self.dx_chapter, self.dx_severity,
        ])


_EMPTY_LOOKUP = RefDataLookup()


def compute(df: pd.DataFrame, *, ref: RefDataLookup | None = None) -> pd.DataFrame:
    ref = ref or _EMPTY_LOOKUP
    out = pd.DataFrame(index=df.index)

    cpts = df.get("primary_cpt", pd.Series([None] * len(df)))
    payers = df.get("payer_canonical_name", pd.Series([None] * len(df)))

    out["avail_procedure_codes_metadata"] = [
        int(_has_str(c) and c in ref.procedure_metadata) for c in cpts
    ]
    out["avail_payer_policies"] = [
        int(_has_str(p) and p in ref.payer_policies_by_payer) for p in payers
    ]
    # NCCI / LCD are file-wide availability (does the table have data at all?).
    out["avail_ncci_edits"] = [int(bool(ref.ncci_pairs))] * len(df)
    out["avail_lcd_coverage"] = [int(bool(ref.lcd_coverage))] * len(df)

    out["reference_data_completeness"] = (
        (out["avail_procedure_codes_metadata"]
         + out["avail_payer_policies"]
         + out["avail_ncci_edits"]
         + out["avail_lcd_coverage"]) / 4.0
    ).astype("float32")

    for c in ("avail_procedure_codes_metadata", "avail_payer_policies",
              "avail_ncci_edits", "avail_lcd_coverage"):
        out[c] = out[c].astype("int8")
    return out
