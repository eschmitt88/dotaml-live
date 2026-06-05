"""Simulate the live continuous-training loop over the last N days to test the
prequential shadow gate end-to-end.

For each simulated "today" D (most recent N days):
  incumbent  = current live model (v7-base, then whatever got promoted)
  candidate  = warm-start fine-tune of the incumbent on recency-weighted data
               through D-1 (the prequential train_cutoff; patch-aware, 7.41 upweight)
  shadow     = score BOTH on day D (UNSEEN by the candidate) -> head-to-head
  gate       = promote candidate iff it beats the incumbent by the margin (probes
               pass trivially here; frozen anchor removed per 0005)
  if promoted, the candidate becomes the incumbent for D+1.

This is a FAST/lean version of the production fine-tune (pure-pregame win objective,
small recency-weighted sample, few steps) — enough to exercise the shadow mechanism.
Production uses the full multi-task train.py recipe via retrain._finetune.

Usage: python scripts/simulate_live.py [--days 3] [--rows 300000] [--steps 600]
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import torch
import torch.nn as nn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dotaml_live.common import config, paths  # noqa: E402
from dotaml_live.model import V7Foundation  # noqa: E402
from dotaml_live.model.v7_inference import FEAT_NAMES  # noqa: E402
from dotaml_live.features.build_features_extended import patch_id_for  # noqa: E402
from dotaml_live.pipeline import rolling_store as rs, seal_holdout  # noqa: E402
from dotaml_live.training import promote, registry  # noqa: E402
from dotaml_live.training.retrain import recency_weights  # noqa: E402

HERO_COLS = [f"{t}{j}" for t in ("r", "d") for j in range(5)]


def _day_path(date: str) -> Path:
    return rs.pf_dir() / f"date={date}.parquet"


def _all_days() -> list[str]:
    return sorted(p.stem.split("=", 1)[1] for p in rs.pf_dir().glob("date=*.parquet"))


def _canonical(heroes: np.ndarray, feats: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return promote._canonical_sort_batch(heroes, feats)


def build_finetune_batch(train_cutoff: str, n_rows: int, lookback_days: int,
                         rcfg: dict, seed: int = 0):
    """Recency-weighted sample of (heroes, feats, patch_id, win) from days <= cutoff.
    Allocates row counts per day ∝ recency weight (exp half-life × current-patch boost)."""
    rng = np.random.default_rng(seed)
    days = [d for d in _all_days() if d <= train_cutoff][-lookback_days:]
    w = recency_weights(days, rcfg["half_life_days"], rcfg["current_patch_upweight"],
                        rcfg["current_patch_start"], now=train_cutoff)
    wv = np.array([w[d] for d in days], dtype=np.float64)
    alloc = np.maximum(1, np.round(n_rows * wv / wv.sum()).astype(int))

    H, F, P, Y = [], [], [], []
    for d, k in zip(days, alloc):
        t = pq.read_table(_day_path(d), columns=HERO_COLS + ["radiant_win"]
                          + [f"p{s}_{fn}" for s in range(10) for fn in FEAT_NAMES])
        N = t.num_rows
        idx = rng.choice(N, size=min(k, N), replace=False)
        heroes = np.stack([t[c].to_numpy()[idx] for c in HERO_COLS], axis=1).astype(np.int64)
        feats = np.stack([np.stack([t[f"p{s}_{fn}"].to_numpy()[idx] for fn in FEAT_NAMES], axis=1)
                          for s in range(10)], axis=1).astype(np.float32)
        heroes, feats = _canonical(heroes, feats)
        H.append(heroes); F.append(feats)
        P.append(np.full(len(idx), patch_id_for(d), dtype=np.int64))
        Y.append(t["radiant_win"].to_numpy()[idx].astype(np.float32))
    return (np.concatenate(H), np.concatenate(F), np.concatenate(P), np.concatenate(Y))


def lean_finetune(incumbent_dir: Path, out_ver: str, train_cutoff: str,
                  n_rows: int, steps: int, batch: int = 1024, lr: float = 2e-4) -> Path:
    """Warm-start the incumbent and fine-tune the win head (pure-pregame) on the
    recency-weighted recent data, patch-aware. Saves a serveable candidate version."""
    rcfg = config.training_config()["recency"]
    f = V7Foundation(model_dir=incumbent_dir)          # warm start (loads incumbent weights)
    model, device = f.model, f.device
    model.train()
    H, Fe, P, Y = build_finetune_batch(train_cutoff, n_rows, lookback_days=90, rcfg=rcfg)
    print(f"    fine-tune set: {len(Y):,} rows through {train_cutoff} "
          f"(patch mix {dict(zip(*np.unique(P, return_counts=True)))})")
    H = torch.from_numpy(H); Fe = torch.from_numpy(Fe); P = torch.from_numpy(P); Y = torch.from_numpy(Y)

    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.0)
    bce = nn.BCEWithLogitsLoss()
    N = len(Y)
    use_amp = device.type == "cuda"
    for step in range(steps):
        sel = torch.randint(0, N, (batch,))
        hero = H[sel].to(device); feat = Fe[sel].to(device)
        pid = P[sel].to(device); y = Y[sel].to(device)
        inp = f.empty_inputs(batch_size=batch)
        inp["hero_ids"] = hero; inp["player_feats"] = feat; inp["patch_id"] = pid
        masks = f.pure_pregame_mask(batch_size=batch)
        with torch.autocast(device_type=device.type, dtype=torch.bfloat16, enabled=use_amp):
            out = model(inp["hero_ids"], inp["player_feats"], inp["items"],
                        inp["kills"], inp["deaths"], inp["assists"], inp["gpm"], inp["hd"],
                        inp["dur_log"], inp["win_idx"], masks=masks, patch_id=pid)
            loss = bce(out["win"].float(), y)
        opt.zero_grad(); loss.backward(); opt.step()
        if step % 150 == 0:
            print(f"      step {step:4d}/{steps}  loss={loss.item():.4f}")

    out_dir = registry.new_version_dir(out_ver)
    torch.save(model.state_dict(), out_dir / "model.pt")
    shutil.copy2(incumbent_dir / "config.yaml", out_dir / "config.yaml")
    shutil.copy2(incumbent_dir / "item_vocab.json", out_dir / "item_vocab.json")
    for art in ("hero_prior.npy", "duration_pmf.npz", "player_features.parquet"):
        if (incumbent_dir / art).exists():
            shutil.copy2(incumbent_dir / art, out_dir / art)
    registry.register(out_ver)
    return out_dir


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--rows", type=int, default=300_000)
    ap.add_argument("--steps", type=int, default=600)
    args = ap.parse_args()

    promo_cfg = config.training_config()["promotion"]
    all_days = _all_days()
    sim_days = all_days[-args.days:]
    incumbent_ver = registry.live_version() or "v7-base"
    print(f"=== live-loop simulation: {sim_days} (incumbent start: {incumbent_ver}) ===")

    rows = []
    for D in sim_days:
        pqd = seal_holdout.prequential_window(D, eval_days=1)   # train<=D-1, eval=[D]
        eval_file = [str(_day_path(D))]
        inc_dir = registry.version_dir(incumbent_ver)
        print(f"\n[{D}] incumbent={incumbent_ver}, train<= {pqd.train_cutoff}, eval={D}")

        t = time.time()
        cand_ver = f"sim-ft-{D}"
        cand_dir = lean_finetune(inc_dir, cand_ver, pqd.train_cutoff, args.rows, args.steps)
        inc_auc = promote.evaluate_win_auc(inc_dir, eval_file)
        cand_auc = promote.evaluate_win_auc(cand_dir, eval_file)
        cand = promote.GateInput(fresh_auc=cand_auc, probes={}, anchor_auc=None)
        inc = promote.GateInput(fresh_auc=inc_auc, probes={}, anchor_auc=None)
        res = promote.decide(cand, inc, promo_cfg, halt_thresholds={})
        print(f"    SHADOW on {D}: incumbent={inc_auc:.4f}  candidate={cand_auc:.4f}  "
              f"-> promote={res.promote}  ({time.time()-t:.0f}s)")
        # Chain the incumbent LOCALLY (don't touch the global live pointer / dashboard;
        # these lean win-only fine-tunes aren't production-quality for the other heads).
        if res.promote:
            incumbent_ver = cand_ver
        rows.append((D, incumbent_ver, inc_auc, cand_auc, res.promote))

    print("\n=== summary ===")
    print(f"{'day':<12}{'incumbent_AUC':>14}{'candidate_AUC':>15}{'promoted':>10}  new_live")
    for D, live, ia, ca, pr in rows:
        print(f"{D:<12}{ia:>14.4f}{ca:>15.4f}{str(pr):>10}  {live}")
    print(f"\nfinal live model: {registry.live_version()}")


if __name__ == "__main__":
    main()
