"""Precompute the hero-combo discovery table (all pairs) for a model version.

Combos are draft-independent (intrinsic pair synergy + kills/min on a generic team),
so they're computed once and served as a static table the dashboard sorts/filters
client-side. Writes registry/<version>/hero_combos.json.

  synergy(A,B) = P(win | A,B together) - [P(win|A) + P(win|B) - base]   (interaction)
  kpm(A,B)     = predicted (kills+assists)/min for the pair, averaged over sampled
                 ally/enemy fills (batched form of queries.kills_per_minute_pair)

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


def synergy_all(f: V7Foundation, heroes: list[int], pairs: list[tuple[int, int]]) -> np.ndarray:
    singles = [()] + [(h,) for h in heroes]
    sp = _radiant_winprob_batch(f, singles)
    base = float(sp[0])
    lift = {h: float(sp[i + 1]) - base for i, h in enumerate(heroes)}
    joint = _radiant_winprob_batch(f, pairs)
    return np.array([joint[k] - base - (lift[a] + lift[b]) for k, (a, b) in enumerate(pairs)])


@torch.no_grad()
def kpm_all(f: V7Foundation, pairs: list[tuple[int, int]],
            n_samples: int = 6, seed: int = 42) -> np.ndarray:
    """Batched kills/min for every pair (pair-major rows -> reshape to average)."""
    rng = np.random.default_rng(seed)
    rows_hero: list[list[int]] = []
    rows_slots: list[list[int]] = []
    for a, b in pairs:
        locked = {a, b}
        for _ in range(n_samples):
            ally = sample_unknown_heroes(3, exclude=locked, rng=rng)
            enemy = sample_unknown_heroes(5, exclude=locked | set(ally), rng=rng)
            radiant_5 = [a, b] + list(ally)
            dire_5 = list(enemy)
            r_arg = sorted(range(5), key=lambda i: radiant_5[i])
            d_arg = sorted(range(5), key=lambda i: dire_5[i])
            rows_hero.append([radiant_5[i] for i in r_arg] + [dire_5[i] for i in d_arg])
            rows_slots.append([r_arg.index(0), r_arg.index(1)])   # where a,b landed
    hero_ids = np.array(rows_hero, dtype=np.int64)
    N = len(rows_hero)
    kpm_row = np.empty(N, dtype=np.float64)
    for s in range(0, N, CHUNK):
        e = min(s + CHUNK, N)
        B = e - s
        inp = f.empty_inputs(batch_size=B)              # player_feats default to ANON
        inp["hero_ids"] = torch.from_numpy(hero_ids[s:e]).to(f.device)
        out = f.predict(inputs=inp, masks=f.pure_pregame_mask(batch_size=B))
        k = out.kills().cpu().numpy()
        asst = out.assists().cpu().numpy()
        dm = np.maximum(out.dur_seconds().cpu().numpy() / 60.0, 1.0)
        for j in range(B):
            sl = rows_slots[s + j]
            kpm_row[s + j] = (k[j, sl].sum() + asst[j, sl].sum()) / dm[j]
    return kpm_row.reshape(len(pairs), n_samples).mean(axis=1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default=None, help="default: live model dir")
    ap.add_argument("--n-samples", type=int, default=6)
    args = ap.parse_args()
    model_dir = Path(args.model_dir) if args.model_dir else paths.live_model_dir()

    f = V7Foundation(model_dir=model_dir)
    names = hero_id_to_name()
    attr = hero_id_to_attr()
    heroes = sorted(h for h in names if 1 <= h <= 150)   # model vocab is ids 0..150
    pairs = list(itertools.combinations(heroes, 2))
    print(f"[combos] {len(heroes)} heroes -> {len(pairs):,} pairs on {f.device}")

    t = time.time()
    syn = synergy_all(f, heroes, pairs)
    print(f"[combos] synergy done ({time.time() - t:.1f}s)")
    t = time.time()
    kpm = kpm_all(f, pairs, n_samples=args.n_samples)
    print(f"[combos] kills/min done ({time.time() - t:.1f}s)")

    combos = [{
        "a": a, "b": b, "a_name": names[a], "b_name": names[b],
        "a_attr": attr.get(a, "?"), "b_attr": attr.get(b, "?"),
        "synergy": round(float(syn[k]), 4), "kpm": round(float(kpm[k]), 3),
    } for k, (a, b) in enumerate(pairs)]

    out = {"computed": True, "version": model_dir.name, "n_heroes": len(heroes),
           "n_pairs": len(pairs), "combos": combos}
    dest = paths.combos_table_json(model_dir)
    dest.write_text(json.dumps(out))
    print(f"[combos] wrote {dest} ({dest.stat().st_size / 1e6:.1f} MB, {len(combos):,} pairs)")
    top = sorted(combos, key=lambda c: -c["synergy"])[:5]
    print("[combos] top-5 synergy:")
    for c in top:
        print(f"    {c['a_name']} + {c['b_name']}: syn={c['synergy']:+.4f} kpm={c['kpm']:.2f}")


if __name__ == "__main__":
    main()
