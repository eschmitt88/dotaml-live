"""Served priors — the registry-resident replacements for the prototype's
runtime val-parquet reads.

The dotaml-turbo `serve/` prototypes read the validation parquet at request time
for three things: the global duration PMF (build_optimizer), the empirical
hero-pick prior (lookups), and per-account player features (lookups). A production
service must not touch a research-repo parquet on every request, so each is
precomputed once into the model's registry directory (see
`scripts/bootstrap_from_snapshot.py`) and loaded from there.

All loaders are keyed by the model directory and cached, so the live model's
artifacts load once and stay resident.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from ..common import paths


# ----- duration PMF (stored as a fine empirical CDF, re-binned per request) -----


@lru_cache(maxsize=4)
def _load_duration_cdf(model_dir_str: str) -> tuple[np.ndarray, np.ndarray]:
    d = np.load(paths.duration_pmf_npz(Path(model_dir_str)))
    return d["grid"], d["cdf"]


def duration_pmf_on_grid(model_dir: Path, t_grid: np.ndarray) -> np.ndarray:
    """Re-bin the stored fine CDF onto an arbitrary minute grid `t_grid`
    (grid points are 1-min-wide bin centers). Equivalent to the prototype's
    `_global_duration_pmf` but sourced from the artifact, not val.parquet.
    """
    grid, cdf = _load_duration_cdf(str(model_dir))
    edges = np.concatenate([[t_grid[0] - 0.5],
                            (t_grid[:-1] + t_grid[1:]) / 2,
                            [t_grid[-1] + 0.5]])
    cdf_at_edges = np.interp(edges, grid, cdf, left=0.0, right=1.0)
    pmf = np.clip(np.diff(cdf_at_edges), 0.0, None)
    s = pmf.sum()
    return pmf / s if s > 0 else pmf


# ----- hero-pick prior -----


@lru_cache(maxsize=4)
def load_hero_prior(model_dir_str: str) -> np.ndarray:
    """(151,) normalized empirical hero-pick distribution; index 0 (PAD) is 0."""
    return np.load(paths.hero_prior_npy(Path(model_dir_str)))


# ----- per-account player-feature store -----


@lru_cache(maxsize=2)
def load_player_feature_store(model_dir_str: str) -> dict[int, np.ndarray]:
    """account_id -> (8,) float32 feature vector. Empty dict if not bootstrapped."""
    p = paths.player_feature_store(Path(model_dir_str))
    if not p.exists():
        return {}
    t = pq.read_table(p)
    acct = t["account_id"].to_numpy()
    feats = np.stack([t[f"f{i}"].to_numpy() for i in range(8)], axis=1).astype(np.float32)
    return {int(a): feats[i] for i, a in enumerate(acct)}
