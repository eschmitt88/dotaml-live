# dotaml-live — working notes

Did / Findings / Next, appended per session (mirrors the research-repo discipline).

## 2026-06-04 — scaffold + Phase 1 dashboard

### Did
- **Phase 0 (scaffold + vendor).** Created the first production/serving project at
  `~/projects/dotaml-live`: `src/dotaml_live/{model,features,queries,pipeline,
  training,serving,common}`, config/, registry/, frontend/, systemd/, scripts/,
  docs/decisions/, tests/. Vendored the v7 model + builders + serve queries pinned to
  `dotaml-turbo @3ce4120` (VENDOR.md), rewired imports into a coherent package, and
  hardened `V7Foundation` to registry-relative paths (`common/paths.py`).
- **ADRs.** 0001 vendor boundary; 0002 walk-forward sealed-holdout policy (fresh
  holdout + frozen-anchor tripwire + recency-weighted training + embargo).
- **Phase 1 (dashboard on the vendored checkpoint).**
  - Broke the 3 prototype runtime val-parquet couplings via registry artifacts
    (`queries/artifacts.py` + `scripts/bootstrap_from_snapshot.py`): `hero_prior.npy`,
    `duration_pmf.npz` (fine CDF), `player_features.parquet` (610k accounts).
  - Built the missing Q4 `queries/hero_combos.py` (synergy = team win-prob lift over
    the additive single-hero baseline; + kills/min mode).
  - FastAPI service (`serving/`) with hot-reloading `ModelHolder`, `/health /model
    /meta` + `POST /api/{winprob,hero-picks,win-vs-duration,item-build,hero-combos}`;
    serves the built SPA.
  - Vite/React SPA (`frontend/`) — draft builder + all four views + win-vs-duration
    chart. Builds clean.

### Findings
- CUDA works here (RTX 5080, torch 2.12 **cu130**, sm_120) — server defaults to cuda.
- Served path is artifact-only (grep-verified: no `val.parquet` at request time).
- Sanity: winprob 0.620 (0.620 w/ account 3303652), AM build = Treads/BKB/Butterfly/
  Battlefury/Manta/Abyssal, synergy surfaces Drow+Io / Drow+CM. 6/6 API tests pass.

### Next
- **Phase 2** — rolling store + durable, resumable `PlayerAggregator` (aggregator
  history vs training window), `build_runner`, `seal_holdout` + walk-forward
  `splits.yaml`, parity check vs dotaml-turbo on overlapping match_ids.
- **Phase 3** — warm-start + recency-weighted loss in `train.py`, `retrain.py`,
  `registry.py`, head-to-head `promote.py` gate, systemd retrain timer.
- **Phase 4** — Azure blob consumer (DefaultAzureCredential), no Steam API.
- Nice-to-have: code-split the SPA bundle (recharts pushes it >500kB).

## 2026-06-04 (session 2) — storage consolidation + Phases 2-4

### Did
- **Storage:** consolidated all raw into one canonical lake `~/projects/dota-datalake`
  (175 GB moved by instant same-FS rename; turbo hard-repointed: 7 configs + dvc.yaml;
  ADR 0003). dotaml-live consumes the lake; raw is one source of truth.
- **Phase 2:** durable resumable aggregator (`features/aggregator.py`, wraps vendored
  PlayerAggregator), `pipeline/{rolling_store,build_runner,seal_holdout}.py`. Verified
  on real lake data: 115/115 schema parity; incremental-resume == fresh-batch
  (bit-identical features + state).
- **Phase 3:** `training/{registry,promote,retrain}.py` + `train.py` warm-start
  (`--resume`) + package-import fixes; systemd nightly retrain. Gate (head-to-head +
  probes + anchor) + recency-resample unit-tested; `evaluate_win_auc` verified 0.629
  on a slice.
- **Phase 4:** `pipeline/blob_consumer.py` (tail-only Azure pull into the lake, lazy
  azure deps, dry-run); wired into the retrain cycle.

### Findings
- 12 tests green. Raw move was low-risk (raw was a DVC *dep* not a cached out).
- Latest local raw date = 2026-03-09 (test-window raw 03-10..23 was never mirrored —
  consistent with HCE); the live consumer will pull from there.

### Next
- Run `scripts/replay_history.sh` (full 175 GB feature replay → seeds aggregator).
- Validate the fine-tune integration point (retrain._finetune): wire train.py's data
  config to the rolling store + materialize the recency-weighted sample; do one real
  warm-start fine-tune on a GPU and confirm the gate flips `live`.
- `az login` + `pip install -e '.[azure]'`, then a live `blob_consumer` pull.
- Optional: GitHub remote + Pages; DVC/backup for the lake.
