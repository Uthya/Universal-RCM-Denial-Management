"""R4 — Behavioral parity verification for the variant-agnostic refactor (CR-064).

Acceptance gate is BEHAVIORAL parity, not byte-equality. Per the approved AIR:

  1. train_variant runs without exception
  2. feature_columns identical (R4 fresh vs R3 baseline)
  3. service_variant identical
  4. claim_subtype identical
  5. decision_threshold identical
  6. metrics keys identical
  7. ModelArtifactBundle.load() succeeds
  8. Post-load metadata + feature schema + threshold + metrics structure consistent
  9. predict_proba on a fixed sample identical across R3 vs R4 reloaded artifacts

Byte-for-byte equality is also reported per file but is INFORMATIONAL only.

scripts/r4_parity_report.json is transient — CR-064 reporting only.
"""
from __future__ import annotations

import asyncio
import filecmp
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rcm.features.builder import FeatureBuilder  # noqa: E402
from rcm.features.dataset import load_training_corpus  # noqa: E402
from rcm.ml.artifacts import ModelArtifactBundle  # noqa: E402
from rcm.ml.trainer import train_variant  # noqa: E402  ← the new canonical entrypoint

DSN_SA = "postgresql+asyncpg://postgres:P0stgreSQL%21Dev%23847@104.130.220.20:30432/rcm_denials"

R3_BASELINE = Path("artifacts/featurebuilder")
R4_TMP = Path("artifacts/r4_tmp")

VARIANTS = [
    ("837D", "dental"),
    ("837P", "healthcare"),
    ("837I", "home_care"),
]

SAMPLE_SIZE = 8


def hdr(s: str) -> None:
    print()
    print("=" * 78)
    print(s)
    print("=" * 78)


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def _byte_compare_dirs(d1: Path, d2: Path) -> dict:
    """Informational only — file-by-file byte-equality."""
    if not d1.exists() or not d2.exists():
        return {"error": f"missing dir d1.exists={d1.exists()} d2.exists={d2.exists()}"}
    files1 = sorted([p.relative_to(d1).as_posix() for p in d1.rglob("*") if p.is_file()])
    files2 = sorted([p.relative_to(d2).as_posix() for p in d2.rglob("*") if p.is_file()])
    if files1 != files2:
        return {"error": "file lists differ", "d1_only": list(set(files1)-set(files2)),
                "d2_only": list(set(files2)-set(files1))}
    per_file: dict = {}
    all_eq = True
    for rel in files1:
        p1, p2 = d1 / rel, d2 / rel
        eq = filecmp.cmp(p1, p2, shallow=False)
        per_file[rel] = {
            "bytes_equal": eq,
            "size_d1": p1.stat().st_size,
            "size_d2": p2.stat().st_size,
            "sha256_d1": _sha256_file(p1)[:16],
            "sha256_d2": _sha256_file(p2)[:16],
        }
        if not eq:
            all_eq = False
    return {"all_bytes_equal": all_eq, "per_file": per_file}


