"""CR-078 — registry coverage guard for reason_renderer.

If anyone adds a new feature to FEATURE_REGISTRY without updating
reason_renderer._FEATURE_TO_BUCKET, this test fails. That way the renderer
can never silently drift behind the registry — every new FB feature gets a
business-language mapping before it can reach a user.
"""

from __future__ import annotations

from rcm.features.registry import FEATURE_REGISTRY
from rcm.ml.reason_renderer import _FEATURE_TO_BUCKET, render_reason


def test_every_registered_feature_has_explicit_bucket():
    missing = sorted(
        name for name in FEATURE_REGISTRY
        if name not in _FEATURE_TO_BUCKET
    )
    assert not missing, (
        "reason_renderer is missing bucket entries for the following registry "
        f"features (add them to _FEATURE_TO_BUCKET): {missing}"
    )


def test_no_renderer_entry_uses_a_nonexistent_feature_name():
    """Catch typos that map a feature name that doesn't exist in the
    registry — those entries do nothing but rot."""
    stale = sorted(
        name for name in _FEATURE_TO_BUCKET
        if name not in FEATURE_REGISTRY
    )
    assert not stale, (
        "reason_renderer maps feature names that are NOT in FEATURE_REGISTRY "
        f"(remove or correct them): {stale}"
    )


def test_render_reason_returns_a_known_bucket_for_every_registered_feature():
    """End-to-end smoke: every registry feature renders to a non-default
    bucket (i.e. not the unknown-name fallback). Catches a `_GEN` entry that
    was meant to be a specific bucket."""
    from rcm.ml.reason_renderer import _GEN

    # _GEN is allowed for explicit GENERAL features; only fail when a feature
    # collapses to general AND isn't on the deliberate general list.
    deliberate_general = {
        "unseen_any", "missing_count",
        "avail_procedure_codes_metadata", "avail_payer_policies",
        "avail_ncci_edits", "avail_lcd_coverage",
        "reference_data_completeness",
    }
    accidental_general = sorted(
        name for name in FEATURE_REGISTRY
        if render_reason(name) == _GEN and name not in deliberate_general
    )
    assert not accidental_general, (
        "These features collapse to REASON_GENERAL but aren't on the "
        "deliberate-general allowlist — pick a more specific bucket "
        f"in reason_renderer: {accidental_general}"
    )
