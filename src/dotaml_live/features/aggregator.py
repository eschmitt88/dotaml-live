"""Durable, resumable player-feature aggregator.

The crux of incremental feature-building: a match's features depend on each
account's *prior* history (lifetime/smoothed winrate, recent form, hero stats,
days-since-last). dotaml-turbo's `build_features_extended` keeps that state in RAM
for a single batch run; a live service must persist it and update it as new matches
arrive, so feature-building is O(new matches), not O(whole window).

`DurableAggregator` wraps the vendored `PlayerAggregator` verbatim (so emitted
features are bit-identical to dotaml-turbo) and adds:
  - save()/load() of the full per-account state,
  - a `watermark` (last fully-processed date) for resumable incremental runs.

IMPORTANT distinction (ADR 0002): the aggregator's *history* is cumulative and
never truncated — it defines features. The *training window* (which recent days we
train on) is a separate, downstream concern handled by seal_holdout/splits.
"""

from __future__ import annotations

import pickle
from collections import defaultdict, deque
from pathlib import Path

from .build_features_extended import (
    ANON_IDS, FEAT_NAMES_PER_PLAYER, N_PLAYERS, PlayerAggregator, patch_id_for,
)

STATE_VERSION = 1


class DurableAggregator:
    def __init__(self, agg: PlayerAggregator | None = None,
                 recent_window: int = 10, alpha: float = 5.0,
                 hero_alpha: float = 5.0, prior: float = 0.5335,
                 watermark: str | None = None):
        self.agg = agg or PlayerAggregator(recent_window, alpha, hero_alpha, prior)
        self.watermark = watermark      # last fully-processed date 'YYYY-MM-DD'

    # ----- per-match feature emission (snapshot BEFORE update — causal) -----

    def features_for_match(self, accts: list[int], heroes: list[int],
                           start_ts: int) -> tuple[list[float], list[int], int]:
        """Snapshot the 8 features for all 10 slots at match time. Returns
        (flat 80-feature row, flat 20 source-count row, n_anonymous)."""
        feat_row: list[float] = []
        src_row: list[int] = []
        n_anon = 0
        for i in range(N_PLAYERS):
            if accts[i] in ANON_IDS:
                n_anon += 1
            feats, n_pre, n_in = self.agg.snapshot(accts[i], heroes[i], start_ts)
            feat_row.extend(feats)
            src_row.extend([n_pre, n_in])
        return feat_row, src_row, n_anon

    def update_for_match(self, accts: list[int], heroes: list[int],
                         rw: int, start_ts: int, is_prepatch: bool) -> None:
        """Update aggregator state for all 10 slots AFTER emission."""
        for i in range(N_PLAYERS):
            won = rw if i < 5 else (1 - rw)
            self.agg.update(accts[i], heroes[i], won, start_ts, is_prepatch)

    # ----- persistence -----

    def _to_state(self) -> dict:
        a = self.agg
        return {
            "version": STATE_VERSION,
            "watermark": self.watermark,
            "config": {"alpha": a.alpha, "hero_alpha": a.hero_alpha,
                       "recent_window": a.recent_window, "global_prior": a.global_prior},
            "n_games": dict(a.n_games), "n_wins": dict(a.n_wins),
            "last_time": dict(a.last_time),
            "recent_wins": {k: list(v) for k, v in a.recent_wins.items()},
            "hero_n": {k: dict(v) for k, v in a.hero_n.items()},
            "hero_w": {k: dict(v) for k, v in a.hero_w.items()},
            "hero_global_n": dict(a.hero_global_n), "hero_global_w": dict(a.hero_global_w),
            "n_pre": dict(a.n_pre), "n_in": dict(a.n_in),
            "clamp_events": a.clamp_events, "clamp_by_feat": dict(a.clamp_by_feat),
        }

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "wb") as f:
            pickle.dump(self._to_state(), f, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(path)            # atomic swap — never leave a half-written state

    @classmethod
    def load(cls, path: str | Path) -> "DurableAggregator":
        with open(path, "rb") as f:
            s = pickle.load(f)
        c = s["config"]
        a = PlayerAggregator(c["recent_window"], c["alpha"], c["hero_alpha"], c["global_prior"])
        a.n_games = defaultdict(int, s["n_games"])
        a.n_wins = defaultdict(int, s["n_wins"])
        a.last_time = dict(s["last_time"])
        rw = defaultdict(lambda: deque(maxlen=a.recent_window))
        for k, v in s["recent_wins"].items():
            rw[k] = deque(v, maxlen=a.recent_window)
        a.recent_wins = rw
        hn = defaultdict(lambda: defaultdict(int))
        for k, v in s["hero_n"].items():
            hn[k] = defaultdict(int, v)
        a.hero_n = hn
        hw = defaultdict(lambda: defaultdict(int))
        for k, v in s["hero_w"].items():
            hw[k] = defaultdict(int, v)
        a.hero_w = hw
        a.hero_global_n = defaultdict(int, s["hero_global_n"])
        a.hero_global_w = defaultdict(int, s["hero_global_w"])
        a.n_pre = defaultdict(int, s["n_pre"])
        a.n_in = defaultdict(int, s["n_in"])
        a.clamp_events = s["clamp_events"]
        a.clamp_by_feat = dict(s["clamp_by_feat"])
        return cls(agg=a, watermark=s.get("watermark"))


def is_prepatch_date(date_str: str) -> bool:
    """Pre-patch-7.40 if before the patch start (drives the n_pre/n_in source split)."""
    from .build_features_extended import PATCH_START_DATE
    import datetime as dt
    return dt.date.fromisoformat(date_str) < PATCH_START_DATE
