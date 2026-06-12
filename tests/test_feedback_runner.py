"""Per-ticket implementation option resolution (no claude / model needed)."""

from __future__ import annotations

import pytest

from dotaml_live.serving import feedback_runner as runner
from dotaml_live.serving import settings_store


@pytest.fixture(autouse=True)
def tmp_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_store.paths, "DATA_DIR", tmp_path)


@pytest.fixture(autouse=True)
def yaml_cfg(monkeypatch):
    monkeypatch.setattr(runner, "_cfg", lambda: {
        "implement_model": "yaml-model", "implement_timeout_minutes": 45})


def test_yaml_fallback():
    model, minutes = runner._implement_opts({})
    assert model == "yaml-model"
    assert minutes == 45


def test_settings_override_yaml():
    settings_store.update({"implement_model": "opus", "implement_effort": "low"})
    model, minutes = runner._implement_opts({})
    assert model == "opus"
    assert minutes == runner.EFFORT_TIMEOUT_MINUTES["low"]


def test_ticket_overrides_settings():
    settings_store.update({"implement_model": "opus", "implement_effort": "low"})
    model, minutes = runner._implement_opts(
        {"implement_model": "haiku", "implement_effort": "high"})
    assert model == "haiku"
    assert minutes == runner.EFFORT_TIMEOUT_MINUTES["high"]


def test_null_ticket_values_fall_through():
    # /approve stores None when the user keeps the defaults — must not mask them
    settings_store.update({"implement_model": "sonnet"})
    model, minutes = runner._implement_opts(
        {"implement_model": None, "implement_effort": None})
    assert model == "sonnet"
    assert minutes == 45
