"""Value-parity check: compare the rolling store's player features against
dotaml-turbo's existing derived parquets on overlapping match_ids.

Only meaningful after the full history replay (the aggregator must have the same
prior history). Picks turbo's val window (where both have rows), joins on match_id,
and compares the 80 p{slot}_{feat} columns. Expect near-exact float32 agreement —
the DurableAggregator wraps the same PlayerAggregator over the same lake.
"""

from __future__ import annotations

import glob
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dotaml_live.common import paths  # noqa: E402
from dotaml_live.features.build_features_extended import (  # noqa: E402
    FEAT_NAMES_PER_PLAYER, N_PLAYERS,
)

TURBO_VAL = Path("~/projects/research/dotaml-turbo/data/snapshots/7.40-2025-12-16/"
                 "processed/player_features_extended/val.parquet").expanduser()
FEAT_COLS = [f"p{p}_{f}" for p in range(N_PLAYERS) for f in FEAT_NAMES_PER_PLAYER]
# turbo's val window
VAL_LO, VAL_HI = "2026-02-24", "2026-03-09"


def main() -> int:
    pf_dir = paths.DATA_DIR / "player_features_extended"
    mine_files = sorted(p for p in glob.glob(str(pf_dir / "date=*.parquet"))
                        if VAL_LO <= Path(p).stem.split("=", 1)[1] <= VAL_HI)
    if not mine_files:
        print(f"no rolling-store days in {VAL_LO}..{VAL_HI} yet — run the replay first")
        return 1
    mine = pa.concat_tables([pq.read_table(f) for f in mine_files])
    turbo = pq.read_table(TURBO_VAL, columns=["match_id"] + FEAT_COLS)

    mine_mid = mine["match_id"].to_numpy()
    turbo_mid = turbo["match_id"].to_numpy()
    common = np.intersect1d(mine_mid, turbo_mid)
    print(f"my val-window rows: {len(mine_mid):,} | turbo: {len(turbo_mid):,} | "
          f"overlap: {len(common):,}")
    if len(common) == 0:
        print("no overlapping match_ids")
        return 1

    mi = {int(m): i for i, m in enumerate(mine_mid)}
    ti = {int(m): i for i, m in enumerate(turbo_mid)}
    sample = common[:: max(1, len(common) // 20000)]   # cap comparison work
    max_abs = 0.0
    n_close = 0
    n_cells = 0
    for col in FEAT_COLS:
        a = mine[col].to_numpy()
        b = turbo[col].to_numpy()
        av = np.array([a[mi[int(m)]] for m in sample], dtype=np.float64)
        bv = np.array([b[ti[int(m)]] for m in sample], dtype=np.float64)
        d = np.abs(av - bv)
        max_abs = max(max_abs, float(d.max()))
        n_close += int((d <= 1e-4).sum())
        n_cells += len(d)
    frac = n_close / max(n_cells, 1)
    print(f"compared {len(sample):,} matches x {len(FEAT_COLS)} feats = {n_cells:,} cells")
    print(f"max |diff| = {max_abs:.6g} | fraction within 1e-4 = {frac:.4%}")
    ok = max_abs < 1e-3
    print("PARITY OK ✓" if ok else "PARITY MISMATCH ✗ (investigate aggregator/filters)")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
