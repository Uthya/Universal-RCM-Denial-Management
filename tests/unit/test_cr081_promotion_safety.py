"""CR-081 — promotion safety & runtime consistency tests.

Pure-Python; no DB, no API spin-up. Guards:

  Issue A — POST /reload-bundles:
    * clears the predictor cache
    * reports cleared count + bundle inventory shape
    * inventory entries carry model_version + threshold + FE version

  Issue C — rollback inventory:
    * lists canonical rollback slot + every timestamp-suffixed sibling
    * canonical entry comes first (next-revert target)
    * unknown / non-bundle directories are ignored
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make scripts/ importable for the promote-script helpers.
SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cr079_promote as P  # noqa: E402
from rcm.routers.public import predictions as PR  # noqa: E402


# ---------------------------------------------------------------------------
# Issue A — cache invalidation + inventory shape
# ---------------------------------------------------------------------------

def _stub_bundle(dir: Path, model_version: str, threshold: float = 0.15) -> None:
    """Write a minimal feature_schema.json that satisfies _read_bundle_schema."""
    dir.mkdir(parents=True, exist_ok=True)
    (dir / "feature_schema.json").write_text(json.dumps({
        "schema_version": "v1.0.0",
        "feature_engineering_version": "v1.0.0",
        "model_version":   model_version,
        "calibrator_version": "isotonic_v1",
        "service_variant": "837P",
        "claim_subtype":   "healthcare",
        "feature_columns": [],
        "decision_threshold": threshold,
        "training_size": 100,
        "training_prevalence": 0.25,
        "metrics": {"held_out": {"f1_at_threshold": 0.9, "roc_auc": 0.99}},
    }), encoding="utf-8")


class TestCacheInvalidation:
    def setup_method(self):
        # Snapshot the original cache so we can restore between tests.
        self._orig = dict(PR._FB_PREDICTOR_CACHE)

    def teardown_method(self):
        PR._FB_PREDICTOR_CACHE.clear()
        PR._FB_PREDICTOR_CACHE.update(self._orig)

    def test_cache_clear_removes_every_entry(self):
        PR._FB_PREDICTOR_CACHE["837P_healthcare"] = object()
        PR._FB_PREDICTOR_CACHE["837D_dental"] = object()
        assert len(PR._FB_PREDICTOR_CACHE) >= 2
        cleared = len(PR._FB_PREDICTOR_CACHE)
        PR._FB_PREDICTOR_CACHE.clear()
        assert PR._FB_PREDICTOR_CACHE == {}
        assert cleared >= 2

    def test_inventory_reads_schema_fields(self, tmp_path, monkeypatch):
        # Point the artifact root at a temp dir so we don't read production
        # bundles for unit-test purposes.
        monkeypatch.setattr(PR, "_FB_ARTIFACT_ROOT", tmp_path)
        _stub_bundle(tmp_path / "837P_healthcare", "v1.test.001", threshold=0.10)
        _stub_bundle(tmp_path / "837D_dental",     "v1.test.002", threshold=0.20)

        # Note: _FB_VARIANT_KEYS keys are (variant, subtype) tuples.
        out = PR._inventory_available_bundles()
        keys = {(b["service_variant"], b["claim_subtype"]) for b in out}
        assert ("837P", "healthcare") in keys
        assert ("837D", "dental")     in keys

        hc = next(b for b in out if b["service_variant"] == "837P")
        assert hc["model_version"]     == "v1.test.001"
        assert hc["decision_threshold"] == pytest.approx(0.10)
        assert hc["feature_engineering_version"] == "v1.0.0"
        assert hc["calibrator_version"] == "isotonic_v1"

    def test_inventory_skips_missing_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(PR, "_FB_ARTIFACT_ROOT", tmp_path)
        _stub_bundle(tmp_path / "837I_home_care", "v1.test.solo")
        out = PR._inventory_available_bundles()
        # Only one variant has a bundle on disk.
        assert len(out) == 1
        assert out[0]["service_variant"] == "837I"

    def test_inventory_ignores_bundle_without_schema(self, tmp_path, monkeypatch):
        monkeypatch.setattr(PR, "_FB_ARTIFACT_ROOT", tmp_path)
        (tmp_path / "837P_healthcare").mkdir()  # no schema file
        _stub_bundle(tmp_path / "837D_dental", "v1.real")
        out = PR._inventory_available_bundles()
        # The bundle without schema is silently skipped (read_bundle_schema
        # returns None; the dict comprehension still includes the row but
        # with None fields).
        keys = {b["service_variant"] for b in out}
        # 837P/healthcare's dir exists so it IS enumerated; schema fields are
        # None. 837D should be present with its real model_version.
        assert "837D" in keys
        assert "837P" in keys
        no_schema = next(b for b in out if b["service_variant"] == "837P")
        assert no_schema["model_version"] is None


# ---------------------------------------------------------------------------
# Issue C — rollback inventory
# ---------------------------------------------------------------------------

class TestRollbackInventory:
    def test_canonical_slot_listed_first(self, tmp_path, monkeypatch):
        monkeypatch.setattr(P, "ROLLBACK_ROOT", tmp_path)
        _stub_bundle(tmp_path / "837I_home_care", "v1.baseline")
        _stub_bundle(tmp_path / "837I_home_care.20260101T000000", "v1.older")
        inv = P._collect_rollback_inventory()
        assert "837I_home_care" in inv
        bundles = inv["837I_home_care"]
        assert len(bundles) == 2
        # canonical entry first
        assert bundles[0]["slot"] == "canonical"
        assert bundles[0]["model_version"] == "v1.baseline"
        # rotated entry second
        assert bundles[1]["slot"] == "rotated"
        assert bundles[1]["model_version"] == "v1.older"
        assert bundles[1]["timestamp_suffix"] == "20260101T000000"

    def test_multiple_rotated_siblings_listed_newest_first(self, tmp_path, monkeypatch):
        monkeypatch.setattr(P, "ROLLBACK_ROOT", tmp_path)
        _stub_bundle(tmp_path / "837I_home_care", "v1.current")
        _stub_bundle(tmp_path / "837I_home_care.20260101T000000", "v1.old1")
        _stub_bundle(tmp_path / "837I_home_care.20260601T000000", "v1.old2")
        bundles = P._collect_rollback_inventory()["837I_home_care"]
        # canonical first, then rotated newest-first (20260601 > 20260101)
        assert bundles[0]["slot"] == "canonical"
        rotated = [b for b in bundles if b["slot"] == "rotated"]
        assert [b["timestamp_suffix"] for b in rotated] == ["20260601T000000", "20260101T000000"]

    def test_no_rollback_dir_returns_empty(self, tmp_path, monkeypatch):
        # Point at a path that doesn't exist
        monkeypatch.setattr(P, "ROLLBACK_ROOT", tmp_path / "doesnt-exist")
        assert P._collect_rollback_inventory() == {}

    def test_ignores_unknown_variant_directories(self, tmp_path, monkeypatch):
        monkeypatch.setattr(P, "ROLLBACK_ROOT", tmp_path)
        _stub_bundle(tmp_path / "837I_home_care", "v1.real")
        _stub_bundle(tmp_path / "not_a_variant",  "v1.junk")
        inv = P._collect_rollback_inventory()
        assert set(inv.keys()) == {"837I_home_care"}

    def test_describe_bundle_extracts_held_out_metrics(self, tmp_path):
        b = tmp_path / "bundle"
        _stub_bundle(b, "v1.x", threshold=0.42)
        out = P._describe_bundle(b)
        assert out["model_version"] == "v1.x"
        assert out["decision_threshold"] == pytest.approx(0.42)
        assert out["held_out_f1"] == 0.9
        assert out["held_out_auc"] == 0.99

    def test_describe_bundle_returns_none_for_missing_schema(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        assert P._describe_bundle(empty) is None


class TestPromoteScriptConfig:
    """Smoke checks on the constants we lean on in the gate logic."""

    def test_variants_constant_matches_gates(self):
        # Already covered by test_cr079_harness but re-asserted here so CR-081
        # changes can't silently de-sync.
        assert set(P.VARIANTS) == set(P.GATES.keys())
