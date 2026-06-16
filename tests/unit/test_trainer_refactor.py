"""CR-064 — Refactor unit test.

Asserts that `train_healthcare_model` is a thin wrapper around `train_variant`
with the correct fixed parameters. No DB / no model required: we replace
`train_variant` with a recording stub and inspect the kwargs the wrapper
forwards.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

import rcm.ml.trainer as trainer


@pytest.mark.asyncio
async def test_train_healthcare_model_delegates_to_train_variant():
    """`train_healthcare_model(...)` MUST call `train_variant(...)` with
    service_variant='837P', claim_subtype='healthcare' and forward every
    other kwarg untouched."""

    recorded: dict = {}

    async def fake_train_variant(session, artifact_dir, **kwargs):
        recorded.update(kwargs)
        recorded["session"] = session
        recorded["artifact_dir"] = artifact_dir
        return "stub-bundle"

    with patch.object(trainer, "train_variant", side_effect=fake_train_variant):
        out = await trainer.train_healthcare_model(
            session="sess",
            artifact_dir="/tmp/x",
            limit=42,
            n_estimators=200,
            max_depth=5,
            learning_rate=0.05,
        )

    assert out == "stub-bundle"
    assert recorded["session"] == "sess"
    assert recorded["artifact_dir"] == "/tmp/x"
    assert recorded["service_variant"] == "837P"
    assert recorded["claim_subtype"] == "healthcare"
    assert recorded["limit"] == 42
    assert recorded["n_estimators"] == 200
    assert recorded["max_depth"] == 5
    assert recorded["learning_rate"] == 0.05


@pytest.mark.asyncio
async def test_train_healthcare_model_uses_default_hyperparameters():
    """When the caller omits hyperparameters, the wrapper must pass through
    the documented Phase-3 defaults: n_estimators=200, max_depth=5, lr=0.05."""

    recorded: dict = {}

    async def fake_train_variant(session, artifact_dir, **kwargs):
        recorded.update(kwargs)
        return "stub-bundle"

    with patch.object(trainer, "train_variant", side_effect=fake_train_variant):
        await trainer.train_healthcare_model(session="sess", artifact_dir="/tmp/x")

    assert recorded["service_variant"] == "837P"
    assert recorded["claim_subtype"] == "healthcare"
    assert recorded["n_estimators"] == 200
    assert recorded["max_depth"] == 5
    assert recorded["learning_rate"] == 0.05
    assert recorded["limit"] is None


def test_train_variant_is_exported():
    """The new variant-agnostic entrypoint must be accessible on the module."""
    assert hasattr(trainer, "train_variant")
    assert callable(trainer.train_variant)


def test_select_threshold_unchanged():
    """_select_threshold is shared by all training paths; CR-064 must not
    touch it (refactor only)."""
    import numpy as np
    scores = np.linspace(0.01, 0.99, 100)
    y = (scores > 0.5).astype(int)
    t = trainer._select_threshold(scores, y, precision_floor=0.5)
    # The function returns a float in (0, 1); precise value isn't the point.
    # The point is that calling it still works after the refactor.
    assert isinstance(t, float)
    assert 0.0 < t < 1.0
