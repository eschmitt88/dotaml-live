"""Promotion gate (ADR 0002): head-to-head + probes + frozen-anchor tripwire.

A candidate is promoted to 'live' only if it
  (a) BEATS the current live model on the same freshly-sealed window (by a margin),
  (b) PASSES the model's probe thresholds, and
  (c) does NOT regress on the frozen anchor beyond tolerance.

`decide()` is the pure gate logic (fully unit-testable). `evaluate_win_auc()` scores
a model's pure-pregame win AUC on a set of rolling-store day parquets — used to score
BOTH incumbent and candidate on the identical window for the head-to-head.
"""

from __future__ import annotations

import glob
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..common import config


# ----- pure gate logic -----


@dataclass
class GateInput:
    fresh_auc: float                       # win AUC on the freshly-sealed window
    probes: dict[str, float] = field(default_factory=dict)
    anchor_auc: float | None = None


@dataclass
class GateResult:
    promote: bool
    reasons: list[str]
    detail: dict


def _probes_pass(probes: dict[str, float], halt_thresholds: dict) -> tuple[bool, list[str]]:
    """A probe FAILS if it crosses its halt threshold in the bad direction."""
    fails = []
    for name, spec in (halt_thresholds or {}).items():
        if name not in probes:
            continue
        v, thr, direction = probes[name], spec["value"], spec["direction"]
        if direction == "below" and v <= thr:
            fails.append(f"{name}={v:.4f}<=halt {thr}")
        elif direction == "above" and v >= thr:
            fails.append(f"{name}={v:.4f}>=halt {thr}")
    return (len(fails) == 0), fails


def decide(candidate: GateInput, incumbent: GateInput | None,
           promo_cfg: dict, halt_thresholds: dict) -> GateResult:
    reasons: list[str] = []
    detail: dict = {}

    probes_ok, probe_fails = _probes_pass(candidate.probes, halt_thresholds)
    detail["probe_fails"] = probe_fails
    if promo_cfg.get("require_probes_pass", True) and not probes_ok:
        reasons.append(f"probe thresholds failed: {probe_fails}")

    if incumbent is None:
        # First model ever — no head-to-head possible; gate on probes only.
        promote = probes_ok or not promo_cfg.get("require_probes_pass", True)
        reasons.append("no incumbent — bootstrap promotion" if promote
                       else "no incumbent but probes failed")
        return GateResult(promote, reasons, detail)

    margin = float(promo_cfg.get("beat_incumbent_margin", 0.0))
    beats = candidate.fresh_auc >= incumbent.fresh_auc + margin
    detail["fresh_auc"] = {"candidate": candidate.fresh_auc,
                           "incumbent": incumbent.fresh_auc, "margin": margin}
    if not beats:
        reasons.append(f"did not beat incumbent: {candidate.fresh_auc:.4f} < "
                       f"{incumbent.fresh_auc:.4f}+{margin}")

    anchor_ok = True
    if candidate.anchor_auc is not None and incumbent.anchor_auc is not None:
        max_reg = float(promo_cfg.get("anchor_max_regression", 1.0))
        anchor_ok = candidate.anchor_auc >= incumbent.anchor_auc - max_reg
        detail["anchor"] = {"candidate": candidate.anchor_auc,
                            "incumbent": incumbent.anchor_auc, "max_regression": max_reg}
        if not anchor_ok:
            reasons.append(f"frozen-anchor regression: {candidate.anchor_auc:.4f} < "
                           f"{incumbent.anchor_auc:.4f}-{max_reg}")

    gate_probes = probes_ok or not promo_cfg.get("require_probes_pass", True)
    promote = beats and anchor_ok and gate_probes
    if promote:
        reasons.append("promoted: beats incumbent, probes pass, anchor held")
    return GateResult(promote, reasons, detail)


# ----- head-to-head evaluator over the rolling store -----


def _canonical_sort_batch(heroes: np.ndarray, feats: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Sort each team's 5 heroes ascending (matches v7 canonical_hero_sort) and
    permute the per-slot player features in lockstep. heroes [N,10], feats [N,10,8]."""
    N = heroes.shape[0]
    out_h = heroes.copy()
    out_f = feats.copy()
    for team, sl in ((0, slice(0, 5)), (1, slice(5, 10))):
        block = heroes[:, sl]
        order = np.argsort(block, axis=1, kind="stable")
        rows = np.arange(N)[:, None]
        out_h[:, sl] = block[rows, order]
        out_f[:, sl, :] = feats[:, sl, :][rows, order]
    return out_h, out_f


def _score_winauc(f, day_files: list[str], batch: int = 8192) -> tuple[float, int]:
    """Pure-pregame win AUC + row count for a loaded model over day files."""
    import torch
    import pyarrow as pa
    import pyarrow.parquet as pq
    from sklearn.metrics import roc_auc_score
    from ..model.v7_inference import FEAT_NAMES

    if not day_files:
        return float("nan"), 0
    tbl = pa.concat_tables([pq.read_table(p) for p in day_files])
    N = tbl.num_rows
    hero_cols = [f"{t}{j}" for t in ("r", "d") for j in range(5)]
    heroes = np.stack([tbl[c].to_numpy() for c in hero_cols], axis=1).astype(np.int64)
    feats = np.stack([np.stack([tbl[f"p{s}_{fn}"].to_numpy() for fn in FEAT_NAMES], axis=1)
                      for s in range(10)], axis=1).astype(np.float32)  # [N,10,8]
    y = tbl["radiant_win"].to_numpy().astype(np.int64)
    heroes, feats = _canonical_sort_batch(heroes, feats)

    probs = np.empty(N, dtype=np.float64)
    for s in range(0, N, batch):
        e = min(s + batch, N)
        B = e - s
        inp = f.empty_inputs(batch_size=B)
        inp["hero_ids"] = torch.from_numpy(heroes[s:e]).to(f.device)
        inp["player_feats"] = torch.from_numpy(feats[s:e]).to(f.device)
        out = f.predict(inputs=inp, masks=f.pure_pregame_mask(batch_size=B))
        probs[s:e] = out.win_prob().cpu().numpy()
    return float(roc_auc_score(y, probs)), N


def evaluate_win_auc(model_dir: Path, day_files: list[str], batch: int = 8192) -> float:
    """Pure-pregame win AUC for a model over rolling-store player_features day files."""
    if not day_files:
        return float("nan")
    from ..model.v7_inference import V7Foundation
    return _score_winauc(V7Foundation(model_dir=model_dir), day_files, batch)[0]


def prequential_backtest(model_dir: Path, pf_dir: Path, dates: list[str]) -> list[dict]:
    """Score a model on each day INDEPENDENTLY (test-then-train, ADR 0004). Honest only
    for days after the model's training cutoff. Loads the model once. Returns a series
    of {date, auc, n}."""
    from ..model.v7_inference import V7Foundation
    f = V7Foundation(model_dir=model_dir)
    out = []
    for d in dates:
        fp = pf_dir / f"date={d}.parquet"
        if not fp.exists():
            continue
        auc, n = _score_winauc(f, [str(fp)])
        out.append({"date": d, "auc": round(auc, 4), "n": n})
    return out


def window_day_files(pf_dir: Path, lo: str, hi: str) -> list[str]:
    return sorted(p for p in glob.glob(str(pf_dir / "date=*.parquet"))
                  if lo <= Path(p).stem.split("=", 1)[1] <= hi)


def promotion_cfg() -> dict:
    return config.training_config()["promotion"]
