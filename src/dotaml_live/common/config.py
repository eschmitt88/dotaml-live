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
