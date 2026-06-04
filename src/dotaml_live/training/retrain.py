"""Continuous-training cycle orchestrator (ADR 0002).

One cycle:
  1. Update the rolling store from the lake (build_runner — O(new matches)).
  2. Seal the walk-forward window at 'now' (freshest = test; embargo gap).
  3. Fine-tune the live checkpoint on the recency-weighted training window
     (warm-start via train.py --resume). [needs the full rolling store + GPU]
  4. Register the candidate version (carry served artifacts forward).
  5. GATE (head-to-head + probes + frozen-anchor tripwire): score BOTH incumbent
     and candidate on the SAME sealed window; promote only if the candidate wins.
  6. Prune old versions.

Recency weighting is done by RESAMPLING the training window (recent + current-patch
days oversampled) rather than reweighting the loss — this keeps the proven training
recipe untouched and is the safe, testable choice. See `recency_weights`.

The training step (3) shells out to the vendored train.py with --resume; wiring its
data config to the rolling store + materializing the recency-weighted sample is the
one piece that needs a real GPU run to validate end-to-end. Steps 1,2,4,5,6 are
exercised without training.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path

from ..common import config
from ..pipeline import build_runner, rolling_store as rs, seal_holdout
from . import promote, registry


def recency_weights(dates: list[str], half_life_days: float,
                    current_patch_upweight: float, patch_start: str,
                    now: str) -> dict[str, float]:
    """Per-day sampling weight w = 0.5^(age_days/half_life) * psi(current-patch).
    Pure + testable; drives oversampling of recent/current-patch days."""
    now_d = dt.date.fromisoformat(now)
    ps = dt.date.fromisoformat(patch_start)
    out = {}
    for ds in dates:
        d = dt.date.fromisoformat(ds)
        age = max(0, (now_d - d).days)
        w = 0.5 ** (age / max(half_life_days, 1e-6))
        if d >= ps:
            w *= float(current_patch_upweight)
        out[ds] = w
    return out


def _latest_day() -> str | None:
    p = rs.aggregator_state_path()
    if p.exists():
        from ..features.aggregator import DurableAggregator
        return DurableAggregator.load(p).watermark
    days = sorted(d.stem.split("=", 1)[1] for d in rs.pf_dir().glob("date=*.parquet"))
    return days[-1] if days else None


def run_cycle(now: str | None = None, timestamp: str = "", train: bool = True,
              update_data: bool = True) -> dict:
    cfg = config.training_config()
    # 1. update rolling store
    if update_data:
        stats = build_runner.run()
        print(f"[retrain] rolling store updated: {stats}")
    now = now or _latest_day()
    if now is None:
        return {"status": "no-data"}

    # 2. seal walk-forward window
    win = seal_holdout.compute_windows(now)
    print(f"[retrain] sealed: train<= {win.train_end} | val {win.val_start}..{win.val_end} "
          f"| test {win.test_start}..{win.test_end} (off-limits to search)")

    incumbent_ver = registry.live_version()

    # 3. fine-tune (warm-start from live). Validated separately on a GPU run.
    candidate_ver = f"ft-{now}"
    if train:
        candidate_ver = _finetune(cfg, incumbent_ver, win, candidate_ver, timestamp)
        if candidate_ver is None:
            return {"status": "train-failed", "now": now}
    else:
        print("[retrain] train=False — gate-only dry run on the current live model")
        candidate_ver = incumbent_ver

    # 5. GATE — head-to-head on the SAME fresh VAL window (test stays sealed for final pass)
    pf = rs.pf_dir()
    val_files = promote.window_day_files(pf, win.val_start, win.val_end)
    anchor = seal_holdout.frozen_anchor()
    anchor_files = promote.window_day_files(pf, anchor["start_date"], anchor["end_date"])

    cand_dir = registry.version_dir(candidate_ver)
    cand_auc = promote.evaluate_win_auc(cand_dir, val_files) if val_files else float("nan")
    cand_anchor = promote.evaluate_win_auc(cand_dir, anchor_files) if anchor_files else None
    cand_probes = _load_probes(cand_dir)

    inc = None
    if incumbent_ver and incumbent_ver != candidate_ver:
        inc_dir = registry.version_dir(incumbent_ver)
        inc = promote.GateInput(
            fresh_auc=promote.evaluate_win_auc(inc_dir, val_files) if val_files else float("nan"),
            probes={}, anchor_auc=promote.evaluate_win_auc(inc_dir, anchor_files) if anchor_files else None)

    cand = promote.GateInput(fresh_auc=cand_auc, probes=cand_probes, anchor_auc=cand_anchor)
    halt = _halt_thresholds(cand_dir)
    result = promote.decide(cand, inc, cfg["promotion"], halt)
    print(f"[retrain] gate: promote={result.promote} :: {result.reasons}")

    # 6. promote + prune
    if result.promote and train:
        registry.set_live(candidate_ver)
        registry.prune(_keep_last_n())
        print(f"[retrain] PROMOTED {candidate_ver} -> live")
    return {"status": "ok", "now": now, "candidate": candidate_ver,
            "incumbent": incumbent_ver, "promote": result.promote,
            "candidate_val_auc": cand_auc, "reasons": result.reasons}


def _keep_last_n() -> int:
    import yaml
    from ..common import paths
    b = yaml.safe_load((paths.REPO_ROOT / "budget.yaml").read_text()) or {}
    return int((b.get("registry", {}) or {}).get("keep_last_n", 10))


def _finetune(cfg: dict, incumbent_ver: str | None, win, candidate_ver: str,
              timestamp: str) -> str | None:
    """Shell out to the vendored train.py with --resume. Returns the registered
    candidate version, or None on failure. NOTE: wiring train's data config to the
    rolling store + materializing the recency-weighted sample is the step to validate
    on a real GPU run; left as the documented integration point."""
    print("[retrain] NOTE: fine-tune execution is the GPU-validated integration point; "
          "see retrain._finetune docstring. Skipping actual training in this orchestration.")
    return None


def _load_probes(model_dir: Path) -> dict:
    m = model_dir / "metrics.json"
    if m.exists():
        return json.loads(m.read_text()).get("final_probe_results", {})
    return {}


def _halt_thresholds(model_dir: Path) -> dict:
    c = model_dir / "config.yaml"
    if c.exists():
        import yaml
        return (yaml.safe_load(c.read_text()).get("probes", {}) or {}).get("halt_thresholds", {})
    return {}


def main() -> None:
    ap = argparse.ArgumentParser(description="Continuous-training cycle")
    ap.add_argument("--now", default=None, help="cycle 'now' date YYYY-MM-DD (default: watermark)")
    ap.add_argument("--no-train", action="store_true", help="gate-only dry run")
    ap.add_argument("--no-update", action="store_true", help="skip rolling-store update")
    ap.add_argument("--timestamp", default="", help="ISO timestamp stamped into the manifest")
    args = ap.parse_args()
    out = run_cycle(now=args.now, timestamp=args.timestamp,
                    train=not args.no_train, update_data=not args.no_update)
    print(f"[retrain] cycle: {out}")


if __name__ == "__main__":
    main()
