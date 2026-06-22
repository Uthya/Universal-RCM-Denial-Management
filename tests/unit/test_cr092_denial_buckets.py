"""CR-092 Issue 3 — canonical CARC/RARC bucket mapping invariants.

Guards:
  1. Every value in ``_CATEGORY_TO_BUCKET`` is one of the 11 canonical
     reason_renderer buckets — keeps explanation + recommendation +
     audit vocabularies aligned.
  2. The 11 reason_renderer slugs and ``denial_buckets._VALID_BUCKETS``
     match exactly. If a new bucket is added in one place, both must
     stay in sync or this test fails.

These tests do NOT touch the database — they verify the static mapping
only. Tests that exercise the DB-backed cache live in integration tests.
"""

from __future__ import annotations

from rcm.ml.denial_buckets import (
    _CATEGORY_TO_BUCKET,
    _CODE_TO_BUCKET_OVERRIDE,
    _VALID_BUCKETS,
)
from rcm.ml.reason_renderer import REASON_BUCKETS


def test_every_category_maps_to_canonical_bucket() -> None:
    """No mapping value may drift away from the 11 canonical slugs."""
    for category, bucket in _CATEGORY_TO_BUCKET.items():
        assert bucket in _VALID_BUCKETS, (
            f"_CATEGORY_TO_BUCKET[{category!r}] = {bucket!r} is not in the "
            f"canonical 11-bucket set"
        )


def test_canonical_bucket_set_matches_reason_renderer() -> None:
    """denial_buckets._VALID_BUCKETS must equal the slugs reason_renderer
    actually emits — otherwise downstream consumers see a different
    vocabulary depending on which side of the explanation they consult."""
    renderer_slugs = {b[0] for b in REASON_BUCKETS}
    assert _VALID_BUCKETS == renderer_slugs, (
        "Drift between rcm.ml.denial_buckets._VALID_BUCKETS and "
        "rcm.ml.reason_renderer.REASON_BUCKETS. Bring them back into sync.\n"
        f"  in denial_buckets but not renderer: {_VALID_BUCKETS - renderer_slugs}\n"
        f"  in renderer but not denial_buckets: {renderer_slugs - _VALID_BUCKETS}"
    )


def test_critical_categories_mapped() -> None:
    """The audit-finding categories must be present and correctly bucketed."""
    # Frequency limits → history. This is the CR-092 Issue 1 alignment.
    assert _CATEGORY_TO_BUCKET["frequency_limits"] == "history"
    # Authorization → authorization. Critical for CARC 197 alignment.
    assert _CATEGORY_TO_BUCKET["authorization"] == "authorization"
    # COB → coverage. Critical for CARC 22 / 23 alignment.
    assert _CATEGORY_TO_BUCKET["coordination_benefits"] == "coverage"
    # Timely filing → timely_filing.
    assert _CATEGORY_TO_BUCKET["timely_filing"] == "timely_filing"
    # Medical necessity → diagnosis.
    assert _CATEGORY_TO_BUCKET["medical_necessity"] == "diagnosis"
    # Documentation → documentation.
    assert _CATEGORY_TO_BUCKET["documentation"] == "documentation"


def test_overrides_are_canonical_buckets() -> None:
    """Per-code overrides must all use canonical bucket slugs."""
    for (code_type, code), bucket in _CODE_TO_BUCKET_OVERRIDE.items():
        assert bucket in _VALID_BUCKETS, (
            f"_CODE_TO_BUCKET_OVERRIDE[({code_type!r}, {code!r})] = "
            f"{bucket!r} is not in the canonical 11-bucket set"
        )


def test_audit_critical_overrides_present() -> None:
    """The audit (CR-092) explicitly framed these denials as history-related.
    Their override entries must be present so prediction and adjudication
    sides agree on the bucket."""
    assert _CODE_TO_BUCKET_OVERRIDE[("CARC", "119")] == "history"
    assert _CODE_TO_BUCKET_OVERRIDE[("CARC", "151")] == "history"
    assert _CODE_TO_BUCKET_OVERRIDE[("RARC", "M86")] == "history"

    # The missing-field RARCs surface as their target field's natural bucket.
    assert _CODE_TO_BUCKET_OVERRIDE[("RARC", "M76")] == "diagnosis"
    assert _CODE_TO_BUCKET_OVERRIDE[("RARC", "M77")] == "procedure"
    assert _CODE_TO_BUCKET_OVERRIDE[("RARC", "N382")] == "coverage"
