"""Phase-1 bootstrap: precompute the served artifacts from dotaml-turbo's existing
7.40 snapshot, so the dashboard runs on the vendored v7 checkpoint with ZERO runtime
val-parquet reads (and zero cloud creds).

Reads, once, as data (ADR 0001):
  - val player_features_extended  (hero columns + p{slot}_{feat})
  - account_ids_val sidecar       (match_id -> p{slot}_account_id)
  - val rich_cols_extended        (duration)

Writes into a model registry dir (default registry/v7-base):
  - hero_prior.npy          (151,) normalized empirical pick prior
  - duration_pmf.npz        fine empirical duration CDF (grid + cdf, minutes)
  - player_features.parquet account_id -> 8-dim averaged feature vector

These are the artifacts loaded by queries/artifacts.py at serve time. This reads
val (search-split), never test — HCE-compliant.

Usage:
  python scripts/bootstrap_from_snapshot.py [--model-dir registry/v7-base] \\
         [--turbo ~/projects/research/dotaml-turbo]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dotaml_live.common import paths  # noqa: E402

FEAT_NAMES = [
    "n_games_log1p", "smoothed_winrate", "smoothed_winrate_hero", "last10_winrate",
    "days_since_last_log1p", "n_games_hero_log1p", "hero_diversity_log1p", "is_anonymous",
]
ANON_ACCOUNT_IDS = {0, 4294967295}
HERO_COLS = ["r0", "r1", "r2", "r3", "r4", "d0", "d1", "d2", "d3", "d4"]


def build_hero_prior(val_pf: Path) -> np.ndarray:
    tbl = pq.read_table(val_pf, columns=HERO_COLS)
    counts = np.zeros(151, dtype=np.float64)
    for col in HERO_COLS:
        arr = tbl[col].to_numpy()
        v = arr[(arr >= 0) & (arr < 151)].astype(np.int64)
        counts += np.bincount(v, minlength=151)
    counts += 1e-6
    counts[0] = 0.0           # never sample PAD
    return (counts / counts.sum()).astype(np.float64)


def build_duration_cdf(rc_val: Path) -> tuple[np.ndarray, np.ndarray]:
    dur_sec = pq.read_table(rc_val, columns=["duration"])["duration"].to_numpy()
    dur_min = dur_sec.astype(np.float64) / 60.0
    grid = np.arange(0.0, 120.5, 0.5)            # fine minute grid
    edges = np.concatenate([grid - 0.25, [grid[-1] + 0.25]])
    counts, _ = np.histogram(dur_min, bins=edges)
    cdf = np.cumsum(counts).astype(np.float64)
    cdf /= cdf[-1]
    return grid, cdf


def build_player_feature_store(val_pf: Path, sidecar: Path) -> pa.Table:
    """account_id -> mean 8-vec over all (match, slot) appearances in val.

    Vectorized: align the sidecar's account columns to the pf table by match_id,
    then accumulate per-account sums/counts per slot with np.add.at.
    """
    feat_cols = [f"p{s}_{f}" for s in range(10) for f in FEAT_NAMES]
    pf = pq.read_table(val_pf, columns=["match_id"] + feat_cols)
    pf_mid = pf["match_id"].to_numpy().astype(np.int64)
    order = np.argsort(pf_mid)
    pf_mid_sorted = pf_mid[order]

    side = pq.read_table(sidecar)
    side_mid = side["match_id"].to_numpy().astype(np.int64)
    pos = np.searchsorted(pf_mid_sorted, side_mid)
    pos_clipped = np.clip(pos, 0, len(pf_mid_sorted) - 1)
    matched = pf_mid_sorted[pos_clipped] == side_mid          # sidecar rows present in pf
    pf_rows_for_side = order[pos_clipped]                      # pf row index per sidecar row

    sums: dict[int, np.ndarray] = {}
    counts: dict[int, int] = {}

    for s in range(10):
        acct = side[f"p{s}_account_id"].to_numpy().astype(np.int64)
        # slot feature matrix in pf order
        F_slot = np.stack([pf[f"p{s}_{f}"].to_numpy() for f in FEAT_NAMES], axis=1).astype(np.float64)
        F = F_slot[pf_rows_for_side]                          # [M, 8] aligned to sidecar rows
        valid = matched & ~np.isin(acct, list(ANON_ACCOUNT_IDS))
        a = acct[valid]
        Fv = F[valid]
        uniq, inv = np.unique(a, return_inverse=True)
        slot_sums = np.zeros((len(uniq), 8), dtype=np.float64)
        np.add.at(slot_sums, inv, Fv)
        slot_counts = np.bincount(inv, minlength=len(uniq))
        for j, acc in enumerate(uniq):
            acc = int(acc)
            if acc in sums:
                sums[acc] += slot_sums[j]
                counts[acc] += int(slot_counts[j])
            else:
                sums[acc] = slot_sums[j].copy()
                counts[acc] = int(slot_counts[j])
        print(f"  slot {s}: {len(uniq):>8d} accounts, running total {len(sums):>8d}")

    accts = np.fromiter(sums.keys(), dtype=np.int64, count=len(sums))
    means = np.stack([sums[int(a)] / max(counts[int(a)], 1) for a in accts], axis=0).astype(np.float32)
    arrays = {"account_id": pa.array(accts)}
    for i in range(8):
        arrays[f"f{i}"] = pa.array(means[:, i])
    return pa.table(arrays)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default=str(paths.REGISTRY_DIR / "v7-base"))
    ap.add_argument("--turbo", default="~/projects/research/dotaml-turbo")
    args = ap.parse_args()

    model_dir = Path(args.model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    turbo = Path(args.turbo).expanduser()
    snap = turbo / "data" / "snapshots" / "7.40-2025-12-16" / "processed"
    val_pf = snap / "player_features_extended" / "val.parquet"
    rc_val = snap / "rich_cols_extended" / "val.parquet"
    sidecar = turbo / "experiments" / "2026-05-19-player-embedding-prelim-740" / \
        "sidecar" / "account_ids_val.parquet"

    for p in (val_pf, rc_val, sidecar):
        if not p.exists():
            raise SystemExit(f"missing bootstrap source: {p}")

    print("[1/3] hero pick prior ...")
    np.save(paths.hero_prior_npy(model_dir), build_hero_prior(val_pf))

    print("[2/3] duration CDF ...")
    grid, cdf = build_duration_cdf(rc_val)
    np.savez(paths.duration_pmf_npz(model_dir), grid=grid, cdf=cdf)

    print("[3/3] player feature store (vectorized join) ...")
    store = build_player_feature_store(val_pf, sidecar)
    pq.write_table(store, paths.player_feature_store(model_dir))

    print(f"done -> {model_dir}")
    print(f"  hero_prior.npy, duration_pmf.npz, player_features.parquet "
          f"({store.num_rows:,} accounts)")


if __name__ == "__main__":
    main()
