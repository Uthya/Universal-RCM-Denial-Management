"""Category F — documentation (5 features)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from rcm.features.categories._helpers import _has_str
from rcm.features.categories.availability import RefDataLookup


def compute(df: pd.DataFrame, *, ref: RefDataLookup | None = None) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    ref = ref or RefDataLookup()

    has_pwk = df.get("has_paperwork", pd.Series(False, index=df.index)).astype(bool).astype("int8")
    has_cert = df.get("has_certification", pd.Series(False, index=df.index)).astype(bool).astype("int8")
    cpts = df.get("primary_cpt", pd.Series([None] * len(df)))
    var_data = df.get("variant_data", pd.Series([None] * len(df)))

    pwk_required = []
    for c in cpts:
        meta = ref.procedure_metadata.get(str(c)) if _has_str(c) else None
        pwk_required.append(int(bool(meta.get("requires_pwk", False)) if meta else 0))

    out["has_paperwork_attachment"] = has_pwk
    out["paperwork_required_for_cpt"] = pd.Series(pwk_required, index=df.index).astype("int8")
    out["paperwork_missing_when_required"] = (
        (out["paperwork_required_for_cpt"] == 1) & (out["has_paperwork_attachment"] == 0)
    ).astype("int8")
    out["has_certification_segment"] = has_cert

    # has_notes: NTE captured under variant_data["notes"]
    def _has_notes(vd: Any) -> int:
        if not vd:
            return 0
        try:
            return int(bool(vd.get("notes")))
        except AttributeError:
            return 0
    out["has_notes"] = pd.Series([_has_notes(vd) for vd in var_data], index=df.index).astype("int8")
    return out
