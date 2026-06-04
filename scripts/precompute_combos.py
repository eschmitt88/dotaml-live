"""Precompute the hero-combo discovery table (pairs + trios) for a model version.

Combos are draft-independent (intrinsic synergy + kills/min on a generic team), so
they're computed once and served as a static table the dashboard sorts/filters
client-side. Writes registry/<version>/hero_combos.json.

  synergy(S) = P(win | all of S together) - [ base + sum_h lift(h) ]   (interaction)
  kpm(S)     = predicted (kills+assists)/min for S, averaged over sampled fills

PAIRS: all C(n,2) get both synergy + kpm (small enough). TRIOS: C(n,3) is ~325k, so
we score synergy for all, then keep the union of (top-N global) and (top-K per hero)
by synergy — enough for browsing the best trios AND filtering by any single hero —
and compute kpm only for that reduced set. Unified row format: {ids,names,attrs,
synergy,kpm}.

Run after a model is promoted (or for v7-base): python scripts/precompute_combos.py
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dotaml_live.common import paths  # noqa: E402
from dotaml_live.model import V7Foundation  # noqa: E402
from dotaml_live.queries.hero_combos import _radiant_winprob_batch  # noqa: E402
from dotaml_live.queries.lookups import (  # noqa: E402
    hero_id_to_attr, hero_id_to_name, sample_unknown_heroes,
)

CHUNK = 8192
TRIO_TOP_GLOBAL = 600        # keep this many best trios overall
TRIO_TOP_PER_HERO = 40       # plus this many per hero (so single-hero filters work)


def base_and_lift(f: V7Foundation, heroes: list[int]) -> tuple[float, dict[int, float]]:
    singles = [()] + [(h,) for h in heroes]
    sp = _radiant_winprob_batch(f, singles)
    base = float(sp[0])
    return base, {h: float(sp[i + 1]) - base for i, h in enumerate(heroes)}


def synergy_scores(f: V7Foundation, subsets: list[tuple[int, ...]],
                   base: float, lift: dict[int, float]) -> np.ndarray:
    joint = _radiant_winprob_batch(f, subsets)
    return np.array([joint[k] - base - sum(lift[h] for h in s) for k, s in enumerate(subsets)])


@torch.no_grad()
def kpm_subsets(f: V7Foundation, subsets: list[tuple[int, ...]],
                n_samples: int, seed: int = 42) -> np.ndarray:
    """Batched kills/min for each subset (size 1-5), pair/trio-agnostic."""
    rng = np.random.default_rng(seed)
    rows_hero: list[list[int]] = []
    rows_slots: list[list[int]] = []
    for s in subsets:
        k = len(s)
        locked = set(s)
        for _ in range(n_samples):
            ally = sample_unknown_heroes(5 - k, exclude=locked, rng=rng)
            enemy = sample_unknown_heroes(5, exclude=locked | set(ally), rng=rng)
            radiant_5 = list(s) + list(ally)
            dire_5 = list(enemy)
            r_arg = sorted(range(5), key=lambda i: radiant_5[i])
            d_arg = sorted(range(5), key=lambda i: dire_5[i])
            rows_hero.append([radiant_5[i] for i in r_arg] + [dire_5[i] for i in d_arg])
            rows_slots.append([r_arg.index(i) for i in range(k)])
    hero_ids = np.array(rows_hero, dtype=np.int64)
    N = len(rows_hero)
    kpm_row = np.empty(N, dtype=np.float64)
    for s in range(0, N, CHUNK):
        e = min(s + CHUNK, N)
        B = e - s
        inp = f.empty_inputs(batch_size=B)
        inp["hero_ids"] = torch.from_numpy(hero_ids[s:e]).to(f.device)
        out = f.predict(inputs=inp, masks=f.pure_pregame_mask(batch_size=B))
        k = out.kills().cpu().numpy()
        asst = out.assists().cpu().numpy()
        dm = np.maximum(out.dur_seconds().cpu().numpy() / 60.0, 1.0)
        for j in range(B):
            sl = rows_slots[s + j]
            kpm_row[s + j] = (k[j, sl].sum() + asst[j, sl].sum()) / dm[j]
    return kpm_row.reshape(len(subsets), n_samples).mean(axis=1)


def rows_for(subsets, syn, kpm, names, attr) -> list[dict]:
    return [{
        "ids": list(s),
        "names": [names[h] for h in s],
        "attrs": [attr.get(h, "?") for h in s],
        "synergy": round(float(syn[k]), 4),
        "kpm": round(float(kpm[k]), 3),
    } for k, s in enumerate(subsets)]


def select_trios(trios, syn_t) -> list[int]:
    sel = set(np.argsort(syn_t)[::-1][:TRIO_TOP_GLOBAL].tolist())
    by_hero: dict[int, list[int]] = {}
    for i, t in enumerate(trios):
        for h in t:
            by_hero.setdefault(h, []).append(i)
    for h, idxs in by_hero.items():
        sel.update(sorted(idxs, key=lambda i: -syn_t[i])[:TRIO_TOP_PER_HERO])
    return sorted(sel)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default=None)
    ap.add_argument("--pair-samples", type=int, default=6)
    ap.add_argument("--trio-samples", type=int, default=4)
    args = ap.parse_args()
    model_dir = Path(args.model_dir) if args.model_dir else paths.live_model_dir()

    f = V7Foundation(model_dir=model_dir)
    names, attr = hero_id_to_name(), hero_id_to_attr()
    heroes = sorted(h for h in names if 1 <= h <= 150)   # model vocab is ids 0..150
    base, lift = base_and_lift(f, heroes)

    # pairs (full)
    pairs = list(itertools.combinations(heroes, 2))
    t = time.time()
    syn_p = synergy_scores(f, pairs, base, lift)
    kpm_p = kpm_subsets(f, pairs, args.pair_samples)
    print(f"[combos] {len(pairs):,} pairs done ({time.time() - t:.1f}s)")

    # trios (synergy for all, kpm for the kept subset)
    trios = list(itertools.combinations(heroes, 3))
    t = time.time()
    syn_t_all = synergy_scores(f, trios, base, lift)
    keep = select_trios(trios, syn_t_all)
    trios_k = [trios[i] for i in keep]
    syn_t = syn_t_all[keep]
    kpm_t = kpm_subsets(f, trios_k, args.trio_samples)
    print(f"[combos] {len(trios):,} trios scored, kept {len(trios_k):,} "
          f"(top-{TRIO_TOP_GLOBAL} + top-{TRIO_TOP_PER_HERO}/hero) ({time.time() - t:.1f}s)")

    out = {
        "computed": True, "version": model_dir.name, "n_heroes": len(heroes),
        "n_pairs": len(pairs), "n_trios_scored": len(trios), "n_trios_kept": len(trios_k),
        "combos": rows_for(pairs, syn_p, kpm_p, names, attr),
        "trios": rows_for(trios_k, syn_t, kpm_t, names, attr),
    }
    dest = paths.combos_table_json(model_dir)
    dest.write_text(json.dumps(out))
    print(f"[combos] wrote {dest} ({dest.stat().st_size / 1e6:.1f} MB)")
    for label, arr in (("pair", out["combos"]), ("trio", out["trios"])):
        top = sorted(arr, key=lambda c: -c["synergy"])[:3]
        print(f"[combos] top-3 {label} synergy: " +
              " | ".join(f"{'+'.join(c['names'])} ({c['synergy']*100:+.1f}%,{c['kpm']:.2f})" for c in top))


if __name__ == "__main__":
    main()
