"""Precompute the hero-combo discovery table (pairs + trios) for a model version.

`build_table(model_dir)` is importable so the retrain cycle regenerates combos for a
newly-promoted model (they're model-specific); the CLI wrapper is scripts/precompute_combos.py.

PAIRS: all C(n,2) get synergy + kills/min. TRIOS: synergy for all ~325k, keep the union
of top-N global + top-K per hero, kills/min for that reduced set. Unified row format
{ids,names,attrs,synergy,kpm}.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np
import torch

from ..common import paths
from ..model import V7Foundation
from .hero_combos import _radiant_winprob_batch
from .lookups import hero_id_to_attr, hero_id_to_name, sample_unknown_heroes

CHUNK = 8192
TRIO_TOP_GLOBAL = 600
TRIO_TOP_PER_HERO = 40


def _base_and_lift(f, heroes):
    singles = [()] + [(h,) for h in heroes]
    sp = _radiant_winprob_batch(f, singles)
    base = float(sp[0])
    return base, {h: float(sp[i + 1]) - base for i, h in enumerate(heroes)}


def _synergy_scores(f, subsets, base, lift):
    joint = _radiant_winprob_batch(f, subsets)
    syn = np.array([joint[k] - base - sum(lift[h] for h in s) for k, s in enumerate(subsets)])
    return syn, joint


def _avg_winprob(f, subsets, joint_radiant):
    """Side-averaged win rate: mean of P(win | combo on radiant) (already computed
    for synergy) and P(win | combo on dire) = 1 - P(radiant_win | combo on dire)."""
    dire = 1.0 - _radiant_winprob_batch(f, subsets, side="dire")
    return (np.asarray(joint_radiant) + dire) / 2.0


@torch.no_grad()
def _kpm_subsets(f, subsets, n_samples, seed=42):
    rng = np.random.default_rng(seed)
    rows_hero, rows_slots = [], []
    for s in subsets:
        k = len(s)
        locked = set(s)
        for _ in range(n_samples):
            ally = sample_unknown_heroes(5 - k, exclude=locked, rng=rng)
            enemy = sample_unknown_heroes(5, exclude=locked | set(ally), rng=rng)
            r5, d5 = list(s) + list(ally), list(enemy)
            ra = sorted(range(5), key=lambda i: r5[i])
            da = sorted(range(5), key=lambda i: d5[i])
            rows_hero.append([r5[i] for i in ra] + [d5[i] for i in da])
            rows_slots.append([ra.index(i) for i in range(k)])
    hero_ids = np.array(rows_hero, dtype=np.int64)
    N = len(rows_hero)
    kpm = np.empty(N, dtype=np.float64)
    for s in range(0, N, CHUNK):
        e = min(s + CHUNK, N); B = e - s
        inp = f.empty_inputs(batch_size=B)
        inp["hero_ids"] = torch.from_numpy(hero_ids[s:e]).to(f.device)
        out = f.predict(inputs=inp, masks=f.pure_pregame_mask(batch_size=B))
        k = out.kills().cpu().numpy(); a = out.assists().cpu().numpy()
        dm = np.maximum(out.dur_seconds().cpu().numpy() / 60.0, 1.0)
        for j in range(B):
            sl = rows_slots[s + j]
            kpm[s + j] = (k[j, sl].sum() + a[j, sl].sum()) / dm[j]
    return kpm.reshape(len(subsets), n_samples).mean(axis=1)


def _rows(subsets, syn, kpm, avg, names, attr):
    return [{"ids": list(s), "names": [names[h] for h in s],
             "attrs": [attr.get(h, "?") for h in s],
             "synergy": round(float(syn[k]), 4), "kpm": round(float(kpm[k]), 3),
             "avg_winprob": round(float(avg[k]), 4)}
            for k, s in enumerate(subsets)]


def _select_trios(trios, syn_t):
    sel = set(np.argsort(syn_t)[::-1][:TRIO_TOP_GLOBAL].tolist())
    by_hero: dict[int, list[int]] = {}
    for i, t in enumerate(trios):
        for h in t:
            by_hero.setdefault(h, []).append(i)
    for idxs in by_hero.values():
        sel.update(sorted(idxs, key=lambda i: -syn_t[i])[:TRIO_TOP_PER_HERO])
    return sorted(sel)


def build_table(model_dir: str | Path, pair_samples: int = 6, trio_samples: int = 4) -> Path:
    """Compute pairs + trios for `model_dir` and write hero_combos.json. Busts the
    serving cache (no-op cross-process; matters when regenerating an already-loaded dir)."""
    model_dir = Path(model_dir)
    f = V7Foundation(model_dir=model_dir)
    names, attr = hero_id_to_name(), hero_id_to_attr()
    heroes = sorted(h for h in names if 1 <= h <= 150)
    base, lift = _base_and_lift(f, heroes)

    pairs = list(itertools.combinations(heroes, 2))
    syn_p, joint_p = _synergy_scores(f, pairs, base, lift)
    avg_p = _avg_winprob(f, pairs, joint_p)
    kpm_p = _kpm_subsets(f, pairs, pair_samples)

    trios = list(itertools.combinations(heroes, 3))
    syn_t_all, joint_t_all = _synergy_scores(f, trios, base, lift)
    keep = _select_trios(trios, syn_t_all)
    trios_k = [trios[i] for i in keep]
    syn_t, kpm_t = syn_t_all[keep], _kpm_subsets(f, trios_k, trio_samples)
    avg_t = _avg_winprob(f, trios_k, joint_t_all[keep])

    out = {"computed": True, "version": model_dir.name, "n_heroes": len(heroes),
           "n_pairs": len(pairs), "n_trios_scored": len(trios), "n_trios_kept": len(trios_k),
           "combos": _rows(pairs, syn_p, kpm_p, avg_p, names, attr),
           "trios": _rows(trios_k, syn_t, kpm_t, avg_t, names, attr)}
    dest = paths.combos_table_json(model_dir)
    dest.write_text(json.dumps(out))
    from . import artifacts
    artifacts.load_combos_table.cache_clear()
    return dest


def backfill_avg_winprob(model_dir: str | Path, f: V7Foundation | None = None) -> Path:
    """Add avg_winprob to a hero_combos.json built before the field existed.
    Recalculates win rates with the model (two winprob passes per row set) but
    skips the expensive kpm resampling — much cheaper than a full build_table."""
    model_dir = Path(model_dir)
    dest = paths.combos_table_json(model_dir)
    table = json.loads(dest.read_text())
    if f is None:
        f = V7Foundation(model_dir=model_dir)
    for key in ("combos", "trios"):
        rows = table.get(key) or []
        if not rows:
            continue
        subsets = [tuple(r["ids"]) for r in rows]
        avg = _avg_winprob(f, subsets, _radiant_winprob_batch(f, subsets))
        for r, a in zip(rows, avg):
            r["avg_winprob"] = round(float(a), 4)
    dest.write_text(json.dumps(table))
    from . import artifacts
    artifacts.load_combos_table.cache_clear()
    return dest
