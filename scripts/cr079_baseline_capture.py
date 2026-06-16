"""CR-079 — baseline metric snapshot.

Reads each production FB bundle's ``feature_schema.json`` and writes a
structured baseline JSON so the tuner has an unambiguous reference to beat.
No DB / no model fit / no /train call — we capture exactly what production
is currently serving.

Usage:
    PYTHONPATH=src python scripts/cr079_baseline_capture.py
    # writes scripts/cr079_baseline.json

The output schema mirrors the trainer's `_block()` shape so post-tuning
comparison is a straight dict-diff per variant per slice.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

VARIANTS: list[tuple[str, str, str]] = [
    ("837P", "healthcare", "837P_healthcare"),
    ("837D", "dental",     "837D_dental"),
    ("837I", "home_care",  "837I_home_care"),
]

ARTIFACT_ROOT = Path("artifacts/featurebuilder")
BASELINE_OUT  = Path("scripts/cr079_baseline.json")


def _slice_to_metric_row(block: dict[str, Any] | None) -> dict[str, Any] | None:
    """Project a trainer `_block()` dict to the exact CR-079 reporting shape."""
    if not block or block.get("degenerate"):
        return None
    return {
        "n":                       int(block.get("n", 0)),
        "prevalence":              float(block.get("prevalence", 0.0)),
        "roc_auc":                 float(block.get("roc_auc", 0.0)),
        "pr_auc":                  float(block.get("pr_auc", 0.0)),
        "f1":                      float(block.get("f1_at_threshold", 0.0)),
        "precision":               float(block.get("precision_at_threshold", 0.0)),
        "recall":                  float(block.get("recall_at_threshold", 0.0)),
        "accuracy":                float(block.get("accuracy_at_threshold", 0.0)),
        "brier_calibrated":        float(block.get("brier_calibrated", 0.0)),
        "brier_uncalibrated":      float(block.get("brier_uncalibrated", 0.0)),
        "positive_rate":           float(block.get("positive_rate", 0.0)),
    }


def capture_variant(variant: str, subtype: str, key: str) -> dict[str, Any]:
    schema_path = ARTIFACT_ROOT / key / "feature_schema.json"
    if not schema_path.exists():
        return {
            "service_variant": variant, "claim_subtype": subtype,
            "status": "missing",
            "reason": f"no production bundle at {schema_path}",
        }
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    m = schema.get("metrics", {}) or {}

    return {
        "service_variant":  variant,
        "claim_subtype":    subtype,
        "status":           "ok",
        "model_version":    schema.get("model_version"),
        "calibrator_version": schema.get("calibrator_version"),
        "feature_engineering_version": schema.get("feature_engineering_version"),
        "decision_threshold": float(schema.get("decision_threshold", 0.0)),
        "training_size":      int(schema.get("training_size", 0)),
        "training_prevalence":float(schema.get("training_prevalence", 0.0)),
        "n_feature_columns":  len(schema.get("feature_columns") or []),
        "split_sizes": {
            "n_training_rows":   int(m.get("n_training_rows", 0)),
            "n_validation_rows": int(m.get("n_validation_rows", 0)),
            "n_held_out_rows":   int(m.get("n_held_out_rows", 0)),
        },
        "slices": {
            "oof":        _slice_to_metric_row(m.get("oof")),
            "validation": _slice_to_metric_row(m.get("validation")),
            "held_out":   _slice_to_metric_row(m.get("held_out")),
        },
    }


def main() -> int:
    out = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source":      "production artifacts/featurebuilder/<variant>/feature_schema.json",
        "variants":    {},
    }
    print("CR-079 baseline capture")
    print("=" * 64)
    for variant, subtype, key in VARIANTS:
        row = capture_variant(variant, subtype, key)
        out["variants"][f"{variant}_{subtype}"] = row
        if row["status"] != "ok":
            print(f"  {variant}/{subtype:<24s} {row['status']} ({row['reason']})")
            continue
        h = row["slices"]["held_out"] or {}
        print(
            f"  {variant}/{subtype:<24s} "
            f"AUC={h.get('roc_auc'):.4f}  "
            f"PR-AUC={h.get('pr_auc'):.4f}  "
            f"F1={h.get('f1'):.4f}  "
            f"P={h.get('precision'):.4f}  "
            f"R={h.get('recall'):.4f}  "
            f"Brier={h.get('brier_calibrated'):.4f}  "
            f"thr={row['decision_threshold']:.3f}"
        )

    BASELINE_OUT.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print()
    print(f"Wrote {BASELINE_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
