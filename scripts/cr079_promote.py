"""CR-079 — atomic promotion of tuned candidate bundles.

Promotion procedure:
    1. Require gate JSON files exist (baseline, tune summary, shap stability).
    2. Verify each variant's gates pass (per F.1 of the Phase 4 AIR).
    3. Move current production artifacts -> artifacts/featurebuilder_pre_cr079/
    4. Move candidate artifacts -> artifacts/featurebuilder/
    5. Print one-line revert command.

Usage:
    PYTHONPATH=src python scripts/cr079_promote.py            # dry-run report
    PYTHONPATH=src python scripts/cr079_promote.py --apply    # actually move

Safety:
    * --apply refuses if any gate JSON is missing.
    * --apply refuses if SHAP overlap < 50 % for ANY variant unless
      --force-shap-review is also passed (manual override; logged in summary).
    * Always writes scripts/cr079_promotion_report.json so the decision
      record persists.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT          = Path("artifacts/featurebuilder")
CAND_ROOT     = Path("artifacts/featurebuilder_cr079_candidate")
ROLLBACK_ROOT = Path("artifacts/featurebuilder_pre_cr079")

BASELINE_JSON = Path("scripts/cr079_baseline.json")
TUNE_JSON     = Path("scripts/cr079_tune_summary.json")
SHAP_JSON     = Path("scripts/cr079_shap_stability.json")
REPORT_OUT    = Path("scripts/cr079_promotion_report.json")

VARIANTS = ["837P_healthcare", "837D_dental", "837I_home_care"]

# Per-variant minimum held-out improvements (Phase 4 AIR Part F.1).
GATES: dict[str, dict[str, float]] = {
    "837P_healthcare": {
        "min_d_roc_auc":  0.001,
        "min_d_pr_auc":   0.002,
        "min_d_f1":       0.005,
        "min_precision":  0.85,
        "max_brier_pct_worse": 5.0,
    },
    "837D_dental": {
        "min_d_roc_auc":  0.005,
        "min_d_pr_auc":   0.010,
        "min_d_f1":       0.010,
        "min_precision":  0.85,
        "max_brier_pct_worse": 10.0,
    },
    "837I_home_care": {
        "min_d_roc_auc":  0.003,
        "min_d_pr_auc":   0.008,
        "min_d_f1":       0.008,
        "min_precision":  0.85,
        "max_brier_pct_worse": 10.0,
    },
}


def _load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _gate_for_variant(key: str, baseline: dict, tune: dict, shap: dict) -> dict[str, Any]:
    g = GATES[key]
    base_row = (baseline or {}).get("variants", {}).get(key, {})
    tune_row = (tune or {}).get("variants", {}).get(key, {})
    shap_row = (shap or {}).get("variants", {}).get(key, {})

    if base_row.get("status") != "ok":
        return {"key": key, "status": "no_baseline", "passed": False}
    if not tune_row or "candidate_metrics" not in tune_row:
        return {"key": key, "status": "no_tune", "passed": False}

    base_held = (base_row.get("slices") or {}).get("held_out") or {}
    cand_held = tune_row["candidate_metrics"].get("held_out") or {}

    d_auc    = float(cand_held.get("roc_auc", 0)) - float(base_held.get("roc_auc", 0))
    d_prauc  = float(cand_held.get("pr_auc", 0)) - float(base_held.get("pr_auc", 0))
    d_f1     = float(cand_held.get("f1_at_threshold", 0)) - float(base_held.get("f1", 0))
    cand_p   = float(cand_held.get("precision_at_threshold", 0))
    base_brier = float(base_held.get("brier_calibrated", 0))
    cand_brier = float(cand_held.get("brier_calibrated", 0))
    brier_pct_worse = (
        100.0 * (cand_brier - base_brier) / max(base_brier, 1e-9) if base_brier > 0 else 0.0
    )

    checks = {
        "d_roc_auc":   {"value": d_auc,    "threshold": g["min_d_roc_auc"], "passed": d_auc >= g["min_d_roc_auc"]},
        "d_pr_auc":    {"value": d_prauc,  "threshold": g["min_d_pr_auc"],  "passed": d_prauc >= g["min_d_pr_auc"]},
        "d_f1":        {"value": d_f1,     "threshold": g["min_d_f1"],      "passed": d_f1 >= g["min_d_f1"]},
        "precision":   {"value": cand_p,   "threshold": g["min_precision"], "passed": cand_p >= g["min_precision"]},
        "brier_drift": {"value": brier_pct_worse, "threshold": g["max_brier_pct_worse"],
                        "passed": brier_pct_worse <= g["max_brier_pct_worse"]},
    }

    shap_pct  = float(shap_row.get("overlap_pct", 0)) if shap_row else 0.0
    shap_pass = shap_pct >= 50.0
    checks["shap_overlap_pct"] = {
        "value": shap_pct, "threshold": 50.0, "passed": shap_pass,
    }

    all_pass = all(c["passed"] for c in checks.values())
    return {
        "key": key,
        "status": "ok",
        "baseline_held_out": base_held,
        "candidate_held_out": cand_held,
        "checks": checks,
        "passed": all_pass,
    }


def _atomic_promote(variants_to_move: list[str]) -> None:
    """Per-variant promotion: for each variant in ``variants_to_move``, snapshot
    its current production bundle into ``featurebuilder_pre_cr079/`` (preserving
    any prior rollback under a timestamp) and move the candidate into the
    production slot. Variants NOT in the list are left untouched."""
    if not variants_to_move:
        return
    ROLLBACK_ROOT.mkdir(parents=True, exist_ok=True)
    ROOT.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    for key in variants_to_move:
        cand = CAND_ROOT / key
        prod = ROOT / key
        rollback = ROLLBACK_ROOT / key
        if not cand.exists():
            print(f"  skip {key}: no candidate", flush=True)
            continue
        if prod.exists():
            if rollback.exists():
                shutil.move(str(rollback), f"{rollback}.{ts}")
            shutil.move(str(prod), str(rollback))
            print(f"  rolled back {key} -> {rollback}", flush=True)
        if prod.exists():
            shutil.rmtree(prod)
        shutil.move(str(cand), str(prod))
        print(f"  promoted   {key} <- candidate", flush=True)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true",
                   help="Actually move files; without this flag the script is a dry-run.")
    p.add_argument("--force-shap-review", action="store_true",
                   help="Promote even when SHAP overlap is below 50%% (manual override).")
    p.add_argument("--variants", default=None,
                   help="Comma-separated subset to promote (e.g. 837I_home_care). "
                        "Default = every variant that passes its gate.")
    args = p.parse_args()

    baseline = _load(BASELINE_JSON)
    tune     = _load(TUNE_JSON)
    shap     = _load(SHAP_JSON)

    print("CR-079 promotion gate")
    print("=" * 64)
    missing = []
    for label, obj, path in (("baseline", baseline, BASELINE_JSON),
                              ("tune",     tune,     TUNE_JSON),
                              ("shap",     shap,     SHAP_JSON)):
        present = obj is not None
        print(f"  {label:9s} json: {'OK' if present else 'MISSING'} ({path})")
        if not present:
            missing.append(str(path))
    if missing:
        print()
        print("Missing one or more inputs — cannot promote.")
        if args.apply:
            return 2

    rows = [_gate_for_variant(k, baseline or {}, tune or {}, shap or {}) for k in VARIANTS]
    print()
    print("Per-variant gate evaluation:")
    overall_pass = True
    for row in rows:
        if row["status"] != "ok":
            print(f"  {row['key']:24s} status={row['status']}")
            overall_pass = False
            continue
        print(f"  {row['key']:24s} {'PASS' if row['passed'] else 'FAIL'}")
        for name, c in row["checks"].items():
            tag = "ok" if c["passed"] else "!!"
            print(f"    [{tag}] {name:18s} value={c['value']:+.4f} threshold={c['threshold']}")
        if not row["passed"]:
            overall_pass = False

    report = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "applied":     False,
        "force_shap":  bool(args.force_shap_review),
        "variants":    {r["key"]: r for r in rows},
        "overall_passed": overall_pass,
    }

    if not args.apply:
        print()
        print("Dry-run only — no files moved. Re-run with --apply to promote.")
        REPORT_OUT.parent.mkdir(parents=True, exist_ok=True)
        REPORT_OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return 0 if overall_pass else 1

    # --apply
    if args.variants:
        explicit = [v.strip() for v in args.variants.split(",") if v.strip()]
        unknown = [v for v in explicit if v not in VARIANTS]
        if unknown:
            print(f"Unknown variant(s): {unknown}")
            return 2
        # Honour the explicit set, but each variant must still pass its gate
        # (unless --force-shap-review, which only overrides the SHAP check).
        to_promote = []
        for r in rows:
            if r["key"] not in explicit:
                continue
            if r["passed"]:
                to_promote.append(r["key"])
            else:
                shap_only_failed = (
                    not r["checks"]["shap_overlap_pct"]["passed"]
                    and all(c["passed"] for n, c in r["checks"].items() if n != "shap_overlap_pct")
                )
                if args.force_shap_review and shap_only_failed:
                    to_promote.append(r["key"])
                else:
                    print(f"  skip {r['key']}: gate not satisfied")
    else:
        to_promote = [r["key"] for r in rows if r["status"] == "ok" and r["passed"]]

    if not to_promote:
        print()
        print("Nothing to promote — every requested variant failed its gate.")
        report["selected"] = []
        REPORT_OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return 1

    print()
    print(f"Promoting {len(to_promote)} variant(s): {to_promote}")
    _atomic_promote(to_promote)
    report["applied"] = True
    report["selected"] = to_promote
    REPORT_OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print()
    print(f"Done. Rollback dir: {ROLLBACK_ROOT}")
    print("To revert one variant:")
    for k in to_promote:
        print(f"  mv artifacts/featurebuilder_pre_cr079/{k}  artifacts/featurebuilder/{k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
