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
              update_data: bool = True, from_scratch: bool = False) -> dict:
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
    print(f"[retrain] prequential: train<= {pq.train_cutoff} | eval {pq.eval_start}..{pq.eval_end} "
          f"({len(eval_files)} unseen days)")

    incumbent_ver = registry.live_version()

    # 3. prequential MONITORING — score current live on the unseen eval window, log it
    inc_auc = float("nan")
    if incumbent_ver and eval_files:
        inc_auc = promote.evaluate_win_auc(registry.version_dir(incumbent_ver), eval_files)
        _log_prequential(now, incumbent_ver, inc_auc, len(eval_files))
        print(f"[retrain] prequential live AUC ({incumbent_ver}): {inc_auc:.4f}")

    # 4. candidate training. Nightly: warm-start fine-tune. Weekly: from-scratch.
    if not train:
        return {"status": "monitor-only", "now": now,
                "eval_window": [pq.eval_start, pq.eval_end],
                "prequential_live_auc": round(inc_auc, 4), "incumbent": incumbent_ver}
    print(f"[retrain] mode: {'WEEKLY from-scratch' if from_scratch else 'nightly warm-start'}")
    candidate_ver = _finetune(cfg, incumbent_ver, pq, f"{'fs' if from_scratch else 'ft'}-{now}",
                              timestamp, from_scratch=from_scratch)
    if candidate_ver is None:
        return {"status": "train-skipped (GPU integration point)", "now": now,
                "prequential_live_auc": round(inc_auc, 4)}

    # 5. SHADOW GATE — candidate vs incumbent on the SAME unseen eval window
    cand_dir = registry.version_dir(candidate_ver)
    cand = promote.GateInput(
        fresh_auc=promote.evaluate_win_auc(cand_dir, eval_files) if eval_files else float("nan"),
        probes=_load_probes(cand_dir), anchor_auc=None)   # frozen anchor removed (0005)
    inc = None
    if incumbent_ver and incumbent_ver != candidate_ver:
        inc = promote.GateInput(fresh_auc=inc_auc, probes={}, anchor_auc=None)
    result = promote.decide(cand, inc, cfg["promotion"], _halt_thresholds(cand_dir))
    print(f"[retrain] shadow gate: promote={result.promote} :: {result.reasons}")

    # 6. promote -> refit-for-serving through `now` (kill the lag) -> regen combos -> prune
    if result.promote:
        if config.splits_policy()["prequential"].get("refit_for_serving", True):
            _refit_for_serving(candidate_ver, now)
        registry.set_live(candidate_ver)
        _regen_combos(candidate_ver)        # discovery table is model-specific
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


def _regen_combos(version: str) -> None:
    """Regenerate the discovery combos for a promoted model (they're model-specific).
    The dashboard then serves the new model's combos on its next request, no restart."""
    try:
        from ..queries.combos_precompute import build_table
        build_table(registry.version_dir(version))
        print(f"[retrain] regenerated discovery combos for {version}")
    except Exception as e:  # noqa: BLE001
        print(f"[retrain] combos regen failed ({e}); discovery tab keeps carried-forward table")


def _refit_for_serving(candidate_ver: str, now: str) -> None:
    """After the gate passes, fine-tune the candidate the rest of the way through `now`
    (fold in the eval days) so the served model has ~0 lag — extends the same version."""
    from . import finetune
    ft = config.training_config().get("finetune", {})
    try:
        finetune.run_finetune(registry.version_dir(candidate_ver), candidate_ver,
                              train_cutoff=now, eval_dates=[now],
                              epochs=max(1, int(ft.get("epochs", 3)) // 2),
                              n_train_rows=int(ft.get("train_rows", 1_500_000)))
        print(f"[retrain] refit-for-serving: {candidate_ver} extended through {now}")
    except Exception as e:  # noqa: BLE001
        print(f"[retrain] refit-for-serving failed ({e}); gated checkpoint serves as-is")


def _keep_last_n() -> int:
    import yaml
    from ..common import paths
    b = yaml.safe_load((paths.REPO_ROOT / "budget.yaml").read_text()) or {}
    return int((b.get("registry", {}) or {}).get("keep_last_n", 10))


def _finetune(cfg: dict, incumbent_ver: str | None, pq, candidate_ver: str,
              timestamp: str, from_scratch: bool = False) -> str | None:
    """Train a candidate on recency-weighted data through pq.train_cutoff (full
    multi-task recipe). Nightly warm-starts from the incumbent; weekly trains from
    scratch (more epochs / full window) to reset warm-start drift. `incumbent_ver`
    supplies config/vocab/artifacts even from-scratch. Returns the version or None."""
    if not incumbent_ver:
        print("[retrain] no source model for config/vocab")
        return None
    from . import finetune          # lazy (avoid retrain<->finetune import cycle)
    ft = cfg.get("finetune", {})
    ar = cfg.get("anchor_retrain", {})
    epochs = int(ar.get("epochs", 25) if from_scratch else ft.get("epochs", 3))
    rows = int(ar.get("train_rows", 5_000_000) if from_scratch else ft.get("train_rows", 1_500_000))
    lr, warmup = (1e-3, 1000) if from_scratch else (2e-4, 500)   # from-scratch needs more
    try:
        finetune.run_finetune(registry.version_dir(incumbent_ver), candidate_ver,
                              train_cutoff=pq.train_cutoff, eval_dates=pq.eval_dates(),
                              epochs=epochs, n_train_rows=rows, lr=lr, warmup_steps=warmup,
                              warm_start=not from_scratch)
        return candidate_ver
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"[retrain] training failed: {e}")
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
    ap.add_argument("--weekly", action="store_true",
                    help="from-scratch full retrain (resets warm-start drift) instead of "
                         "the nightly warm-start fine-tune")
    args = ap.parse_args()
    out = run_cycle(now=args.now, timestamp=args.timestamp,
                    train=not args.no_train, update_data=not args.no_update,
                    from_scratch=args.weekly)
    print(f"[retrain] cycle: {out}")


if __name__ == "__main__":
    main()
