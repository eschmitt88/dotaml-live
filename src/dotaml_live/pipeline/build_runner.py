"""Incremental feature builder — the rolling-store updater.

Walks raw match parquets from the canonical lake in chronological order, and for
each NEW day (after the aggregator watermark): parses matches, applies the same
filters as dotaml-turbo, emits player-features rows (snapshot BEFORE update — causal)
+ rich-cols rows, then updates the durable aggregator. Writes one parquet per day to
the rolling store and advances the watermark. Resumable: re-running picks up after
the last persisted day.

Reuses the vendored builders' helpers (filters, PlayerAggregator, parse_items) so
emitted features match dotaml-turbo bit-for-bit given the same aggregator history.
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import orjson
import pyarrow.parquet as pq

from ..common import config
from ..features.aggregator import DurableAggregator, is_prepatch_date
from ..features.build_features_extended import (
    enumerate_raw_files, is_forfeit, too_many_empty_inv,
    HERO_ID_MIN, HERO_ID_MAX,
)
from ..features.build_rich_cols_extended import parse_items
from . import rolling_store as rs
from . import seal_holdout

U16_MAX = 65535
U32_MAX = 4294967295
RICH_SLOT_U16 = ["kills", "deaths", "assists", "gpm", "xpm"]
RICH_SLOT_U32 = ["hero_damage", "net_worth"]
_FIELD = {"kills": "kills", "deaths": "deaths", "assists": "assists",
          "gpm": "gold_per_min", "xpm": "xp_per_min",
          "hero_damage": "hero_damage", "net_worth": "net_worth"}


def _clamp(v, hi: int) -> int:
    v = int(v or 0)
    return 0 if v < 0 else (hi if v > hi else v)


def _lake_roots() -> list[Path]:
    raw = config.pipeline_config()["raw"]
    roots = list(raw["historical_roots"]) + [raw["live_dir"]]
    return [Path(r).expanduser() for r in roots]


def _load_or_init_aggregator() -> DurableAggregator:
    p = rs.aggregator_state_path()
    if p.exists():
        agg = DurableAggregator.load(p)
        print(f"[build_runner] resumed aggregator @ watermark={agg.watermark}")
        return agg
    print("[build_runner] no aggregator state — starting fresh")
    return DurableAggregator()


def _parse_day_matches(files: list[Path]) -> list[tuple]:
    """Return sorted, deduped, filtered matches for a day: [(start_ts, mid, m)]."""
    seen: set[int] = set()
    out: list[tuple] = []
    for fp in files:
        try:
            tbl = pq.read_table(fp, columns=["match_id", "raw_json", "game_mode"])
        except Exception as e:  # noqa: BLE001
            print(f"  read fail {fp}: {e}")
            continue
        mids = tbl.column("match_id").to_numpy(zero_copy_only=False)
        gms = tbl.column("game_mode").to_numpy(zero_copy_only=False)
        jsons = tbl.column("raw_json").to_pylist()
        for i in range(len(jsons)):
            if int(gms[i]) != 23:
                continue
            mid = int(mids[i])
            if mid in seen:
                continue
            try:
                m = orjson.loads(jsons[i])
            except Exception:
                continue
            players = m.get("players")
            if not players or len(players) != 10:
                continue
            st, rw_raw = m.get("start_time"), m.get("radiant_win")
            if st is None or rw_raw is None:
                continue
            ts_r = int(m.get("tower_status_radiant", 0) or 0)
            ts_d = int(m.get("tower_status_dire", 0) or 0)
            if is_forfeit(bool(rw_raw), ts_r, ts_d) or too_many_empty_inv(players):
                continue
            seen.add(mid)
            out.append((int(st), mid, m))
    out.sort(key=lambda x: (x[0], x[1]))
    return out


def _emit_match(agg: DurableAggregator, m: dict, start_ts: int, date: str,
                split: str) -> tuple[dict, dict] | None:
    players = m["players"]
    accts = [int(p.get("account_id") or 0) for p in players]
    heroes = [int(p.get("hero_id") or 0) for p in players]
    if any(h < HERO_ID_MIN or h > HERO_ID_MAX for h in heroes):   # range from config (ADR 0007)
        return None
    rw = 1 if m["radiant_win"] else 0

    feats, src, n_anon = agg.features_for_match(accts, heroes, start_ts)
    pf_row = {"match_id": m_id(m), "radiant_win": rw, "heroes": heroes,
              "feats": feats, "src": src, "n_anon": n_anon, "split": split}

    items = [parse_items(p) for p in players]
    slot = {s: [_clamp(players[i].get(_FIELD[s]), U16_MAX) for i in range(10)]
            for s in RICH_SLOT_U16}
    for s in RICH_SLOT_U32:
        slot[s] = [_clamp(players[i].get(_FIELD[s]), U32_MAX) for i in range(10)]
    rich_row = {"match_id": m_id(m), "duration": _clamp(m.get("duration"), U32_MAX),
                "radiant_win": rw, "items": items, "slot": slot}

    agg.update_for_match(accts, heroes, rw, start_ts, is_prepatch_date(date))
    return pf_row, rich_row


def m_id(m: dict) -> int:
    return int(m["match_id"])


def run(end_date: str | None = None, max_days: int | None = None,
        save_every: int = 5) -> dict:
    """Process all new days (after the watermark) up to end_date (inclusive)."""
    agg = _load_or_init_aggregator()
    by_day = enumerate_raw_files(_lake_roots())
    all_days = sorted(by_day.keys())
    days = [d for d in all_days
            if (agg.watermark is None or d > agg.watermark)
            and (end_date is None or d <= end_date)]
    if max_days:
        days = days[:max_days]
    if not days:
        print("[build_runner] nothing to do (watermark up to date)")
        return {"days": 0, "matches": 0, "watermark": agg.watermark}

    now = days[-1]
    win = seal_holdout.compute_windows(now)
    print(f"[build_runner] {len(days)} day(s) {days[0]}..{days[-1]} | "
          f"seal: train<= {win.train_end}, val {win.val_start}..{win.val_end}, "
          f"test {win.test_start}..{win.test_end}")

    total = 0
    for di, day in enumerate(days):
        matches = _parse_day_matches(by_day[day])
        split = win.classify(day)
        pf_rows, rich_rows = [], []
        for start_ts, _mid, m in matches:
            res = _emit_match(agg, m, start_ts, day, split)
            if res is None:
                continue
            pf_rows.append(res[0])
            rich_rows.append(res[1])
        if pf_rows:
            rs.write_player_features_day(day, pf_rows)
            rs.write_rich_cols_day(day, rich_rows)
        agg.watermark = day
        total += len(pf_rows)
        print(f"  {day} [{split:>7}] {len(pf_rows):,} matches")
        if (di + 1) % save_every == 0:
            agg.save(rs.aggregator_state_path())
    agg.save(rs.aggregator_state_path())
    return {"days": len(days), "matches": total, "watermark": agg.watermark}


def main() -> None:
    ap = argparse.ArgumentParser(description="Incremental rolling-store feature builder")
    ap.add_argument("--end-date", default=None, help="process days up to this YYYY-MM-DD")
    ap.add_argument("--max-days", type=int, default=None, help="cap number of days this run")
    args = ap.parse_args()
    stats = run(end_date=args.end_date, max_days=args.max_days)
    print(f"[build_runner] done: {stats}")


if __name__ == "__main__":
    main()
