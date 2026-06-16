"""CR-080 — presentation-layer grouping for training history.

Pure-Python; no DB. Guards that:
  * three variant rows from one /train call collapse into ONE TrainingRunItem
  * gap > 60 s starts a new run
  * same variant inside a window: the LATER row wins (retry case)
  * synthetic training_run_id is deterministic on the same input
  * legacy single-row data still surfaces (as a single-variant run)
  * variants dict is keyed by claim_subtype
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from rcm.routers.public.ml import (
    GROUPING_WINDOW_SECONDS,
    _group_rows_into_runs,
    _model_version_prefix,
)


def _row(
    row_id: int,
    variant: str,
    subtype: str,
    ts: datetime,
    *,
    f1: float = 0.9,
    auc: float = 0.95,
    model_version: str | None = None,
) -> dict:
    return {
        "id": row_id,
        "training_id": f"00000000-0000-0000-0000-{row_id:012d}",
        "training_timestamp": ts,
        "model_version": model_version or f"v1.fb.{ts.strftime('%Y%m%dT%H%M%S')}.{variant}_{subtype}",
        "service_variant": variant,
        "claim_subtype": subtype,
        "total_claims_used": 100,
        "training_samples": 70,
        "validation_samples": 15,
        "metrics": {
            "held_out": {
                "f1_at_threshold": f1,
                "roc_auc": auc,
                "precision_at_threshold": 0.88,
                "recall_at_threshold": 0.93,
                "accuracy_at_threshold": 0.91,
                "pr_auc": 0.92,
                "prevalence": 0.25,
            },
            "prevalence": 0.25,
        },
        "training_duration_seconds": 1.5,
        "decision_threshold": 0.15,
        "status": "success",
    }


class TestModelVersionPrefix:
    def test_strips_variant_subtype_suffix(self):
        assert (
            _model_version_prefix("v1.fb.20260616T060216.837P_healthcare")
            == "v1.fb.20260616T060216"
        )

    def test_handles_tuned_variant(self):
        assert (
            _model_version_prefix("v1.fb.tuned.20260616T090800.837I_home_care")
            == "v1.fb.tuned.20260616T090800"
        )

    def test_none_in_none_out(self):
        assert _model_version_prefix(None) is None
        assert _model_version_prefix("") is None

    def test_no_dot_returns_input(self):
        assert _model_version_prefix("blob") == "blob"


class TestGroupingHelper:
    def _three_variants(self, base_ts: datetime) -> list[dict]:
        """Three rows within ~1 s of each other — the realistic /train shape."""
        return [
            _row(3, "837I", "home_care",  base_ts + timedelta(milliseconds=20)),
            _row(2, "837D", "dental",     base_ts + timedelta(milliseconds=10)),
            _row(1, "837P", "healthcare", base_ts),
        ]

    def test_three_within_window_collapse_to_one_run(self):
        ts = datetime(2026, 6, 16, 6, 2, 16, tzinfo=timezone.utc)
        rows = self._three_variants(ts)
        runs = _group_rows_into_runs(rows)
        assert len(runs) == 1
        run = runs[0]
        assert run.variant_count == 3
        assert set(run.variants.keys()) == {"healthcare", "dental", "home_care"}

    def test_run_id_is_deterministic(self):
        ts = datetime(2026, 6, 16, 6, 2, 16, tzinfo=timezone.utc)
        rows = self._three_variants(ts)
        a = _group_rows_into_runs(rows)
        b = _group_rows_into_runs(rows)
        assert a[0].training_run_id == b[0].training_run_id

    def test_run_id_format(self):
        ts = datetime(2026, 6, 16, 6, 2, 16, tzinfo=timezone.utc)
        runs = _group_rows_into_runs(self._three_variants(ts))
        assert runs[0].training_run_id.startswith("run-")
        assert len(runs[0].training_run_id) == 16  # "run-" + 12-hex

    def test_started_and_ended_at_span_run(self):
        ts = datetime(2026, 6, 16, 6, 2, 16, tzinfo=timezone.utc)
        runs = _group_rows_into_runs(self._three_variants(ts))
        run = runs[0]
        # started_at is the earliest, ended_at is the latest
        assert run.started_at <= run.ended_at

    def test_gap_above_window_splits_runs(self):
        a_ts = datetime(2026, 6, 16, 6, 2, 16, tzinfo=timezone.utc)
        b_ts = a_ts + timedelta(seconds=GROUPING_WINDOW_SECONDS + 5)
        rows = [
            # Newest first (as DB ORDER BY DESC would yield)
            _row(13, "837I", "home_care",  b_ts + timedelta(milliseconds=20)),
            _row(12, "837D", "dental",     b_ts + timedelta(milliseconds=10)),
            _row(11, "837P", "healthcare", b_ts),
            _row(3,  "837I", "home_care",  a_ts + timedelta(milliseconds=20)),
            _row(2,  "837D", "dental",     a_ts + timedelta(milliseconds=10)),
            _row(1,  "837P", "healthcare", a_ts),
        ]
        runs = _group_rows_into_runs(rows)
        assert len(runs) == 2
        assert runs[0].variant_count == 3
        assert runs[1].variant_count == 3
        assert runs[0].training_run_id != runs[1].training_run_id

    def test_same_variant_twice_in_window_later_wins(self):
        ts = datetime(2026, 6, 16, 6, 2, 16, tzinfo=timezone.utc)
        rows = [
            # Later row carries f1=0.99, earlier row carries f1=0.50
            _row(99, "837P", "healthcare",
                 ts + timedelta(seconds=10), f1=0.99),
            _row(1,  "837P", "healthcare", ts, f1=0.50),
        ]
        runs = _group_rows_into_runs(rows)
        assert len(runs) == 1
        assert runs[0].variant_count == 1  # collapsed to one variant
        assert runs[0].variants["healthcare"].f1_score == 0.99

    def test_legacy_single_row_renders_as_single_variant_run(self):
        ts = datetime(2026, 6, 16, 6, 2, 16, tzinfo=timezone.utc)
        runs = _group_rows_into_runs([_row(1, "837P", "healthcare", ts)])
        assert len(runs) == 1
        assert runs[0].variant_count == 1
        assert "healthcare" in runs[0].variants

    def test_empty_input_returns_empty_list(self):
        assert _group_rows_into_runs([]) == []

    def test_model_version_group_collapses_when_prefix_matches(self):
        ts = datetime(2026, 6, 16, 6, 2, 16, tzinfo=timezone.utc)
        shared_prefix = "v1.fb.20260616T060216"
        rows = [
            _row(3, "837I", "home_care",
                 ts + timedelta(milliseconds=2),
                 model_version=f"{shared_prefix}.837I_home_care"),
            _row(2, "837D", "dental",
                 ts + timedelta(milliseconds=1),
                 model_version=f"{shared_prefix}.837D_dental"),
            _row(1, "837P", "healthcare", ts,
                 model_version=f"{shared_prefix}.837P_healthcare"),
        ]
        runs = _group_rows_into_runs(rows)
        assert runs[0].model_version_group == shared_prefix

    def test_training_time_sums_across_variants(self):
        ts = datetime(2026, 6, 16, 6, 2, 16, tzinfo=timezone.utc)
        runs = _group_rows_into_runs(self._three_variants(ts))
        # Each row reports 1.5 s; 3 variants → 4.5 s total
        assert runs[0].training_time_seconds == 4.5

    def test_variants_keyed_by_claim_subtype(self):
        ts = datetime(2026, 6, 16, 6, 2, 16, tzinfo=timezone.utc)
        runs = _group_rows_into_runs(self._three_variants(ts))
        assert set(runs[0].variants.keys()) == {"healthcare", "dental", "home_care"}
        assert runs[0].variants["healthcare"].service_variant == "837P"
        assert runs[0].variants["dental"].service_variant == "837D"
        assert runs[0].variants["home_care"].service_variant == "837I"

    def test_long_run_does_not_split_at_60s_mark(self):
        """A single /train invocation where 837P takes 22 s should still
        collapse into ONE run even though the gap between the first and last
        row's training_timestamp approaches the window boundary."""
        ts = datetime(2026, 6, 16, 6, 2, 16, tzinfo=timezone.utc)
        rows = [
            _row(3, "837I", "home_care",  ts + timedelta(seconds=25)),
            _row(2, "837D", "dental",     ts + timedelta(seconds=22)),
            _row(1, "837P", "healthcare", ts),
        ]
        runs = _group_rows_into_runs(rows)
        assert len(runs) == 1
        assert runs[0].variant_count == 3
