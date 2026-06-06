"""Patch-edge registry — the single source of truth for the date->patch_id mapping.

Loaded from config/patches.yaml (fallback to the historical hardcoded edges if absent
so nothing breaks). Both the feature builder and the training data loader read this, so
adding a patch is one edit (or `patch_watch --add`), picked up everywhere.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from . import paths

# Historical edges (what v7-base was trained with) — fallback if the yaml is missing.
_FALLBACK = {
    "default_patch_id": 1,
    "edges": [
        {"date": "2025-08-01", "patch_id": 2, "name": "pre-7.40-a"},
        {"date": "2025-09-10", "patch_id": 3, "name": "pre-7.40-b"},
        {"date": "2025-12-16", "patch_id": 1, "name": "7.40"},
        {"date": "2026-03-25", "patch_id": 4, "name": "7.41"},
    ],
}

PATCH_VOCAB_SIZE = 8   # must match the model's patch_embed rows


def _path() -> Path:
    return paths.REPO_ROOT / "config" / "patches.yaml"


@lru_cache(maxsize=1)
def _load() -> dict:
    p = _path()
    return yaml.safe_load(p.read_text()) if p.exists() else _FALLBACK


def reload() -> None:
    _load.cache_clear()


def default_patch_id() -> int:
    return int(_load().get("default_patch_id", 1))


def edges() -> list[tuple[str, int]]:
    """(date, patch_id) sorted ascending by date."""
    return [(e["date"], int(e["patch_id"]))
            for e in sorted(_load()["edges"], key=lambda e: e["date"])]


def patch_id_for(date_str: str, default: int | None = None) -> int:
    out = default if default is not None else default_patch_id()
    for ed, pid in edges():
        if date_str >= ed:
            out = pid
    return out


def latest_patch() -> dict:
    return sorted(_load()["edges"], key=lambda e: e["date"])[-1]


def current_patch_start() -> str:
    """Date of the most recent patch — the boundary the recency upweight keys off."""
    return latest_patch()["date"]


def known_dates() -> set[str]:
    return {e["date"] for e in _load()["edges"]}


def add_edge(date: str, name: str) -> int:
    """Append a new patch with the next free small patch_id and persist. Returns the id."""
    cfg = _load()
    used = {int(e["patch_id"]) for e in cfg["edges"]}
    free = [i for i in range(1, PATCH_VOCAB_SIZE) if i not in used]
    if not free:
        raise RuntimeError(f"patch_vocab ({PATCH_VOCAB_SIZE}) exhausted — grow the model's "
                           "patch_embed before adding more patches")
    nid = free[0]
    cfg["edges"].append({"date": date, "patch_id": nid, "name": name})
    _path().write_text(yaml.safe_dump(cfg, sort_keys=False))
    reload()
    return nid