async def parity_check_variant(
    Session, variant: str, subtype: str,
) -> dict:
    print(f"\n--- {variant}/{subtype} ---")
    result: dict = {
        "variant": variant,
        "subtype": subtype,
        "assertions": {},
        "byte_equality_report": {},
        "value_deltas": {},
        "exception": None,
        "overall_pass": False,
    }

    r3_dir = R3_BASELINE / f"{variant}_{subtype}"
    r4_dir = R4_TMP / f"{variant}_{subtype}"

    if not r3_dir.is_dir():
        result["exception"] = f"R3 baseline not present at {r3_dir}"
        result["assertions"]["0_r3_baseline_present"] = {"pass": False, "detail": str(r3_dir)}
        return result
    result["assertions"]["0_r3_baseline_present"] = {"pass": True, "detail": str(r3_dir)}

    # ---- 1. Train via the new canonical entrypoint ----
    if r4_dir.exists():
        shutil.rmtree(r4_dir)
    t0 = time.monotonic()
    try:
        async with Session() as session:
            await train_variant(
                session, r4_dir,
                service_variant=variant, claim_subtype=subtype,
            )
        result["assertions"]["1_train_variant_runs"] = {
            "pass": True, "detail": f"{time.monotonic() - t0:.2f}s",
        }
    except Exception as e:
        import traceback
        result["exception"] = str(e)
        result["traceback"] = traceback.format_exc()
        result["assertions"]["1_train_variant_runs"] = {
            "pass": False, "detail": f"{type(e).__name__}: {e}",
        }
        print(f"  FAIL #1 train_variant: {e}")
        return result
    print(f"  train_variant OK ({time.monotonic() - t0:.1f}s)")

    # ---- 7. Reload both artifacts ----
    try:
        r3_bundle = ModelArtifactBundle.load(r3_dir)
        r4_bundle = ModelArtifactBundle.load(r4_dir)
    except Exception as e:
        import traceback
        result["assertions"]["7_artifact_reload"] = {
            "pass": False, "detail": f"{type(e).__name__}: {e}",
        }
        result["exception"] = str(e)
        result["traceback"] = traceback.format_exc()
        print(f"  FAIL #7 reload: {e}")
        return result
    result["assertions"]["7_artifact_reload"] = {
        "pass": True, "detail": "both bundles loaded",
    }
    print(f"  reload OK — R3 + R4 bundles loaded")

    # ---- 2. feature_columns identical ----
    cols_eq = list(r3_bundle.feature_columns) == list(r4_bundle.feature_columns)
    result["assertions"]["2_feature_columns_identical"] = {
        "pass": cols_eq,
        "detail": f"r3_n={len(r3_bundle.feature_columns)} r4_n={len(r4_bundle.feature_columns)}",
    }
    if not cols_eq:
        only_r3 = set(r3_bundle.feature_columns) - set(r4_bundle.feature_columns)
        only_r4 = set(r4_bundle.feature_columns) - set(r3_bundle.feature_columns)
        result["assertions"]["2_feature_columns_identical"]["only_r3"] = sorted(only_r3)
        result["assertions"]["2_feature_columns_identical"]["only_r4"] = sorted(only_r4)
        print(f"  FAIL #2 feature_columns: r3-only={only_r3} r4-only={only_r4}")
        return result

    # ---- 3. service_variant identical ----
    sv_eq = r3_bundle.service_variant == r4_bundle.service_variant
    result["assertions"]["3_service_variant_identical"] = {
        "pass": sv_eq,
        "detail": f"r3={r3_bundle.service_variant!r} r4={r4_bundle.service_variant!r}",
    }
    if not sv_eq:
        return result

    # ---- 4. claim_subtype identical ----
    cs_eq = r3_bundle.claim_subtype == r4_bundle.claim_subtype
    result["assertions"]["4_claim_subtype_identical"] = {
        "pass": cs_eq,
        "detail": f"r3={r3_bundle.claim_subtype!r} r4={r4_bundle.claim_subtype!r}",
    }
    if not cs_eq:
        return result

    # ---- 5. decision_threshold identical ----
    thr_eq = float(r3_bundle.decision_threshold) == float(r4_bundle.decision_threshold)
    result["assertions"]["5_decision_threshold_identical"] = {
        "pass": thr_eq,
        "detail": f"r3={r3_bundle.decision_threshold} r4={r4_bundle.decision_threshold}",
    }
    if not thr_eq:
        return result

    # ---- 6. metrics keys identical (informational: also capture value deltas) ----
    keys_eq = set(r3_bundle.metrics.keys()) == set(r4_bundle.metrics.keys())
    result["assertions"]["6_metrics_keys_identical"] = {
        "pass": keys_eq,
        "detail": f"r3_keys={sorted(r3_bundle.metrics.keys())}",
    }
    if not keys_eq:
        result["assertions"]["6_metrics_keys_identical"]["only_r3"] = sorted(
            set(r3_bundle.metrics.keys()) - set(r4_bundle.metrics.keys())
        )
        result["assertions"]["6_metrics_keys_identical"]["only_r4"] = sorted(
            set(r4_bundle.metrics.keys()) - set(r3_bundle.metrics.keys())
        )
        return result
    # informational: value deltas
    value_deltas: dict = {}
    for k in r3_bundle.metrics.keys():
        v3, v4 = r3_bundle.metrics.get(k), r4_bundle.metrics.get(k)
        if v3 is None or v4 is None:
            value_deltas[k] = {"r3": v3, "r4": v4, "delta": None}
        else:
            try:
                value_deltas[k] = {
                    "r3": float(v3), "r4": float(v4),
                    "delta": float(v4) - float(v3),
                    "equal_within_1e-9": abs(float(v4) - float(v3)) < 1e-9,
                }
            except Exception:
                value_deltas[k] = {"r3": v3, "r4": v4, "delta": "n/a"}
    result["value_deltas"]["metrics"] = value_deltas

    # ---- 8. Post-load metadata + feature schema + threshold + metrics structure ----
    schema_consistent = (
        r3_bundle.feature_engineering_version == r4_bundle.feature_engineering_version
        and r3_bundle.model_version == r4_bundle.model_version
        and r3_bundle.calibrator_version == r4_bundle.calibrator_version
        and r3_bundle.training_size == r4_bundle.training_size
        and abs(float(r3_bundle.training_prevalence) - float(r4_bundle.training_prevalence)) < 1e-9
        and float(r3_bundle.decision_threshold) == float(r4_bundle.decision_threshold)
    )
    result["assertions"]["8_post_load_metadata_consistent"] = {
        "pass": schema_consistent,
        "detail": (
            f"fe_ver: r3={r3_bundle.feature_engineering_version} r4={r4_bundle.feature_engineering_version}; "
            f"model_ver: r3={r3_bundle.model_version} r4={r4_bundle.model_version}; "
            f"cal_ver: r3={r3_bundle.calibrator_version} r4={r4_bundle.calibrator_version}; "
            f"train_size: r3={r3_bundle.training_size} r4={r4_bundle.training_size}; "
            f"prevalence: r3={r3_bundle.training_prevalence:.4f} r4={r4_bundle.training_prevalence:.4f}"
        ),
    }
    if not schema_consistent:
        return result

    # ---- 9. predict_proba identical on a fixed sample ----
    # Build a fresh feature matrix; deterministic from the same DB state.
    async with Session() as session:
        df = await load_training_corpus(
            session, service_variant=variant, claim_subtype=subtype, limit=100,
        )
        if len(df) < SAMPLE_SIZE:
            result["assertions"]["9_predict_proba_identical"] = {
                "pass": False, "detail": f"corpus too small for sample: {len(df)} rows",
            }
            return result
        y = df["denied"].astype(int).to_numpy()
        sb = FeatureBuilder(service_variant=variant, claim_subtype=subtype)
        sa = await sb.fit_transform(session, df, pd.Series(y, index=df.index))
    sample_X = sa.features.astype("float32").iloc[:SAMPLE_SIZE].to_numpy()

    dmat_r3 = xgb.DMatrix(sample_X, feature_names=r3_bundle.feature_columns)
    dmat_r4 = xgb.DMatrix(sample_X, feature_names=r4_bundle.feature_columns)
    r3_raw = r3_bundle.booster.predict(dmat_r3)
    r4_raw = r4_bundle.booster.predict(dmat_r4)
    if r3_bundle.calibrator is not None:
        r3_cal = r3_bundle.calibrator.transform(r3_raw)
    else:
        r3_cal = r3_raw
    if r4_bundle.calibrator is not None:
        r4_cal = r4_bundle.calibrator.transform(r4_raw)
    else:
        r4_cal = r4_raw

    raw_eq = bool(np.allclose(r3_raw, r4_raw, atol=1e-9, rtol=0))
    cal_eq = bool(np.allclose(r3_cal, r4_cal, atol=1e-9, rtol=0))
    pred_eq = raw_eq and cal_eq

    result["assertions"]["9_predict_proba_identical"] = {
        "pass": pred_eq,
        "detail": (
            f"raw_max_abs_diff={float(np.max(np.abs(r3_raw - r4_raw))):.3e} "
            f"cal_max_abs_diff={float(np.max(np.abs(r3_cal - r4_cal))):.3e}"
        ),
        "sample_size": SAMPLE_SIZE,
        "r3_raw_first3": [float(x) for x in r3_raw[:3]],
        "r4_raw_first3": [float(x) for x in r4_raw[:3]],
        "r3_cal_first3": [float(x) for x in r3_cal[:3]],
        "r4_cal_first3": [float(x) for x in r4_cal[:3]],
    }

    # ---- INFORMATIONAL — byte-equality (not gating) ----
    result["byte_equality_report"] = _byte_compare_dirs(r3_dir, r4_dir)

    result["overall_pass"] = all(a["pass"] for a in result["assertions"].values())
    print(f"  predict_proba parity: raw_max_diff={result['assertions']['9_predict_proba_identical']['detail']}")
    return result


