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
