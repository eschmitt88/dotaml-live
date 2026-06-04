"""Day-partitioned derived-feature store + aggregator-state location.

Two stores under data/, each written one parquet per day so the rolling window
appends cheaply and prunes by deleting old day files:
  - player_features_extended/date=YYYY-MM-DD.parquet   (schema matches dotaml-turbo)
  - rich_cols_extended/date=YYYY-MM-DD.parquet

The durable aggregator state lives under aggregator_state/.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from ..common import config, paths
from ..features.build_features_extended import (
    FEAT_NAMES_PER_PLAYER, N_PLAYERS, SOURCE_COL_NAMES,
)

RICH_SLOT_U16 = ["kills", "deaths", "assists", "gpm", "xpm"]
RICH_SLOT_U32 = ["hero_damage", "net_worth"]


def _cfg() -> dict:
    return config.pipeline_config()["rolling_store"]


def store_root() -> Path:
    return paths.DATA_DIR


def pf_dir() -> Path:
    return paths.DATA_DIR / _cfg()["player_features"]


def rich_dir() -> Path:
    return paths.DATA_DIR / _cfg()["rich_cols"]


def aggregator_state_path() -> Path:
    return paths.DATA_DIR / _cfg()["aggregator_state"] / "agg.pkl"


def _day_file(d: Path, date: str) -> Path:
    return d / f"date={date}.parquet"


def written_days(which: str = "player_features") -> set[str]:
    d = pf_dir() if which == "player_features" else rich_dir()
    if not d.exists():
        return set()
    return {p.stem.split("=", 1)[1] for p in d.glob("date=*.parquet")}


# ----- player-features day table (schema parity with build_features_extended) -----


def write_player_features_day(date: str, rows: list[dict]) -> Path:
    """rows: each {match_id, radiant_win, heroes[10], feats[80], src[20],
    n_anon, split}. Written with turbo's column names + dtypes."""
    pf_dir().mkdir(parents=True, exist_ok=True)
    n = len(rows)
    arrays = {
        "match_id": pa.array(np.array([r["match_id"] for r in rows], dtype=np.int64)),
        "start_time_date": pa.array([date] * n, type=pa.string()),
        "radiant_win": pa.array(np.array([r["radiant_win"] for r in rows], dtype=np.uint8)),
    }
    for j in range(5):
        arrays[f"r{j}"] = pa.array(np.array([r["heroes"][j] for r in rows], dtype=np.uint16))
        arrays[f"d{j}"] = pa.array(np.array([r["heroes"][5 + j] for r in rows], dtype=np.uint16))
    # 80 player-feature columns (float32), in p{p}_{feat} order
    fi = 0
    for p in range(N_PLAYERS):
        for f in FEAT_NAMES_PER_PLAYER:
            arrays[f"p{p}_{f}"] = pa.array(
                np.array([r["feats"][fi] for r in rows], dtype=np.float32))
            fi += 1
    si = 0
    for p in range(N_PLAYERS):
        for s in SOURCE_COL_NAMES:
            arrays[f"p{p}_{s}"] = pa.array(
                np.array([r["src"][si] for r in rows], dtype=np.uint32))
            si += 1
    arrays["n_anonymous_in_match"] = pa.array(np.array([r["n_anon"] for r in rows], dtype=np.uint8))
    arrays["split"] = pa.array([r["split"] for r in rows], type=pa.string())
    out = _day_file(pf_dir(), date)
    pq.write_table(pa.table(arrays), out)
    return out


def write_rich_cols_day(date: str, rows: list[dict]) -> Path:
    """rows: each {match_id, duration, radiant_win, items[10][...], slot[name][10]}."""
    rich_dir().mkdir(parents=True, exist_ok=True)
    arrays = {
        "match_id": pa.array(np.array([r["match_id"] for r in rows], dtype=np.int64)),
        "duration": pa.array(np.array([r["duration"] for r in rows], dtype=np.int32)),
        "radiant_win": pa.array(np.array([r["radiant_win"] for r in rows], dtype=np.uint8)),
    }
    for p in range(N_PLAYERS):
        arrays[f"p{p}_items"] = pa.array([r["items"][p] for r in rows],
                                         type=pa.list_(pa.int32()))
        for s in RICH_SLOT_U16:
            arrays[f"p{p}_{s}"] = pa.array(
                np.array([r["slot"][s][p] for r in rows], dtype=np.int64), type=pa.uint16())
        for s in RICH_SLOT_U32:
            arrays[f"p{p}_{s}"] = pa.array(
                np.array([r["slot"][s][p] for r in rows], dtype=np.int64), type=pa.uint32())
    out = _day_file(rich_dir(), date)
    pq.write_table(pa.table(arrays), out)
    return out
