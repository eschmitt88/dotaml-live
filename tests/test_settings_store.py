"""Unit tests for the JSON settings store (no model artifacts required)."""

from __future__ import annotations

from dotaml_live.serving import settings_store


def test_load_missing_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_store.paths, "DATA_DIR", tmp_path)
    assert settings_store.load() == {}


def test_update_merges_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_store.paths, "DATA_DIR", tmp_path)
    players = [{"id": "p1", "name": "Alaric", "account": "123456"}]
    out = settings_store.update({"players": players})
    assert out["players"] == players
    # shallow merge keeps existing keys
    out = settings_store.update({"other": 1})
    assert out["players"] == players and out["other"] == 1
    # survives a fresh load (i.e. a server restart)
    assert settings_store.load()["players"] == players


def test_none_deletes_key(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_store.paths, "DATA_DIR", tmp_path)
    settings_store.update({"a": 1, "b": 2})
    out = settings_store.update({"a": None})
    assert "a" not in out and out["b"] == 2


def test_corrupt_file_recovers(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_store.paths, "DATA_DIR", tmp_path)
    (tmp_path / "settings.json").write_text("{not json")
    assert settings_store.load() == {}
    assert settings_store.update({"a": 1}) == {"a": 1}
