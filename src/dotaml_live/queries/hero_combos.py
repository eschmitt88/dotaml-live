"""Dashboard query 4 — top hero combos (pair/trio).

Two scoring modes:

- ``synergy`` — rank combos by the **team win-probability lift over the heroes'
  independent baseline**. Using v7's trained partial-draft path (hero-mask token,
  all post-game masked), we measure, for a combo C placed together on one team:

      base        = P(win | empty team, all masked)              ~ 0.5
      lift(h)     = P(win | {h} on team)            - base        (single-hero effect)
      joint(C)    = P(win | C on team)              - base        (combined effect)
      synergy(C)  = joint(C) - sum_{h in C} lift(h)

  Positive synergy means the heroes are worth more *together* than the sum of their
  individual contributions — the interaction effect, exactly "lift vs the heroes'
  independent baseline."

- ``kills_per_min`` — rank combos by predicted kills+assists per minute, reusing
  the validated ``queries.kills_per_minute_pair`` estimator over each combo.

Synergy mode is fully batched (all combos in chunked forward passes); kills mode
loops the per-combo estimator, so it caps the candidate pool and logs truncation.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import numpy as np
import torch

from ..model.v7_inference import V7Foundation
from . import queries
from .lookups import hero_name, hero_pick_distribution

# Synergy forwards are cheap; cap pool sizes so the dashboard stays snappy.
_MAX_TRIO_POOL_DEFAULT = 40     # C(40,3) = 9880 combos in one chunked sweep
_KILLS_POOL_CAP = 20           # kills mode loops a ~120ms estimator per combo
_CHUNK = 4096                  # forward-pass batch size


@dataclass
class HeroCombo:
    heroes: tuple[int, ...]
    hero_names: tuple[str, ...]
    score: float                       # synergy lift, or kills/min, per `mode`
    # synergy-mode breakdown (None in kills mode):
    joint_winprob: float | None = None
    independent_baseline: float | None = None
    # kills-mode breakdown (None in synergy mode):
    kills_per_min: float | None = None


def _radiant_subset_row(subset: tuple[int, ...]) -> tuple[list[int], list[bool]]:
    """One row: place `subset` (1-5 heroes) on radiant unmasked, everything else
    masked. Canonical sort by (is_masked, hero_id) to match v7 training order."""
    n = len(subset)
    radiant_5 = list(subset) + [0] * (5 - n)
    radiant_mask = [False] * n + [True] * (5 - n)
    dire_5 = [0] * 5
    dire_mask = [True] * 5
    r_arg = sorted(range(5), key=lambda i: (radiant_mask[i], radiant_5[i]))
    d_arg = sorted(range(5), key=lambda i: (dire_mask[i], dire_5[i]))
    hero_ids = [radiant_5[i] for i in r_arg] + [dire_5[i] for i in d_arg]
    hero_mask = [radiant_mask[i] for i in r_arg] + [dire_mask[i] for i in d_arg]
    return hero_ids, hero_mask


@torch.no_grad()
def _radiant_winprob_batch(f: V7Foundation, subsets: list[tuple[int, ...]]) -> np.ndarray:
    """P(radiant_win) for each subset placed (masked) on radiant. Chunked."""
    out = np.empty(len(subsets), dtype=np.float64)
    for start in range(0, len(subsets), _CHUNK):
        chunk = subsets[start:start + _CHUNK]
        N = len(chunk)
        hero_ids_np = np.zeros((N, 10), dtype=np.int64)
        hero_mask_np = np.zeros((N, 10), dtype=bool)
        for i, sub in enumerate(chunk):
            hids, hmask = _radiant_subset_row(sub)
            hero_ids_np[i] = hids
            hero_mask_np[i] = hmask
        inputs = f.empty_inputs(batch_size=N)
        inputs["hero_ids"] = torch.from_numpy(hero_ids_np).to(f.device)
        masks = f.pure_pregame_mask(batch_size=N)
        masks["hero"] = torch.from_numpy(hero_mask_np).to(f.device)
        winp = f.predict(inputs=inputs, masks=masks).win_prob().cpu().numpy()
        out[start:start + N] = winp
    return out


def _default_pool(size: int, pool: list[int] | None) -> list[int]:
    if pool is not None:
        return [int(h) for h in pool]
    if size == 2:
        return list(range(1, 151))
    # trios: cap to the most-picked heroes so the sweep stays bounded
    prior = hero_pick_distribution()
    top = np.argsort(prior)[::-1]
    return [int(h) for h in top if 1 <= int(h) < 151][:_MAX_TRIO_POOL_DEFAULT]


def hero_combos(f: V7Foundation,
                pool: list[int] | None = None,
                size: int = 2,
                mode: str = "synergy",
                top_k: int = 15) -> list[HeroCombo]:
    """Top hero combos of `size` (2 or 3) ranked by `mode`
    ('synergy' | 'kills_per_min'). `pool` restricts the candidate heroes
    (default: all heroes for pairs; top-popularity heroes for trios)."""
    assert size in (2, 3), "size must be 2 (pair) or 3 (trio)"
    assert mode in ("synergy", "kills_per_min")
    cand = _default_pool(size, pool)

    if mode == "synergy":
        return _synergy_combos(f, cand, size, top_k)
    return _kills_combos(f, cand, size, top_k)


def _synergy_combos(f: V7Foundation, cand: list[int], size: int, top_k: int) -> list[HeroCombo]:
    # base (empty team) + every single-hero lift, in one batch
    singles_subsets: list[tuple[int, ...]] = [()] + [(h,) for h in cand]
    sp = _radiant_winprob_batch(f, singles_subsets)
    base = float(sp[0])
    lift = {h: float(sp[i + 1]) - base for i, h in enumerate(cand)}

    combos = list(combinations(cand, size))
    joint = _radiant_winprob_batch(f, combos)

    out: list[HeroCombo] = []
    for c, jw in zip(combos, joint):
        indep = base + sum(lift[h] for h in c)        # additive-from-singles baseline
        synergy = float(jw) - indep
        out.append(HeroCombo(
            heroes=c, hero_names=tuple(hero_name(h) for h in c),
            score=synergy, joint_winprob=float(jw), independent_baseline=indep))
    out.sort(key=lambda x: -x.score)
    return out[:top_k]


def _kills_combos(f: V7Foundation, cand: list[int], size: int, top_k: int) -> list[HeroCombo]:
    if len(cand) > _KILLS_POOL_CAP:
        # Loop estimator is ~120ms/combo; keep the pool bounded and say so.
        prior = hero_pick_distribution()
        cand = sorted(cand, key=lambda h: -prior[h])[:_KILLS_POOL_CAP]
        print(f"[hero_combos] kills_per_min pool capped to {_KILLS_POOL_CAP} "
              f"most-picked heroes ({len(list(combinations(cand, size)))} combos)")
    out: list[HeroCombo] = []
    for c in combinations(cand, size):
        res = queries.kills_per_minute_pair(f, hero_subset=list(c))
        out.append(HeroCombo(
            heroes=c, hero_names=tuple(hero_name(h) for h in c),
            score=float(res.kills_per_min), kills_per_min=float(res.kills_per_min)))
    out.sort(key=lambda x: -x.score)
    return out[:top_k]
