"""Category Z — reference-data availability + completeness.

Emits a boolean per ref table indicating whether the lookup for THIS claim's
primary CPT / payer / payer × CPT / etc. actually found data, plus a
`reference_data_completeness` score (mean of the boolean flags).

These columns are themselves features so the model can learn "predictions
made against incomplete reference data are less reliable" — useful both as
a model signal and as a monitoring axis.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

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

    @classmethod
    async def from_session(cls, session: AsyncSession) -> "RefDataLookup":
        """CR-088: load reference-data tables from the DB into a populated
        ``RefDataLookup``. Each missing/empty table degrades to its empty
        default — every existing fallback in the FE category modules is
        preserved (they all use ``ref.<dict>.get(key, default)``).

        Five tables are queried:
          - procedure_codes  -> procedure_metadata (keyed by code; merged
                                metadata JSONB + category column)
          - diagnosis_codes  -> dx_chapter, dx_severity (severity_score read
                                from metadata JSONB when present)
          - ncci_edits       -> ncci_pairs (active edits only: deletion_date IS NULL)
          - cms_lcd_coverage -> lcd_coverage (denormalised: one entry per CPT)
          - payer_policies   -> payer_policies_by_payer (joined to payers for
                                canonical_name as the key)
        """
        # procedure_codes
        procedure_metadata: dict[str, dict[str, Any]] = {}
        rows = (await session.execute(text(
            "SELECT code, category, metadata FROM procedure_codes"
        ))).mappings().all()
        for r in rows:
            raw_md = r["metadata"]
            if isinstance(raw_md, str):
                try: raw_md = json.loads(raw_md)
                except Exception: raw_md = {}
            md: dict[str, Any] = dict(raw_md or {})
            if r["category"] is not None:
                md.setdefault("category", r["category"])
            procedure_metadata[str(r["code"])] = md

        # diagnosis_codes
        dx_chapter: dict[str, str] = {}
        dx_severity: dict[str, float] = {}
        rows = (await session.execute(text(
            "SELECT code, chapter, metadata FROM diagnosis_codes"
        ))).mappings().all()
        for r in rows:
            code = str(r["code"])
            if r["chapter"]:
                dx_chapter[code] = r["chapter"]
            raw_md = r["metadata"]
            if isinstance(raw_md, str):
                try: raw_md = json.loads(raw_md)
                except Exception: raw_md = {}
            md = raw_md or {}
            sev = md.get("severity_score") if isinstance(md, dict) else None
            if sev is not None:
                try: dx_severity[code] = float(sev)
                except (TypeError, ValueError): pass

        # ncci_edits
        rows = (await session.execute(text(
            "SELECT column1_code, column2_code FROM ncci_edits WHERE deletion_date IS NULL"
        ))).mappings().all()
        ncci_pairs = frozenset(
            (str(r["column1_code"]), str(r["column2_code"])) for r in rows
        )

        # cms_lcd_coverage — denormalise (one entry per CPT in the cpt_codes array)
        lcd_coverage: dict[str, dict[str, Any]] = {}
        rows = (await session.execute(text(
            "SELECT lcd_id, state, contractor, cpt_codes, covered_dx_codes, excluded_dx_codes "
            "FROM cms_lcd_coverage"
        ))).mappings().all()
        for r in rows:
            payload = {
                "lcd_id": r["lcd_id"], "state": r["state"], "contractor": r["contractor"],
                "covered_dx_codes": list(r["covered_dx_codes"] or []),
                "excluded_dx_codes": list(r["excluded_dx_codes"] or []),
            }
            for cpt in (r["cpt_codes"] or []):
                lcd_coverage[str(cpt)] = payload  # last-write-wins by CPT

        # payer_policies — joined to payers for canonical_name keyset
        payer_policies_by_payer: dict[str, list[dict[str, Any]]] = {}
        rows = (await session.execute(text(
            "SELECT py.canonical_name AS payer_name, pp.policy_type::text AS policy_type, "
            "pp.applies_to_codes, pp.structured_rule, pp.service_variant, pp.claim_subtype "
            "FROM payer_policies pp JOIN payers py ON py.id = pp.payer_id"
        ))).mappings().all()
        for r in rows:
            key = r["payer_name"]
            if not key:
                continue
            raw_rule = r["structured_rule"]
            if isinstance(raw_rule, str):
                try: raw_rule = json.loads(raw_rule)
                except Exception: raw_rule = {}
            payer_policies_by_payer.setdefault(str(key), []).append({
                "policy_type": r["policy_type"],
                "applies_to_codes": list(r["applies_to_codes"] or []),
                "structured_rule": raw_rule or {},
                "service_variant": r["service_variant"],
                "claim_subtype": r["claim_subtype"],
            })

        return cls(
            procedure_metadata=procedure_metadata,
            ncci_pairs=ncci_pairs,
            lcd_coverage=lcd_coverage,
            payer_policies_by_payer=payer_policies_by_payer,
            dx_chapter=dx_chapter,
            dx_severity=dx_severity,
        )


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
