"""Lookups: hero ID ↔ name, item ID ↔ name + cost, account_id → player features.

All ID-keyed dicts use integer keys.

Player-feature lookup pulls the user's typical feature profile from val
parquet rows (averaged over matches they appear in). For unknown accounts,
returns ANON_FEATS from v7_inference.

Hero/item metadata from OpenDota constants (heroes.json, items.json
saved alongside this module).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from ..model.v7_inference import ANON_FEATS, EXP_DIR, FEAT_NAMES, PROJECT_ROOT
from ..common import paths
from . import artifacts

SERVE_DIR = Path(__file__).resolve().parent

# Anonymous account IDs (matches data.py:ANON_IDS)
ANON_ACCOUNT_IDS = {0, 4294967295}


# ----- Hero lookups -----


@lru_cache(maxsize=1)
def _heroes_json() -> dict:
    with open(SERVE_DIR / "heroes.json") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def hero_id_to_name() -> dict[int, str]:
    """Map: 1 -> 'Anti-Mage', 6 -> 'Drow Ranger', etc.

    Uses OpenDota's `localized_name` if available, else slugified `name`.
    """
    out = {}
    for hid_str, h in _heroes_json().items():
        hid = int(hid_str)
        name = h.get("localized_name") or h["name"].replace("npc_dota_hero_", "").replace("_", " ").title()
        out[hid] = name
    return out


@lru_cache(maxsize=1)
def hero_name_to_id() -> dict[str, int]:
    """Reverse map. Names are case-INSENSITIVE; lookup via .lower()."""
    return {n.lower(): i for i, n in hero_id_to_name().items()}


@lru_cache(maxsize=1)
def hero_id_to_roles() -> dict[int, list[str]]:
    """Map: 1 -> ['Carry', 'Escape']."""
    return {int(k): h.get("roles", []) for k, h in _heroes_json().items()}


@lru_cache(maxsize=1)
def hero_id_to_attr() -> dict[int, str]:
    """Map: 1 -> 'agi' (str / agi / int / all)."""
    return {int(k): h.get("primary_attr", "?") for k, h in _heroes_json().items()}


@lru_cache(maxsize=1)
def _hero_abilities_json() -> dict:
    """hero_abilities.json: npc hero name -> [{'dname', 'desc'}, ...].

    Regenerated from the OpenDota constants by
    `python -m dotaml_live.queries._refresh_hero_abilities`. Returns {} when
    the file is missing or unreadable so callers can fall back gracefully.
    """
    try:
        with open(SERVE_DIR / "hero_abilities.json") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


@lru_cache(maxsize=256)
def hero_id_to_abilities(hid: int) -> list[str]:
    """Short 'Dname: desc' strings for a hero's abilities; [] on any miss."""
    h = _heroes_json().get(str(int(hid)))
    if h is None:
        return []
    return [f"{a['dname']}: {a['desc']}"
            for a in _hero_abilities_json().get(h.get("name", ""), [])
            if a.get("dname") and a.get("desc")]


def hero_name(hid: int) -> str:
    return hero_id_to_name().get(int(hid), f"hero_{hid}")


def hero_id(name: str) -> int | None:
    """Look up by case-insensitive localized name; returns None on miss."""
    return hero_name_to_id().get(name.lower())


# ----- Item lookups -----


@lru_cache(maxsize=1)
def _items_json() -> dict:
    with open(SERVE_DIR / "items.json") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def item_id_to_info() -> dict[int, dict]:
    """Map: item_id (int) -> {'name', 'dname', 'cost'}.

    Keyed by the integer item_id used in our rich_cols + item_vocab.
    """
    out = {}
    for slug, info in _items_json().items():
        iid = info.get("id")
        if iid is None:
            continue
        out[int(iid)] = {
            "name": slug,
            "dname": info.get("dname", slug),
            "cost": info.get("cost") or 0,
        }
    return out


def item_name(iid: int) -> str:
    info = item_id_to_info().get(int(iid))
    return info["dname"] if info else f"item_{iid}"


def item_cost(iid: int) -> int:
    """Total gold cost. Returns 0 for unknown items (caller should handle)."""
    info = item_id_to_info().get(int(iid))
    return int(info["cost"]) if info else 0


@lru_cache(maxsize=1)
def _item_name_to_id() -> dict[str, int]:
    """Map item slug (e.g. 'point_booster') -> integer id."""
    out = {}
    for slug, info in _items_json().items():
        iid = info.get("id")
        if iid is not None:
            out[slug] = int(iid)
    return out


@lru_cache(maxsize=1)
def item_id_to_components() -> dict[int, list[int]]:
    """Map item_id -> list of immediate component item_ids.

    Empty-string component placeholders (recipe markers in OpenDota) are
    dropped here; the recipe gold cost is recovered in decompose_item()
    as (item total cost - sum of component costs).
    """
    name2id = _item_name_to_id()
    out: dict[int, list[int]] = {}
    for slug, info in _items_json().items():
        iid = info.get("id")
        if iid is None:
            continue
        comps = info.get("components") or []
        comp_ids = [name2id[c] for c in comps if c and c in name2id]
        out[int(iid)] = comp_ids
    return out


