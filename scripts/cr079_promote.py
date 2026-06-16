"""CR-079 — atomic promotion of tuned candidate bundles.

Promotion procedure:
    1. Require gate JSON files exist (baseline, tune summary, shap stability).
    2. Verify each variant's gates pass (per F.1 of the Phase 4 AIR).
    3. Move current production artifacts -> artifacts/featurebuilder_pre_cr079/
    4. Move candidate artifacts -> artifacts/featurebuilder/
    5. CR-081 Issue A — POST /api/predictions/reload-bundles so the running
       uvicorn drops its in-process predictor cache and picks up the new
       on-disk bundles without a restart. Failure is non-fatal (warn only)
       so an unreachable API doesn't strand the file move.
    6. CR-081 Issue C — print the rollback inventory: current production
       bundle + every available rollback candidate under
       ``artifacts/featurebuilder_pre_cr079/`` (with their model_versions
       and timestamps).

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
import os
import shutil
import sys
import urllib.error
import urllib.request
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


# ---------------------------------------------------------------------------
# CR-081 Issue C — rollback inventory
# ---------------------------------------------------------------------------

def _read_schema(artifact_dir: Path) -> dict[str, Any]:
    schema_path = artifact_dir / "feature_schema.json"
    if not schema_path.is_file():
        return {}
    try:
        return json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _describe_bundle(artifact_dir: Path) -> dict[str, Any] | None:
    """Read the schema sidecar of a bundle dir and emit a compact summary.
    Returns None if the dir doesn't look like a bundle (no schema file)."""
    schema = _read_schema(artifact_dir)
    if not schema:
        return None
    held = ((schema.get("metrics") or {}).get("held_out") or {})
    return {
        "dir":              str(artifact_dir),
        "model_version":    schema.get("model_version"),
        "decision_threshold": (
            float(schema["decision_threshold"])
            if schema.get("decision_threshold") is not None else None
        ),
        "feature_engineering_version": schema.get("feature_engineering_version"),
        "calibrator_version": schema.get("calibrator_version"),
        "held_out_f1":      held.get("f1_at_threshold"),
        "held_out_auc":     held.get("roc_auc"),
    }


def _collect_rollback_inventory() -> dict[str, list[dict]]:
    """Walk ROLLBACK_ROOT for each known variant and assemble a list of
    available rollback targets — both the canonical ``<key>/`` slot and any
    timestamp-suffixed siblings produced by previous promotions.

    Returns ``{variant_key: [bundle_summary, ...]}`` ordered newest-first
    (the canonical slot always sits at index 0 when present)."""
    inventory: dict[str, list[dict]] = {}
    if not ROLLBACK_ROOT.is_dir():
        return inventory
    for key in VARIANTS:
        candidates: list[dict] = []
        canonical = ROLLBACK_ROOT / key
        bundle = _describe_bundle(canonical)
        if bundle:
            bundle["slot"] = "canonical"
            bundle["timestamp_suffix"] = None
            candidates.append(bundle)
        # Pick up <key>.<TS> siblings (created when a prior rollback was
        # rotated out by a subsequent promotion).
        for sib in sorted(ROLLBACK_ROOT.glob(f"{key}.*"), reverse=True):
            if not sib.is_dir():
                continue
            b = _describe_bundle(sib)
            if not b:
                continue
            b["slot"] = "rotated"
            b["timestamp_suffix"] = sib.name[len(key) + 1:]  # drop "<key>."
            candidates.append(b)
        if candidates:
            inventory[key] = candidates
    return inventory


def _print_inventory(header: str, inventory: dict[str, list[dict]]) -> None:
    print()
    print(header)
    if not inventory:
        print("  (no rollback bundles on disk)")
        return
    for key, bundles in inventory.items():
        print(f"  {key}:")
        for i, b in enumerate(bundles):
            tag = "ROLLBACK TARGET (next revert)" if i == 0 else "older rollback"
            ts = f" [ts={b['timestamp_suffix']}]" if b.get("timestamp_suffix") else ""
            mv = b.get("model_version") or "<unknown>"
            thr = b.get("decision_threshold")
            thr_s = f"thr={thr:.3f}" if thr is not None else "thr=?"
            f1 = b.get("held_out_f1")
            f1_s = f"F1={f1:.3f}" if f1 is not None else "F1=?"
            print(f"    [{tag}] {mv}  {thr_s}  {f1_s}{ts}")


def _print_current_production() -> None:
    print()
    print("Current production:")
    for key in VARIANTS:
        b = _describe_bundle(ROOT / key)
        if b is None:
            print(f"  {key}: (no bundle on disk)")
            continue
        mv = b.get("model_version") or "<unknown>"
        thr = b.get("decision_threshold")
        thr_s = f"thr={thr:.3f}" if thr is not None else "thr=?"
        print(f"  {key}: {mv}  {thr_s}")


# ---------------------------------------------------------------------------
# CR-081 Issue A — invalidate the running backend's predictor cache
# ---------------------------------------------------------------------------

def _reload_running_backend() -> dict[str, Any]:
    """POST to /api/predictions/reload-bundles so a live uvicorn drops its
    in-process predictor cache after the file move. Failure is non-fatal —
    the file move on disk is already correct; the operator will eventually
    restart or hit the endpoint manually."""
    base = os.environ.get("RCM_API_URL", "http://localhost:8000").rstrip("/")
    url = f"{base}/api/predictions/reload-bundles"
    req = urllib.request.Request(url, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        print()
        print(f"Reload-bundles call to {url}:")
        print(f"  status:             {payload.get('status')}")
        print(f"  cleared predictors: {payload.get('cleared_predictors')}")
        print(f"  available bundles:  {len(payload.get('available_bundles') or [])}")
        for b in (payload.get("available_bundles") or []):
            print(f"    {b['service_variant']}/{b['claim_subtype']:<22s} "
                  f"-> {b.get('model_version')}  thr={b.get('decision_threshold')}")
        return payload
    except urllib.error.URLError as exc:
        print()
        print(f"Reload-bundles call FAILED ({exc}). The on-disk swap is "
              "complete, but the running backend (if any) is still using "
              "cached predictors. Restart uvicorn or hit the endpoint "
              "manually:")
        print(f"  curl -X POST {url}")
        return {"status": "unreachable", "error": str(exc)}


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

    # CR-081 Issue C — surface what production looks like RIGHT NOW and what
    # rollback candidates exist before we touch any files.
    _print_current_production()
    _print_inventory(
        "Available rollback candidates (before promotion):",
        _collect_rollback_inventory(),
    )

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

    # CR-081 Issue A — invalidate running backend's predictor cache.
    reload_payload = _reload_running_backend()
    report["reload_bundles"] = reload_payload

    # CR-081 Issue C — print the final state so the operator can see where
    # they can roll back to.
    _print_current_production()
    _print_inventory(
        "Available rollback candidates (after promotion):",
        _collect_rollback_inventory(),
    )

    REPORT_OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print()
    print(f"Done. Rollback dir: {ROLLBACK_ROOT}")
    print("To revert one variant (canonical slot):")
    for k in to_promote:
        print(f"  mv artifacts/featurebuilder_pre_cr079/{k}  artifacts/featurebuilder/{k}")
    print("(Older rollback bundles, if any, live under "
          "artifacts/featurebuilder_pre_cr079/<key>.<timestamp>/. "
          "See the inventory above.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
