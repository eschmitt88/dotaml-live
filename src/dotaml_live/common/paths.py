"""Central path resolution for dotaml-live.

Everything is repo-relative so the package is self-contained (no reach into the
sibling research repo). The serving artifacts that replace dotaml-turbo's runtime
val-parquet reads all live inside a model's registry directory.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parents[1]      # src/dotaml_live
SRC_DIR = PKG_DIR.parent                             # src
REPO_ROOT = SRC_DIR.parent                           # dotaml-live

REGISTRY_DIR = Path(os.environ.get("DOTAML_REGISTRY", REPO_ROOT / "registry"))
DATA_DIR = Path(os.environ.get("DOTAML_DATA", REPO_ROOT / "data"))

# Package-bundled OpenDota constants (hero/item metadata)
QUERIES_DIR = PKG_DIR / "queries"
HEROES_JSON = QUERIES_DIR / "heroes.json"
ITEMS_JSON = QUERIES_DIR / "items.json"

# Default model version used until a retrain promotes something newer.
BASE_VERSION = "v7-base"


def live_model_dir() -> Path:
    """Resolve the currently-promoted ('live') model directory.

    Resolution order:
      1. registry/live  (symlink → a version dir)               [preferred]
      2. registry/registry.json  with {"live": "<version>"}
      3. registry/v7-base  (the vendored base checkpoint)       [fallback]
    """
    live_link = REGISTRY_DIR / "live"
    if live_link.is_symlink() or (live_link.exists() and live_link.is_dir()):
        return live_link.resolve()
    reg_json = REGISTRY_DIR / "registry.json"
    if reg_json.exists():
        live = json.loads(reg_json.read_text()).get("live")
        if live:
            return REGISTRY_DIR / live
    return REGISTRY_DIR / BASE_VERSION


# --- artifact paths within a given model directory ---

def model_pt(model_dir: Path) -> Path:
    return model_dir / "model.pt"

def config_yaml(model_dir: Path) -> Path:
    return model_dir / "config.yaml"

def item_vocab_json(model_dir: Path) -> Path:
    return model_dir / "item_vocab.json"

def duration_pmf_npz(model_dir: Path) -> Path:
    """Precomputed global game-end-time PMF (replaces build_optimizer's val read)."""
    return model_dir / "duration_pmf.npz"

def hero_prior_npy(model_dir: Path) -> Path:
    """Precomputed empirical hero-pick prior (replaces lookups' val read)."""
    return model_dir / "hero_prior.npy"

def player_feature_store(model_dir: Path) -> Path:
    """KV store account_id → 8-dim feature vector (replaces lookups' val read)."""
    return model_dir / "player_features.parquet"

def manifest_json(model_dir: Path) -> Path:
    return model_dir / "manifest.json"

def combos_table_json(model_dir: Path) -> Path:
    """Precomputed all-pairs hero-combo discovery table (synergy + kills/min)."""
    return model_dir / "hero_combos.json"
