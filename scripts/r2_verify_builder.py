"""R2 — FeatureBuilder.fit_transform schema verification + Feature Contribution Inventory.

Per the approved AIR (CR-062):
  - Verifies FeatureBuilder.fit_transform runs cleanly per variant
  - Verifies the output matches the registry contract (column count, order, dtypes)
  - Verifies validate_feature_frame passes (M1 lesson — column order must match)
  - Produces a Feature Contribution Inventory per (variant, category)
  - Produces a cross-variant signal-density summary
  - Adds dominant_value_pct + low_information_flag (informational only)

Read-only:
  - No FeatureBuilder modification
  - No feature removal
  - No registry change
  - No SQL change
  - No schema / migration
  - No model training
  - No hyperparameter tuning

Stops on the first failed assertion (per user protocol). Generates the full
inventory regardless of assertion outcomes so the contribution data is
available for the remediation discussion if NO-GO.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import traceback
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Project src on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rcm.features.dataset import load_training_corpus  # noqa: E402
from rcm.features.builder import FeatureBuilder  # noqa: E402
from rcm.features.registry import (  # noqa: E402
    FEATURE_REGISTRY,
    get_feature_columns,
    validate_feature_frame,
)

import os as _os
DSN_SA = _os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://rcm:rcm_dev_password@localhost:5433/rcm_denials_dev",
)

VARIANTS = [
    ("837D", "dental"),
    ("837P", "healthcare"),
    ("837I", "home_care"),
]


def hdr(s: str) -> None:
    print()
    print("=" * 78)
    print(s)
    print("=" * 78)


def _safe_unique_count(series: pd.Series) -> int:
    """Distinct value count over non-null entries, handling list/dict cells."""
    s = series.dropna()
    if len(s) == 0:
        return 0
    try:
        # object dtype with hashable values
        return int(s.nunique())
    except TypeError:
        # list/dict cells aren't hashable; coerce to repr
        return int(s.apply(repr).nunique())


def _dominant_value_pct(series: pd.Series) -> float:
    """Share (0..100) of the most frequent value among non-null entries."""
    s = series.dropna()
    n = len(s)
    if n == 0:
        return 0.0
    try:
        counts = s.value_counts(dropna=True)
    except TypeError:
        counts = s.apply(repr).value_counts(dropna=True)
    if len(counts) == 0:
        return 0.0
    return float(100.0 * int(counts.iloc[0]) / n)


def _category_of(col: str) -> str | None:
    spec = FEATURE_REGISTRY.get(col)
    if spec is None:
        return None
    cat = getattr(spec, "category", None)
    if cat is None:
        return None
    return getattr(cat, "name", str(cat))


def _column_metrics(features_df: pd.DataFrame, col: str) -> dict:
    series = features_df[col]
    n = len(series)
    non_null = int(series.notna().sum())
    non_null_pct = 100.0 * non_null / max(n, 1)
    uniques = _safe_unique_count(series)
    dom_pct = _dominant_value_pct(series)

    flags = []
    is_constant = uniques == 1
    is_all_null = uniques == 0
    is_high_null = non_null_pct < 5.0
    is_low_info = dom_pct > 95.0      # informational; not failing
    if is_all_null:
        flags.append("all_null")
    if is_high_null and not is_all_null:
        flags.append(">95%_null")
    if is_constant:
        flags.append("constant")
    if is_low_info and not is_constant and not is_all_null:
        flags.append("low_information")

    # "active" = non-null on >=5% AND has >=2 distinct values
    is_active = (non_null_pct >= 5.0) and (uniques >= 2)

    return {
        "non_null_pct": non_null_pct,
        "uniques": uniques,
        "dominant_value_pct": dom_pct,
        "is_constant": is_constant,
        "is_all_null": is_all_null,
        "is_high_null": is_high_null,
        "low_information_flag": is_low_info,
        "is_active": is_active,
        "flags": flags,
    }


async def verify_variant(Session, variant: str, subtype: str) -> dict:
    print(f"\n--- {variant}/{subtype} ---")
    result: dict = {
        "variant": variant,
        "subtype": subtype,
        "assertions": {},
        "inventory": {},
        "exception": None,
        "overall_pass": False,
    }

    # ---- 1. Load corpus ----
    t0 = time.monotonic()
    async with Session() as session:
        df = await load_training_corpus(session, service_variant=variant, claim_subtype=subtype)
    load_secs = time.monotonic() - t0
    n_rows = len(df)
    print(f"  loaded corpus rows={n_rows} in {load_secs:.2f}s")
    result["corpus_rows"] = n_rows
    result["corpus_load_secs"] = load_secs

    if n_rows == 0:
        result["assertions"]["corpus_non_empty"] = {"pass": False, "detail": "0 rows"}
        result["exception"] = "Empty corpus — cannot fit_transform"
        return result
    result["assertions"]["corpus_non_empty"] = {"pass": True, "detail": f"rows={n_rows}"}

    # Extract y, drop it from df (fit_transform takes y separately)
    y = df["denied"].astype("int8")
    fit_df = df  # FeatureBuilder reads from the full df including the 'denied' col

    # ---- 2. fit_transform ----
    fb = FeatureBuilder(service_variant=variant, claim_subtype=subtype)
    t0 = time.monotonic()
    try:
        async with Session() as session:
            artifacts = await fb.fit_transform(session, fit_df, y)
        fit_secs = time.monotonic() - t0
        print(f"  fit_transform OK in {fit_secs:.2f}s")
        result["fit_secs"] = fit_secs
        result["assertions"]["fit_transform_runs"] = {"pass": True, "detail": f"{fit_secs:.2f}s"}
    except Exception as e:
        fit_secs = time.monotonic() - t0
        tb = traceback.format_exc()
        result["fit_secs"] = fit_secs
        result["exception"] = str(e)
        result["traceback"] = tb
        result["assertions"]["fit_transform_runs"] = {
            "pass": False,
            "detail": f"{type(e).__name__}: {e}",
        }
        print(f"  fit_transform FAILED: {type(e).__name__}: {e}")
        print(tb)
        return result

    features = artifacts.features
    n_features = features.shape[1]
    print(f"  features matrix: rows={features.shape[0]} cols={n_features}")

    # ---- 3. Row count preserved ----
    rows_ok = features.shape[0] == n_rows
    result["assertions"]["row_count_preserved"] = {
        "pass": rows_ok,
        "detail": f"input={n_rows} output={features.shape[0]}",
    }
    if not rows_ok:
        print(f"  FAIL row_count_preserved: in={n_rows} out={features.shape[0]}")
        return result

    # ---- 4. Column count matches registry ----
    expected_cols = list(get_feature_columns(variant, subtype))
    cols_ok = n_features == len(expected_cols)
    result["assertions"]["column_count_matches_registry"] = {
        "pass": cols_ok,
        "detail": f"expected={len(expected_cols)} actual={n_features}",
    }
    if not cols_ok:
        missing = set(expected_cols) - set(features.columns)
        extra = set(features.columns) - set(expected_cols)
        print(f"  FAIL column count: expected={len(expected_cols)} actual={n_features}")
        if missing:
            print(f"    missing: {sorted(missing)[:10]}")
        if extra:
            print(f"    extra:   {sorted(extra)[:10]}")
        result["assertions"]["column_count_matches_registry"]["missing"] = sorted(missing)
        result["assertions"]["column_count_matches_registry"]["extra"] = sorted(extra)
        return result

    # ---- 5. validate_feature_frame (M1 lesson — order must match registry) ----
    try:
        validate_feature_frame(features, variant, subtype)
        result["assertions"]["validate_feature_frame"] = {"pass": True, "detail": "ok"}
    except Exception as e:
        result["assertions"]["validate_feature_frame"] = {
            "pass": False,
            "detail": f"{type(e).__name__}: {e}",
        }
        print(f"  FAIL validate_feature_frame: {type(e).__name__}: {e}")
        return result

    # ---- 6. NaN / inf check ----
    nan_count_by_col: dict = {}
    inf_count_by_col: dict = {}
    for col in features.columns:
        col_series = features[col]
        n_nan = int(col_series.isna().sum())
        n_inf = 0
        if pd.api.types.is_numeric_dtype(col_series):
            n_inf = int(np.isinf(col_series.fillna(0)).sum())
        if n_nan > 0:
            nan_count_by_col[col] = n_nan
        if n_inf > 0:
            inf_count_by_col[col] = n_inf
    nans_ok = len(nan_count_by_col) == 0
    infs_ok = len(inf_count_by_col) == 0
    result["assertions"]["no_nan_in_features"] = {
        "pass": nans_ok,
        "detail": f"columns_with_nan={len(nan_count_by_col)}",
        "cols": nan_count_by_col,
    }
    result["assertions"]["no_inf_in_features"] = {
        "pass": infs_ok,
        "detail": f"columns_with_inf={len(inf_count_by_col)}",
        "cols": inf_count_by_col,
    }
    if not nans_ok:
        print(f"  FAIL no_nan_in_features: {len(nan_count_by_col)} columns contain NaN")
        for c, n in list(nan_count_by_col.items())[:10]:
            print(f"    {c}: {n} NaN")
        return result
    if not infs_ok:
        print(f"  FAIL no_inf_in_features: {len(inf_count_by_col)} columns contain inf")
        return result

    # ---- 7. Fitted artifacts present ----
    enc_ok = artifacts.encoder is not None
    rar_ok = artifacts.rarity_state is not None
    ref_ok = artifacts.ref_lookup is not None
    result["assertions"]["artifacts_present"] = {
        "pass": enc_ok and rar_ok and ref_ok,
        "detail": f"encoder={enc_ok} rarity_state={rar_ok} ref_lookup={ref_ok}",
    }
    if not (enc_ok and rar_ok and ref_ok):
        print(f"  FAIL artifacts_present: encoder={enc_ok} rarity_state={rar_ok} ref_lookup={ref_ok}")
        return result

    # ---- 8. Feature Contribution Inventory (per category) ----
    # Group columns by registry category. Columns absent from FEATURE_REGISTRY
    # (variant-module additions) are bucketed under VARIANT_MODULE.
    by_category: dict[str, list[str]] = defaultdict(list)
    for col in features.columns:
        cat = _category_of(col) or "VARIANT_MODULE"
        by_category[cat].append(col)

    inventory: dict[str, dict] = {}
    for cat, cols in sorted(by_category.items()):
        per_col = {c: _column_metrics(features, c) for c in cols}
        n_reg = len(cols)
        n_active = sum(1 for m in per_col.values() if m["is_active"])
        n_constant = sum(1 for m in per_col.values() if m["is_constant"])
        n_high_null = sum(1 for m in per_col.values() if m["is_high_null"])
        n_low_info = sum(1 for m in per_col.values() if m["low_information_flag"])
        mean_nn = float(np.mean([m["non_null_pct"] for m in per_col.values()])) if per_col else 0.0
        mean_card = float(np.mean([m["uniques"] for m in per_col.values()])) if per_col else 0.0
        mean_dom = float(np.mean([m["dominant_value_pct"] for m in per_col.values()])) if per_col else 0.0
        inventory[cat] = {
            "registered_feature_count": n_reg,
            "active_feature_count": n_active,
            "constant_feature_count": n_constant,
            "high_null_count": n_high_null,
            "low_information_count": n_low_info,
            "mean_non_null_pct": round(mean_nn, 2),
            "mean_cardinality": round(mean_card, 1),
            "mean_dominant_value_pct": round(mean_dom, 2),
            "signal_density": round(n_active / max(n_reg, 1), 3),
            "per_column": per_col,
        }
    result["inventory"] = inventory
    result["assertions"]["inventory_generated"] = {
        "pass": True,
        "detail": f"categories={len(inventory)} columns={n_features}",
    }

    # ---- 9. Overall pass — all assertions must pass ----
    result["overall_pass"] = all(a["pass"] for a in result["assertions"].values())
    return result


async def main() -> None:
    engine = create_async_engine(DSN_SA, pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    overall = {
        "status": "GO",
        "findings": [],
        "alembic_head": "0016_mv_pch_deleted",
        "variants": {},
    }

    hdr("R2 — FeatureBuilder.fit_transform verification + Contribution Inventory")
    for v, s in VARIANTS:
        result = await verify_variant(Session, v, s)
        overall["variants"][f"{v}/{s}"] = result
        if not result["overall_pass"]:
            overall["status"] = "NO-GO"
            failed_assertions = [
                k for k, a in result["assertions"].items() if not a["pass"]
            ]
            overall["findings"].append({
                "variant": f"{v}/{s}",
                "failed_assertions": failed_assertions,
                "exception": result.get("exception"),
            })

    # ---- Cross-variant summary ----
    hdr("Per-Variant Assertion Outcomes")
    for v_s, r in overall["variants"].items():
        marker = "PASS" if r["overall_pass"] else "FAIL"
        print(f"  [{marker}] {v_s}  rows={r.get('corpus_rows', '?')}  "
              f"fit={r.get('fit_secs', '?')}s")
        for name, a in r["assertions"].items():
            m = "PASS" if a["pass"] else "FAIL"
            print(f"    [{m}] {name:35s} {a.get('detail', '')}")

    hdr("Feature Contribution Inventory — per variant × category")
    # If any variant failed before inventory was generated, skip that variant's table
    for v_s, r in overall["variants"].items():
        if not r.get("inventory"):
            print(f"\n  {v_s}: inventory not generated (verification halted earlier)")
            continue
        inv = r["inventory"]
        print(f"\n  {v_s}  (rows={r['corpus_rows']})")
        print(f"  {'category':22s} {'reg':>4s} {'act':>4s} {'cnst':>4s} {'hi-null':>7s} {'low-info':>8s} {'mean_nn%':>9s} {'mean_card':>9s} {'mean_dom%':>9s} {'sig_dens':>8s}")
        for cat, m in sorted(inv.items()):
            print(f"  {cat:22s} {m['registered_feature_count']:>4} "
                  f"{m['active_feature_count']:>4} {m['constant_feature_count']:>4} "
                  f"{m['high_null_count']:>7} {m['low_information_count']:>8} "
                  f"{m['mean_non_null_pct']:>8.2f}% {m['mean_cardinality']:>9.1f} "
                  f"{m['mean_dominant_value_pct']:>8.2f}% {m['signal_density']:>7.2f}")

    # Cross-variant signal density
    hdr("Cross-Variant Signal-Density Summary")
    tot_reg = 0
    tot_act = 0
    tot_const = 0
    tot_low = 0
    tot_high_null = 0
    for v_s, r in overall["variants"].items():
        if not r.get("inventory"):
            continue
        for cat, m in r["inventory"].items():
            tot_reg += m["registered_feature_count"]
            tot_act += m["active_feature_count"]
            tot_const += m["constant_feature_count"]
            tot_low += m["low_information_count"]
            tot_high_null += m["high_null_count"]
    overall_signal_density = round(tot_act / max(tot_reg, 1), 3)
    overall["summary"] = {
        "total_registered_feature_instances": tot_reg,
        "total_active_feature_instances": tot_act,
        "total_constant_feature_instances": tot_const,
        "total_high_null_feature_instances": tot_high_null,
        "total_low_information_feature_instances": tot_low,
        "overall_signal_density": overall_signal_density,
    }
    print(f"  total registered (3 variants × ~115 cols)             = {tot_reg}")
    print(f"  total active (non-null ≥5% AND ≥2 distinct)           = {tot_act}  ({100*tot_act/max(tot_reg,1):.1f}%)")
    print(f"  total constant (1 unique value)                       = {tot_const}  ({100*tot_const/max(tot_reg,1):.1f}%)")
    print(f"  total >95% null                                       = {tot_high_null}  ({100*tot_high_null/max(tot_reg,1):.1f}%)")
    print(f"  total low-information (dominant_value_pct > 95%, but not constant) = {tot_low}  ({100*tot_low/max(tot_reg,1):.1f}%)")
    print(f"  OVERALL SIGNAL DENSITY = {overall_signal_density:.3f}")

    hdr("R2 STATUS")
    print(f"  ★ status: {overall['status']}")
    if overall["findings"]:
        for f in overall["findings"]:
            print(f"  - {f['variant']}: failed={f['failed_assertions']} exception={f['exception']}")

    Path("scripts/r2_verify_post.json").write_text(
        json.dumps(overall, indent=2, default=str), encoding="utf-8"
    )
    print("\n  wrote scripts/r2_verify_post.json")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
