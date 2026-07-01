"""Model registry — versioned model dirs + a 'live' pointer.

Each version is a directory under registry/<version>/ holding:
  model.pt, config.yaml, item_vocab.json, metrics.json,
  served artifacts (hero_prior.npy, duration_pmf.npz, player_features.parquet),
  manifest.json.

registry.json at the registry root is the source of truth for which version is
'live' (plus a versions list); a `live` symlink is maintained alongside it for
convenience and so paths.live_model_dir() resolves quickly. Serving hot-reloads
when this pointer moves.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, asdict
from pathlib import Path

from ..common import paths

ARTIFACTS = ["hero_prior.npy", "duration_pmf.npz", "player_features.parquet",
             "hero_combos.json"]   # carried forward so the discovery tab never breaks;
                                   # regenerated per-model post-promotion (precompute_combos)
CORE = ["model.pt", "config.yaml", "item_vocab.json"]


def _registry_json() -> Path:
    return paths.REGISTRY_DIR / "registry.json"


def _read() -> dict:
    p = _registry_json()
    if p.exists():
        return json.loads(p.read_text())
    return {"live": None, "versions": []}


def _write(state: dict) -> None:
    _registry_json().write_text(json.dumps(state, indent=2))


def version_dir(version: str) -> Path:
    return paths.REGISTRY_DIR / version


def new_version_dir(version: str) -> Path:
    d = version_dir(version)
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class Manifest:
    version: str
    parent: str | None            # the checkpoint this was warm-started from
    created: str                  # ISO timestamp (passed in; no Date.now in-process)
    train_window: dict            # seal windows used
    metrics: dict                 # val-split search metrics
    promoted: bool = False
    notes: str = ""


def write_manifest(version: str, manifest: Manifest) -> None:
    (version_dir(version) / "manifest.json").write_text(json.dumps(asdict(manifest), indent=2))


def read_manifest(version: str) -> dict | None:
    """The version's durable record (parent, created, train_window, promoted,
    notes), or None if it predates manifest-writing."""
    p = version_dir(version) / "manifest.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return None
    return None


def register(version: str) -> None:
    """Record a version (without promoting it)."""
    st = _read()
    if version not in st["versions"]:
        st["versions"].append(version)
    _write(st)


def list_versions() -> list[str]:
    return _read()["versions"]


def live_version() -> str | None:
    return _read()["live"]


def set_live(version: str) -> None:
    """Promote a version: update registry.json + the `live` symlink atomically."""
    d = version_dir(version)
    assert (d / "model.pt").exists(), f"{version} has no model.pt"
    st = _read()
    if version not in st["versions"]:
        st["versions"].append(version)
    st["live"] = version
    _write(st)
    # maintain the convenience symlink (registry/live -> version)
    link = paths.REGISTRY_DIR / "live"
    tmp = paths.REGISTRY_DIR / ".live.tmp"
    if tmp.exists() or tmp.is_symlink():
        tmp.unlink()
    tmp.symlink_to(d.name)            # relative target
    tmp.replace(link)
    # reflect promotion in the manifest if present
    mpath = d / "manifest.json"
    if mpath.exists():
        m = json.loads(mpath.read_text())
        m["promoted"] = True
        mpath.write_text(json.dumps(m, indent=2))


def has_artifacts(version: str) -> bool:
    d = version_dir(version)
    return all((d / f).exists() for f in CORE)


def copy_artifacts_from(src_version: str, dst_version: str) -> None:
    """Carry the served priors forward when they don't change between versions."""
    s, d = version_dir(src_version), version_dir(dst_version)
    for f in ARTIFACTS:
        if (s / f).exists():
            shutil.copy2(s / f, d / f)


def prune(keep_last_n: int) -> list[str]:
    """Delete oldest non-live versions beyond keep_last_n. Returns removed."""
    st = _read()
    live = st["live"]
    keep = set(st["versions"][-keep_last_n:]) | ({live} if live else set())
    removed = []
    for v in list(st["versions"]):
        if v not in keep:
            shutil.rmtree(version_dir(v), ignore_errors=True)
            st["versions"].remove(v)
            removed.append(v)
    _write(st)
    return removed
