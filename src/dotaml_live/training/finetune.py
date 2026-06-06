"""Production fine-tune driver — runs the FULL multi-task v7 recipe (train_v7: all 8
heads, 9-scenario masking, adaptive probes) warm-started from the incumbent, on data
materialized from the rolling store.

This is what retrain._finetune (candidate, trained through train_cutoff) and
_refit_for_serving (trained through `now`) call. Unlike the lean sim, the resulting
checkpoint keeps good item-build / hero-pick / duration heads, not just win.

Data is materialized into a temp dir in dotaml-turbo's expected layout
(source_dir/{train,val}.parquet + rich/{train,val}.parquet) so load_train_val + the
vendored train_v7 run unchanged. The train split is RECENCY-RESAMPLED (recent +
current-patch days oversampled); the val split is the prequential eval window.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import torch

from ..common import config
from ..pipeline import rolling_store as rs
from . import registry
from .data import load_train_val
from .mae import ScenarioSampler
from .probes import ProbeSuite
from .retrain import recency_weights
from .train import train_v7
from ..model.models import build_model, count_params

# load_train_val only consults splits for assert_no_test_dates; our materialized data
# is all train/eval (never test), so a far-future test window passes everything.
_BENIGN_SPLITS = {"test_start_date": "2099-01-01", "test_end_date": "2099-12-31"}


def _day(store: Path, d: str) -> Path:
    return store / f"date={d}.parquet"


def _all_pf_days() -> list[str]:
    return sorted(p.stem.split("=", 1)[1] for p in rs.pf_dir().glob("date=*.parquet"))


def _materialize(train_cutoff: str, eval_dates: list[str], tmp: Path,
                 n_train_rows: int, lookback_days: int, seed: int = 0) -> None:
    """Write tmp/pf/{train,val}.parquet + tmp/rich/{train,val}.parquet from the rolling
    store. Train = recency-resampled days <= cutoff; val = eval_dates (full)."""
    rcfg = config.training_config()["recency"]
    rng = np.random.default_rng(seed)
    (tmp / "pf").mkdir(parents=True, exist_ok=True)
    (tmp / "rich").mkdir(parents=True, exist_ok=True)

    # --- train: recency-weighted sample of days <= cutoff (pf + rich row-aligned) ---
    days = [d for d in _all_pf_days() if d <= train_cutoff][-lookback_days:]
    w = recency_weights(days, rcfg["half_life_days"], rcfg["current_patch_upweight"],
                        rcfg["current_patch_start"], now=train_cutoff)
    wv = np.array([w[d] for d in days], dtype=np.float64)
    alloc = np.maximum(1, np.round(n_train_rows * wv / wv.sum()).astype(int))
    pf_parts, rich_parts = [], []
    for d, k in zip(days, alloc):
        pf_t = pq.read_table(_day(rs.pf_dir(), d))
        rich_t = pq.read_table(_day(rs.rich_dir(), d))
        n = min(int(k), pf_t.num_rows)
        idx = np.sort(rng.choice(pf_t.num_rows, size=n, replace=False))
        pf_parts.append(pf_t.take(idx))
        rich_parts.append(rich_t.take(idx))     # row-aligned with pf (built in lockstep)
    pq.write_table(pa.concat_tables(pf_parts), tmp / "pf" / "train.parquet")
    pq.write_table(pa.concat_tables(rich_parts), tmp / "rich" / "train.parquet")

    # --- val: the prequential eval window, full ---
    val_pf = [pq.read_table(_day(rs.pf_dir(), d)) for d in eval_dates if _day(rs.pf_dir(), d).exists()]
    val_rich = [pq.read_table(_day(rs.rich_dir(), d)) for d in eval_dates if _day(rs.rich_dir(), d).exists()]
    pq.write_table(pa.concat_tables(val_pf), tmp / "pf" / "val.parquet")
    pq.write_table(pa.concat_tables(val_rich), tmp / "rich" / "val.parquet")


def run_finetune(incumbent_dir: Path, out_ver: str, train_cutoff: str,
                 eval_dates: list[str], epochs: int, n_train_rows: int,
                 lr: float = 2e-4, warmup_steps: int = 500, warm_start: bool = True) -> Path:
    """Run the full multi-task recipe on materialized rolling-store data and register
    the candidate version (model.pt + config + vocab + carried-forward artifacts +
    metrics.json). warm_start=True loads the incumbent weights (nightly fine-tune);
    warm_start=False trains from random init (weekly from-scratch). `incumbent_dir`
    always supplies config/vocab/artifacts. Returns the version dir."""
    cfg = config.training_config()
    base_cfg = __import__("yaml").safe_load((incumbent_dir / "config.yaml").read_text())
    seed = int(base_cfg.get("seed", 42))
    feat_names = base_cfg["player_features"]["feat_names"]
    n_pf = int(base_cfg["player_features"]["n_player_feats"])
    vocab_path = incumbent_dir / "item_vocab.json"
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    tmp = Path(tempfile.mkdtemp(prefix="ftcycle_", dir=str(rs.store_root())))
    try:
        t0 = time.time()
        _materialize(train_cutoff, eval_dates, tmp, n_train_rows, lookback_days=90, seed=seed)
        train_ds, val_ds, meta = load_train_val(
            seed=seed, n_target=n_train_rows, feat_names=feat_names,
            source_dir=tmp / "pf", splits=_BENIGN_SPLITS, smoke=False,
            sidecar_dir=tmp / "rich", vocab_path=vocab_path,
            canonical_sort=bool(base_cfg["transformer_model"].get("use_canonical_sort", True)),
            default_patch_id=int(base_cfg["patch"].get("default_patch_id", 1)))
        print(f"[finetune] data ready {time.time()-t0:.0f}s — train={len(train_ds):,} "
              f"val={len(val_ds):,} ({meta['train_date_min']}..{meta['train_date_max']} -> "
              f"{meta['val_date_min']}..{meta['val_date_max']})")

        mhp = base_cfg["transformer_model"]
        item_vocab_size = int(meta.get("item_vocab_size", 0)) or 1
        model = build_model(mhp, vocab_size=int(base_cfg["hero"]["vocab_size"]),
                            n_player_feats=n_pf, item_vocab_size=item_vocab_size,
                            patch_vocab_size=int(base_cfg.get("patch", {}).get("vocab_size", 8))).to(device)
        if warm_start:
            sd = torch.load(incumbent_dir / "model.pt", map_location="cpu", weights_only=True)
            missing, unexpected = model.load_state_dict(sd, strict=False)
            assert not unexpected and set(missing) <= {"patch_embed.weight"}, (missing, unexpected)
            print(f"[finetune] warm-started from {incumbent_dir.name} ({count_params(model)['total']:,} params)")
        else:
            print(f"[finetune] FROM SCRATCH (random init, {count_params(model)['total']:,} params)")

        sc = base_cfg["scenarios"]["distribution"]
        sampler = ScenarioSampler(
            initial_probs={s: float(d["init_prob"]) for s, d in sc.items()},
            loss_weights={s: dict(d.get("loss_weights", {})) for s, d in sc.items()},
            initial_targets={s: float(d["probe_target"]) for s, d in sc.items()
                             if d.get("probe_target") is not None},
            seed=seed)
        tot = sum(sampler.probs.values()); sampler.probs = {k: v / tot for k, v in sampler.probs.items()}

        pcfg = base_cfg["probes"]
        autocast_dtype = torch.bfloat16 if device.type == "cuda" else None
        probe_suite = ProbeSuite(val_ds=val_ds, device=device, autocast_dtype=autocast_dtype,
                                fixed_subset_size=min(int(pcfg["fixed_subset_size"]), len(val_ds)),
                                seed=int(pcfg.get("seed", 42)),
                                batch_size=int(pcfg.get("batch_size", 1024)),
                                halt_thresholds={})   # monitor-only during fine-tune (0004)

        hist = tmp / "hist.json"; hist.write_text("")
        tr = train_v7(model, train_ds, val_ds,
                      hp={"batch_size": int(base_cfg["transformer_optim"]["batch_size"]),
                          "lr": lr, "weight_decay": 0.0},
                      max_epochs=epochs, device=device, mixed_precision=device.type == "cuda",
                      patience=None, sampler=sampler, probe_suite=probe_suite,
                      probe_every_epochs=1, halt_at_epoch=10_000,
                      warmup_steps=warmup_steps, cosine_min_lr=1e-5,
                      probe_history_path=hist, sampling_history_path=tmp / "samp.json", smoke=False)
        final_probes = probe_suite.run(model)
        print(f"[finetune] done {tr.train_seconds:.0f}s — pure_pregame_val_auc="
              f"{tr.best_pure_pregame_auc:.4f} | probes {final_probes}")

        out_dir = registry.new_version_dir(out_ver)
        torch.save(model.state_dict(), out_dir / "model.pt")
        if out_dir.resolve() != incumbent_dir.resolve():   # not an in-place refit
            shutil.copy2(incumbent_dir / "config.yaml", out_dir / "config.yaml")
            shutil.copy2(incumbent_dir / "item_vocab.json", out_dir / "item_vocab.json")
            registry.copy_artifacts_from(incumbent_dir.name, out_ver)
        import json
        (out_dir / "metrics.json").write_text(json.dumps({
            "val_auc_pure_pregame": tr.best_pure_pregame_auc,
            "final_probe_results": final_probes, "epochs": epochs,
            "train_cutoff": train_cutoff, "warm_started_from": incumbent_dir.name}, indent=2))
        registry.register(out_ver)
        return out_dir
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
