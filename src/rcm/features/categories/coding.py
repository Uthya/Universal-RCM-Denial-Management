"""Category D — coding integrity (10 features; CR-104 retired has_invalid_modifier_combo)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from rcm.features.categories._helpers import _has_str, _safe_int
from rcm.features.categories.availability import RefDataLookup
from rcm.features.constants import NCCI_OVERRIDE_MODIFIERS


def _is_likely_unbundled(cpts: list[str], modifiers: list[str],
                        ncci_pairs: frozenset[tuple[str, str]]) -> int:
    if not ncci_pairs or len(cpts) < 2:
        return 0
    has_override = any(m.upper() in NCCI_OVERRIDE_MODIFIERS for m in modifiers if _has_str(m))
    if has_override:
        return 0
    for i in range(len(cpts)):
        for j in range(i + 1, len(cpts)):
            a, b = str(cpts[i]), str(cpts[j])
            if (a, b) in ncci_pairs or (b, a) in ncci_pairs:
                return 1
    return 0


def compute(
    df: pd.DataFrame,
    *,
    ref: RefDataLookup | None = None,
    frequency_code_encoded: pd.Series | None = None,
    cpt_frequency_ytd: dict[int, int] | None = None,
) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    ref = ref or RefDataLookup()
    cpt_frequency_ytd = cpt_frequency_ytd or {}

    modifiers_col = df.get("modifiers", pd.Series([[]] * len(df), index=df.index))
    cpts_col = df.get("procedure_codes", pd.Series([[]] * len(df), index=df.index))
    primary_cpts = df.get("primary_cpt", pd.Series([None] * len(df)))
    pos_col = df.get("primary_pos", pd.Series([None] * len(df)))
    freqs = df.get("frequency_code", pd.Series([None] * len(df)))
    claim_ids = df.get("claim_id", pd.Series([None] * len(df)))

    has_mod = []
    mod_count = []
    for mods in modifiers_col:
        ms = mods if isinstance(mods, list) else []
        ms = [m for m in ms if _has_str(m)]
        has_mod.append(int(bool(ms)))
        mod_count.append(len(ms))
    out["has_modifier"] = pd.Series(has_mod, index=df.index).astype("int8")
    out["modifier_count_total"] = pd.Series(mod_count, index=df.index).astype("int8")

    # Required modifier per CPT (from procedure_codes.metadata)
    required = []
    present = []
    for cpt, mods in zip(primary_cpts, modifiers_col):
        meta = ref.procedure_metadata.get(str(cpt)) if _has_str(cpt) else None
        rm = meta.get("requires_modifier") if meta else None
        if not rm:
            required.append(0)
            present.append(1)   # nothing required → vacuously true
            continue
        required.append(1)
        ms = [str(m).upper() for m in (mods if isinstance(mods, list) else []) if _has_str(m)]
        present.append(int(str(rm).upper() in ms))
    out["has_required_modifier_for_cpt"] = pd.Series(required, index=df.index).astype("int8")
    out["required_modifier_present"] = pd.Series(present, index=df.index).astype("int8")

    # NCCI unbundling check
    unbundled = []
    for cpt_list, mods in zip(cpts_col, modifiers_col):
        cl = cpt_list if isinstance(cpt_list, list) else []
        ml = mods if isinstance(mods, list) else []
        unbundled.append(_is_likely_unbundled(cl, ml, ref.ncci_pairs))
    out["is_likely_unbundled"] = pd.Series(unbundled, index=df.index).astype("int8")

    # CPT-POS alignment
    pos_align = []
    for cpt, pos in zip(primary_cpts, pos_col):
        meta = ref.procedure_metadata.get(str(cpt)) if _has_str(cpt) else None
        valid = meta.get("valid_pos_codes") if meta else None
        if not valid or not _has_str(pos):
            pos_align.append(1)
        else:
            pos_align.append(int(str(pos) in valid))
    out["cpt_pos_alignment_score"] = pd.Series(pos_align, index=df.index).astype("int8")

    # Frequency code
    if frequency_code_encoded is not None:
        out["frequency_code_encoded"] = frequency_code_encoded.reindex(df.index, fill_value=0.0).astype("float32")
    else:
        out["frequency_code_encoded"] = pd.Series(0.0, index=df.index, dtype="float32")
    out["is_replacement_claim"] = pd.Series(
        [int(str(f) in ("6", "7")) for f in freqs], index=df.index,
    ).astype("int8")

    # CPT frequency YTD per patient (MV-backed)
    out["cpt_frequency_for_patient_ytd"] = pd.Series(
        [int(cpt_frequency_ytd.get(cid, 0)) if cid is not None else 0 for cid in claim_ids],
        index=df.index,
    ).astype("int16")

    # Exceeds annual limit from procedure_codes.metadata
    exceeds = []
    for cpt, ytd in zip(primary_cpts, out["cpt_frequency_for_patient_ytd"]):
        meta = ref.procedure_metadata.get(str(cpt)) if _has_str(cpt) else None
        limit = meta.get("annual_limit") if meta else None
        if limit is None:
            exceeds.append(0)
        else:
            exceeds.append(int(ytd > _safe_int(limit, 999_999)))
    out["cpt_frequency_exceeds_limit"] = pd.Series(exceeds, index=df.index).astype("int8")
    return out
