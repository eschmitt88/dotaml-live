"""Walk-forward holdout sealing (ADR 0002).

At cycle time, the freshest window is sealed as TEST (the promotion signal), the
block before it as VAL, with an embargo gap between train-end and val/test-start
(the aggregator carries player history forward, so adjacent-day labels could leak).
Everything older is TRAIN. Widths/embargo come from splits.yaml:walk_forward.

This module only computes/records the date windows; it does not move data. The
aggregator processes every day chronologically regardless of label.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, asdict
from pathlib import Path

from ..common import config, paths


@dataclass
class Windows:
    now: str
    train_end: str
    val_start: str
    val_end: str
    test_start: str
    test_end: str
    embargo_days: int

    def classify(self, date_str: str) -> str:
        d = dt.date.fromisoformat(date_str)
        if d >= _d(self.test_start) and d <= _d(self.test_end):
            return "test"
        if d >= _d(self.val_start) and d <= _d(self.val_end):
            return "val"
        if d <= _d(self.train_end):
            return "train"
        return "embargo"     # the gap day(s); excluded from all splits


def _d(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def _s(d: dt.date) -> str:
    return d.isoformat()


def compute_windows(now_date: str | dt.date) -> Windows:
    pol = config.splits_policy()["walk_forward"]
    now = _d(now_date) if isinstance(now_date, str) else now_date
    test_days = int(pol["test_days"])
    val_days = int(pol["val_days"])
    emb = int(pol["embargo_days"])

    test_start = now - dt.timedelta(days=test_days - 1)
    # embargo gap between val and test, and between train and val
    val_end = test_start - dt.timedelta(days=1 + emb)
    val_start = val_end - dt.timedelta(days=val_days - 1)
    train_end = val_start - dt.timedelta(days=1 + emb)
    return Windows(now=_s(now), train_end=_s(train_end),
                   val_start=_s(val_start), val_end=_s(val_end),
                   test_start=_s(test_start), test_end=_s(now), embargo_days=emb)


def seal(now_date: str | dt.date, cycle_dir: str | Path | None = None) -> Windows:
    """Compute and persist the cycle's sealed windows to cycle metadata.
    Returns the Windows. The TEST window is off-limits to search-phase code."""
    w = compute_windows(now_date)
    if cycle_dir is not None:
        cycle_dir = Path(cycle_dir)
        cycle_dir.mkdir(parents=True, exist_ok=True)
        (cycle_dir / "windows.json").write_text(json.dumps(asdict(w), indent=2))
    return w


def frozen_anchor() -> dict | None:
    """Removed in ADR 0005 (a frozen pre-patch slice can wrongly block patch
    adaptation). Returns None when no frozen_anchor is configured."""
    return config.splits_policy().get("frozen_anchor")


# ----- prequential (test-then-train) evaluation — ADR 0004 -----


@dataclass
class Prequential:
    now: str
    train_cutoff: str        # candidate trains through this (inclusive) for the gate
    eval_start: str          # first unseen eval day
    eval_end: str            # = now

    def eval_dates(self) -> list[str]:
        d, out = _d(self.eval_start), []
        while d <= _d(self.eval_end):
            out.append(_s(d)); d += dt.timedelta(days=1)
        return out


def prequential_window(now: str | dt.date, eval_days: int | None = None) -> Prequential:
    """The most recent `eval_days` days form the prequential eval window (scored on
    data the candidate hasn't trained on); the candidate trains through the day before.
    """
    if eval_days is None:
        eval_days = int(config.splits_policy()["prequential"]["eval_days"])
    now = _d(now) if isinstance(now, str) else now
    eval_start = now - dt.timedelta(days=eval_days - 1)
    train_cutoff = eval_start - dt.timedelta(days=1)
    return Prequential(now=_s(now), train_cutoff=_s(train_cutoff),
                       eval_start=_s(eval_start), eval_end=_s(now))