async def main() -> None:
    R4_TMP.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(DSN_SA, pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    overall = {
        "status": "GO",
        "findings": [],
        "variants": {},
    }

    hdr("R4 — Behavioral parity verification (CR-064)")
    for variant, subtype in VARIANTS:
        r = await parity_check_variant(Session, variant, subtype)
        overall["variants"][f"{variant}/{subtype}"] = r
        if not r["overall_pass"]:
            overall["status"] = "NO-GO"
            failed = [k for k, a in r["assertions"].items() if not a["pass"]]
            overall["findings"].append({
                "variant": f"{variant}/{subtype}",
                "failed_assertions": failed,
                "exception": r.get("exception"),
            })

    hdr("Per-Variant Assertion Outcomes")
    for v_s, r in overall["variants"].items():
        marker = "PASS" if r["overall_pass"] else "FAIL"
        print(f"\n  [{marker}] {v_s}")
        for name, a in r["assertions"].items():
            m = "PASS" if a["pass"] else "FAIL"
            print(f"    [{m}] {name:38s} {a.get('detail', '')}")

    hdr("Behavioral Parity Table")
    print(f"  {'variant':22s} {'fcols':>5s} {'sv':>5s} {'cs':>5s} {'thresh':>6s} {'mkeys':>5s} {'reload':>6s} {'meta':>5s} {'pred':>5s}")
    for v_s, r in overall["variants"].items():
        a = r["assertions"]
        cells = [
            "✓" if a.get("2_feature_columns_identical", {}).get("pass") else "✗",
            "✓" if a.get("3_service_variant_identical", {}).get("pass") else "✗",
            "✓" if a.get("4_claim_subtype_identical", {}).get("pass") else "✗",
            "✓" if a.get("5_decision_threshold_identical", {}).get("pass") else "✗",
            "✓" if a.get("6_metrics_keys_identical", {}).get("pass") else "✗",
            "✓" if a.get("7_artifact_reload", {}).get("pass") else "✗",
            "✓" if a.get("8_post_load_metadata_consistent", {}).get("pass") else "✗",
            "✓" if a.get("9_predict_proba_identical", {}).get("pass") else "✗",
        ]
        print(f"  {v_s:22s}  {cells[0]:>3s}  {cells[1]:>3s}  {cells[2]:>3s}  "
              f"{cells[3]:>3s}  {cells[4]:>3s}  {cells[5]:>3s}  {cells[6]:>3s}  {cells[7]:>3s}")

    hdr("Byte-Equality Report (INFORMATIONAL — not gating)")
    for v_s, r in overall["variants"].items():
        be = r.get("byte_equality_report", {})
        if "error" in be:
            print(f"\n  {v_s}: {be['error']}")
            continue
        all_eq = be.get("all_bytes_equal")
        print(f"\n  {v_s}: all_bytes_equal={all_eq}")
        for fname, info in be.get("per_file", {}).items():
            eq = info["bytes_equal"]
            marker = "==" if eq else "!="
            print(f"    {marker} {fname:30s} r3:{info['sha256_d1']} r4:{info['sha256_d2']} ({info['size_d1']} vs {info['size_d2']} bytes)")

    hdr("Metric Value Deltas (INFORMATIONAL)")
    for v_s, r in overall["variants"].items():
        vd = r.get("value_deltas", {}).get("metrics", {})
        if not vd:
            continue
        print(f"\n  {v_s}:")
        for k, info in vd.items():
            if "equal_within_1e-9" in info:
                marker = "==" if info["equal_within_1e-9"] else "≠ "
                print(f"    {marker} {k:30s} r3={info['r3']} r4={info['r4']} Δ={info['delta']:+.3e}")
            else:
                print(f"    -- {k:30s} r3={info['r3']} r4={info['r4']}")

    hdr("R4 STATUS")
    print(f"  ★ status: {overall['status']}")
    if overall["findings"]:
        for f in overall["findings"]:
            print(f"  - {f['variant']}: failed={f['failed_assertions']}  exception={f['exception']}")

    Path("scripts/r4_parity_report.json").write_text(
        json.dumps(overall, indent=2, default=str), encoding="utf-8",
    )
    print(f"\n  wrote scripts/r4_parity_report.json (transient — CR-064 reporting only)")

    # Clean up the temporary R4 artifact directory
    if R4_TMP.exists():
        shutil.rmtree(R4_TMP)
        print(f"  cleaned up {R4_TMP}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
