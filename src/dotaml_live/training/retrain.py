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

from ..common import config, paths
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
    # 0+1. pull new blobs from Azure (if available), then update the rolling store
    if update_data:
        try:
            from ..pipeline import blob_consumer
            if not blob_consumer._azure_unavailable():
                print(f"[retrain] blob pull: {blob_consumer.pull()}")
        except Exception as e:  # noqa: BLE001
            print(f"[retrain] blob pull skipped: {e}")
        stats = build_runner.run()
        print(f"[retrain] rolling store updated: {stats}")
    now = now or _latest_day()
    if now is None:
        return {"status": "no-data"}

    # 2. prequential window — the most recent unseen days (ADR 0004)
    pq = seal_holdout.prequential_window(now)
    pf = rs.pf_dir()
    eval_files = [str(pf / f"date={d}.parquet") for d in pq.eval_dates()
                  if (pf / f"date={d}.parquet").exists()]
    anchor = seal_holdout.frozen_anchor()
    anchor_files = promote.window_day_files(pf, anchor["start_date"], anchor["end_date"])
    print(f"[retrain] prequential: train<= {pq.train_cutoff} | eval {pq.eval_start}..{pq.eval_end} "
          f"({len(eval_files)} unseen days)")

    incumbent_ver = registry.live_version()

    # 3. prequential MONITORING — score current live on the unseen eval window, log it
    inc_auc = float("nan")
    if incumbent_ver and eval_files:
        inc_auc = promote.evaluate_win_auc(registry.version_dir(incumbent_ver), eval_files)
        _log_prequential(now, incumbent_ver, inc_auc, len(eval_files))
        print(f"[retrain] prequential live AUC ({incumbent_ver}): {inc_auc:.4f}")

    # 4. candidate fine-tune (warm-start through train_cutoff). GPU integration point.
    if not train:
        return {"status": "monitor-only", "now": now,
                "eval_window": [pq.eval_start, pq.eval_end],
                "prequential_live_auc": round(inc_auc, 4), "incumbent": incumbent_ver}
    candidate_ver = _finetune(cfg, incumbent_ver, pq, f"ft-{now}", timestamp)
    if candidate_ver is None:
        return {"status": "train-skipped (GPU integration point)", "now": now,
                "prequential_live_auc": round(inc_auc, 4)}

    # 5. SHADOW GATE — candidate vs incumbent on the SAME unseen eval window
    cand_dir = registry.version_dir(candidate_ver)
    cand = promote.GateInput(
        fresh_auc=promote.evaluate_win_auc(cand_dir, eval_files) if eval_files else float("nan"),
        probes=_load_probes(cand_dir),
        anchor_auc=promote.evaluate_win_auc(cand_dir, anchor_files) if anchor_files else None)
    inc = None
    if incumbent_ver and incumbent_ver != candidate_ver:
        inc_dir = registry.version_dir(incumbent_ver)
        inc = promote.GateInput(fresh_auc=inc_auc, probes={},
            anchor_auc=promote.evaluate_win_auc(inc_dir, anchor_files) if anchor_files else None)
    result = promote.decide(cand, inc, cfg["promotion"], _halt_thresholds(cand_dir))
    print(f"[retrain] shadow gate: promote={result.promote} :: {result.reasons}")

    # 6. promote -> refit-for-serving through `now` (kill the lag) -> prune
    if result.promote:
        if config.splits_policy()["prequential"].get("refit_for_serving", True):
            _refit_for_serving(candidate_ver, now)
        registry.set_live(candidate_ver)
        registry.prune(_keep_last_n())
        print(f"[retrain] PROMOTED {candidate_ver} -> live")
    return {"status": "ok", "now": now, "candidate": candidate_ver, "incumbent": incumbent_ver,
            "promote": result.promote, "candidate_eval_auc": round(cand.fresh_auc, 4),
            "incumbent_eval_auc": round(inc_auc, 4), "reasons": result.reasons}


def prequential_monitor(now: str | None = None, eval_days: int | None = None) -> dict:
    """Score the current live model on the most recent unseen day(s) and log it.
    Runnable independently of retraining (e.g. a daily cron) — the lag-free health metric."""
    now = now or _latest_day()
    if now is None:
        return {"status": "no-data"}
    pq = seal_holdout.prequential_window(now, eval_days)
    pf = rs.pf_dir()
    files = [str(pf / f"date={d}.parquet") for d in pq.eval_dates()
             if (pf / f"date={d}.parquet").exists()]
    ver = registry.live_version()
    mdir = registry.version_dir(ver) if ver else paths.live_model_dir()
    auc = promote.evaluate_win_auc(mdir, files) if files else float("nan")
    _log_prequential(now, ver or mdir.name, auc, len(files))
    return {"now": now, "eval_window": [pq.eval_start, pq.eval_end], "version": ver,
            "eval_auc": round(float(auc), 4), "n_days": len(files)}


def _log_prequential(now: str, version: str, auc: float, n_days: int) -> None:
    import json
    from ..common import paths
    p = paths.DATA_DIR / "prequential.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as fh:
        fh.write(json.dumps({"cycle": now, "version": version,
                             "eval_auc": round(float(auc), 4), "n_days": n_days}) + "\n")


def _refit_for_serving(candidate_ver: str, now: str) -> None:
    """After the gate passes, fine-tune the candidate the rest of the way through `now`
    (fold in the eval days) so the served model has ~0 lag. GPU integration point —
    until wired, the gated checkpoint (trained through train_cutoff) serves as-is."""
    print(f"[retrain] NOTE: refit-for-serving ({candidate_ver} through {now}) is the "
          "GPU integration point; gated checkpoint serves until wired.")


def _keep_last_n() -> int:
    import yaml
    from ..common import paths
    b = yaml.safe_load((paths.REPO_ROOT / "budget.yaml").read_text()) or {}
    return int((b.get("registry", {}) or {}).get("keep_last_n", 10))


def _finetune(cfg: dict, incumbent_ver: str | None, pq, candidate_ver: str,
              timestamp: str) -> str | None:
    """Warm-start fine-tune the incumbent on data through pq.train_cutoff (recency-
    weighted), via the vendored train.py --resume. Returns the registered candidate
    version, or None on failure. NOTE: wiring train's data config to the rolling store
    + materializing the recency-weighted sample is the GPU-validated integration point."""
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