def decompose_item(iid: int, _depth: int = 0) -> list[tuple[int | None, int]]:
    """Recursively decompose a built item into its purchasable pieces.

    Returns an ordered list of (component_item_id, cost) tuples representing
    the base components + recipe costs that sum to the item's total cost.
    A recipe step is returned as (None, recipe_cost).

    Components within an item are ordered cheapest-first (matches the way
    gold trickles in — you buy affordable pieces as you go, then combine).
    Recipes are appended last (you complete the item once components are in).

    Example: decompose_item(110)  # Refresher Orb
      -> [(base components of ring_of_tarrasque...), (recipe, X),
          (base components of tiara_of_selemene...), (recipe, Y),
          (refresher recipe, Z)]
    """
    info = item_id_to_info().get(int(iid))
    if info is None:
        return [(int(iid), 0)]
    comp_ids = item_id_to_components().get(int(iid), [])
    if not comp_ids:
        # Base item — leaf
        return [(int(iid), int(info["cost"]))]

    # Recurse into each component, cheapest-component-first
    comp_sorted = sorted(comp_ids, key=lambda c: item_cost(c))
    leaves: list[tuple[int | None, int]] = []
    comp_cost_sum = 0
    for c in comp_sorted:
        sub = decompose_item(c, _depth + 1)
        leaves.extend(sub)
        comp_cost_sum += sum(cost for _, cost in sub)

    # Recipe cost = this item's total cost - sum of all component costs
    recipe_cost = int(info["cost"]) - comp_cost_sum
    if recipe_cost > 0:
        leaves.append((None, recipe_cost))  # None marks a recipe purchase
    return leaves


# ----- Account → player features -----
# NOTE: the prototype's runtime val-parquet + sidecar reader was removed when this
# module was hardened for serving. Per-account features now come from the
# registry-resident player-feature store (queries/artifacts.py), precomputed by
# the pipeline / bootstrap_from_snapshot.py.


def lookup_player_features(account_id: int) -> np.ndarray | None:
    """Return an account's typical 8-dim feature vector from the registry-resident
    player-feature store (precomputed by bootstrap_from_snapshot.py / the pipeline).
    Returns None for anonymous or unknown accounts.

    Hardened from the prototype: reads the served artifact, not a val parquet.
    """
    if int(account_id) in ANON_ACCOUNT_IDS:
        return None
    store = artifacts.load_player_feature_store(str(paths.live_model_dir()))
    vec = store.get(int(account_id))
    return None if vec is None else vec.astype(np.float32)


def get_player_features_or_default(account_id: int | None) -> np.ndarray:
    """Convenience: lookup_player_features() with ANON_FEATS fallback."""
    if account_id is None:
        return ANON_FEATS.copy()
    pf = lookup_player_features(account_id)
    if pf is None:
        return ANON_FEATS.copy()
    return pf


# ----- Hero popularity (for masked-slot sampling) -----


def hero_pick_distribution() -> np.ndarray:
    """(151,) empirical hero-pick prior for sampling unknown hero slots in
    partial-draft queries. Loaded from the registry-resident artifact
    (precomputed), not the val parquet."""
    return artifacts.load_hero_prior(str(paths.live_model_dir()))


def sample_unknown_heroes(n_slots: int,
                            exclude: set[int] | None = None,
                            rng: np.random.Generator | None = None) -> list[int]:
    """Sample n_slots heroes from the empirical pick distribution, excluding
    any hero in `exclude`. Used to fill unknown ally/enemy slots during a
    partial-draft hero-pick recommendation sweep.

    Returns a list of n_slots hero IDs WITHOUT replacement.
    """
    if rng is None:
        rng = np.random.default_rng()
    if exclude is None:
        exclude = set()
    p = hero_pick_distribution().copy()
    for hid in exclude:
        if 0 <= int(hid) < 151:
            p[int(hid)] = 0.0
    # Re-normalize
    s = p.sum()
    if s <= 0:
        return [int(rng.integers(1, 151)) for _ in range(n_slots)]
    p = p / s
    return list(np.random.default_rng(int(rng.integers(0, 2**31))).choice(151, size=n_slots, replace=False, p=p))


__all__ = [
    "ANON_ACCOUNT_IDS",
    "hero_id_to_name", "hero_name_to_id", "hero_id_to_roles", "hero_id_to_attr",
    "hero_id_to_abilities", "hero_name", "hero_id",
    "item_id_to_info", "item_name", "item_cost",
    "item_id_to_components", "decompose_item",
    "lookup_player_features", "get_player_features_or_default",
    "hero_pick_distribution", "sample_unknown_heroes",
]
