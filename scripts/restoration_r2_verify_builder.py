"""R2 — Verify FeatureBuilder.fit_transform produces a stable, valid frame.

For each (variant, subtype) with ≥10 labelled claims, fit a FeatureBuilder
on the corpus and assert the resulting feature matrix:
    * is non-empty
    * has the column count the registry promises for that variant
    * passes validate_feature_frame (M1 schema enforcement)
    * has no NaN columns

Read-only — no artifact saved, no model fit.

Usage:
    PYTHONPATH=src DATABASE_URL=... JWT_SECRET_KEY=... \
        python scripts/restoration_r2_verify_builder.py
"""

from __future__ import annotations

import asyncio
import traceback

import pandas as pd

from rcm.core.database import async_session
from rcm.features.builder import FeatureBuilder
from rcm.features.dataset import load_training_corpus
from rcm.features.registry import FeatureSchemaError, validate_feature_frame


_VARIANTS = [
    ("837P", "healthcare"),
    ("837P", "therapy"),
    ("837P", "transport"),
    ("837P", "specialty"),
    ("837I", "home_care"),
    ("837I", "institutional_other"),
    ("837I", "inpatient"),
    ("837I", "hospice"),
    ("837I", "specialty"),
    ("837D", "dental"),
]


async def run_one(variant: str, subtype: str) -> dict:
    out = {"variant": variant, "subtype": subtype, "ok": False, "note": ""}
    async with async_session() as session:
        df = await load_training_corpus(
            session, service_variant=variant, claim_subtype=subtype,
        )
        out["n_rows"] = len(df)
        out["n_denied"] = int(df["denied"].sum()) if len(df) else 0

        if len(df) < 10:
            out["note"] = f"skip: too few rows ({len(df)} < 10)"
            return out

        y = df["denied"].astype(int).to_numpy()
        if min(int(y.sum()), int((1 - y).sum())) < 2:
            out["note"] = "skip: only one class present"
            return out

        try:
            builder = FeatureBuilder(service_variant=variant, claim_subtype=subtype)
            artifacts = await builder.fit_transform(session, df, pd.Series(y, index=df.index))
            X = artifacts.features
            out["n_features"] = X.shape[1]
            out["nan_cols"] = int(X.isna().any().sum())
        except Exception as exc:
            out["note"] = f"fit_transform: {type(exc).__name__}: {exc}"
            out["traceback"] = traceback.format_exc(limit=4)
            return out

        try:
            validate_feature_frame(X, variant, subtype)
            out["validate"] = "passed"
        except FeatureSchemaError as exc:
            out["validate"] = f"FAILED: {exc}"

        out["ok"] = (out.get("validate") == "passed" and out.get("nan_cols", 0) == 0)
    return out


async def main() -> None:
    print(f'{"variant":<8} {"subtype":<22} {"rows":>6} {"denied":>7} '
          f'{"feats":>6} {"nan":>4} {"validate":<10} {"note"}')
    print('-' * 90)
    for v, s in _VARIANTS:
        r = await run_one(v, s)
        line = (f'{r["variant"]:<8} {r["subtype"]:<22} '
                f'{r.get("n_rows", "-"):>6} {r.get("n_denied", "-"):>7} '
                f'{r.get("n_features", "-"):>6} {r.get("nan_cols", "-"):>4} '
                f'{r.get("validate", "-"):<10} {r.get("note", "")}')
        print(line)
        if r.get("traceback"):
            for ln in r["traceback"].splitlines()[-4:]:
                print(f'    {ln}')


if __name__ == "__main__":
    asyncio.run(main())
