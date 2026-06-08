"""Thin YAML config loader."""

from __future__ import annotations

from pathlib import Path

import yaml

from . import paths


def load_yaml(path: str | Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def serving_config() -> dict:
    return load_yaml(paths.REPO_ROOT / "config" / "serving.yaml")


def pipeline_config() -> dict:
    return load_yaml(paths.REPO_ROOT / "config" / "pipeline.yaml")


def training_config() -> dict:
    return load_yaml(paths.REPO_ROOT / "config" / "training.yaml")


def splits_policy() -> dict:
    return load_yaml(paths.REPO_ROOT / "splits.yaml")


def hero_config() -> dict:
    """Hero-id range + embedding vocab — single source of truth (config/training.yaml
    `hero:` block). Falls back to the historical [1, 150] / vocab 151 when absent so
    older configs keep working. See ADR 0007."""
    h = (training_config().get("hero") or {})
    return {"id_min": int(h.get("id_min", 1)),
            "id_max": int(h.get("id_max", 150)),
            "vocab_size": int(h.get("vocab_size", 151))}
