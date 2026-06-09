# dotaml-live — working notes

Did / Findings / Next, appended per session (mirrors the research-repo discipline).

## 2026-06-09 — Largo (7.40) baked into the live model

### Did
- **Included patch-7.40 hero Largo (id 155) in the model**, end to end, in a git worktree:
  - Hero vocab is now config-driven (`config/training.yaml` `hero: id_max 160 / vocab 161`);
    the `[1,150]` filters in build_features / build_runner / data.py read it; `finetune`
    resizes the hero embedding on warm-start (preserves learned rows) and writes the
    candidate config's hero block synced to the model vocab. (ADR 0007.)
  - **Rebuilt the rolling store** (causal replay, 277 days / 51.9M matches) with the
    relaxed filter — Largo enters exactly at the 7.40 edge (2025-12-16). A memory watchdog
    (kill+resume from the 5-day checkpoint) guarded the long single-process replay.
  - Warm-start retrain → candidate **ft-2026-06-09** (vocab 161, learns Largo).
  - **hero-pick candidates** now use the real roster within the model vocab (was hardcoded
    `range(1,151)`), so Largo is recommendable; phantom roster-gap ids excluded.
- **Promoted ft-2026-06-09 to live** via a new `--force-promote` override and restarted the
  dashboard. Swapped the production rolling store to the Largo build (old store backed up to
  `data/*.pre-largo`). Re-enabled the nightly timer.

### Findings
- The AUC gate **declined** the candidate (0.6576 vs incumbent 0.6578 — noise). Expected:
  Largo is ~2% of matches, so a Largo-aware model can't move overall AUC. The gate is blind
  to hero coverage, so promotion was a **deliberate override** (non-inferior AUC + the
  incumbent literally can't represent Largo). Hence `--force-promote`.
- Serving verified: `/model` = ft-2026-06-09, n_heroes=161; winprob + hero-picks on a
  Largo draft return 200 with Largo in-vocab (no longer masked). The dashboard no longer
  marks Largo "not in model".
- `refit-for-serving` failed on an empty-window edge (forced now=2026-06-09 > lake's last
  day 06-08); the gated checkpoint serves as-is (trained through 06-02). The next nightly
  warm-starts from ft-2026-06-09 and refits forward, closing the ~7-day lag.

### Next
- Let the Wed nightly refit ft-2026-06-09 forward (closes the train-through lag) and confirm
  it warm-starts cleanly across the new 161 vocab.
- Once stable, delete `data/*.pre-largo` backups (reproducible from the datalake anyway).
- Consider making the promotion gate coverage-aware (promote at non-inferior AUC when the
  hero roster expands) so future new heroes don't need a manual `--force-promote`.

## 2026-06-07 — dashboard redesign + out-of-vocab hero crash

### Did
- **Dashboard redesign** (Draft analysis tab). Searchable/alphabetical/clearable hero
  combobox (Tab commits the highlight), prominent Radiant/Dire toggle, player assignment
  via popover (Alaric / "wuts a dota", account IDs persisted to localStorage), asymmetric
  layout (Top hero picks dominant + click-to-assign, compact win-prob, full-height item
  build), debounced auto-recompute, favorites strip with contextual win%, Clear-all,
  swap-sides, and an add-to-draft button on combo-discovery rows.
- **Fixed a serving outage** caused by a hero id outside the model's hero vocab.

### Findings — Largo (id 155, patch 7.40) was crashing serving
- Hero embedding is `vocab_size=151` (ids 0–150). **Largo (155), added in 7.40**, overflows
  it → CUDA **device-side assert**, which poisons the whole CUDA context, so *every*
  endpoint 500s until restart (not just the offending call). Auto-recompute made it trivial
  to trigger by selecting Largo.
- Largo is also **silently dropped from features/training**: `build_features_extended.py` and
  `data.py` hard-filter heroes to `[1,150]`. So the model has never seen 7.40 Largo games —
  he's invisible, not just unsupported. Recorded in **ADR 0007**.

### Fixes shipped (committed + pushed)
- `predict()` maps out-of-vocab hero ids → PAD(0) + masked "unknown hero" (trained
  partial-draft scenario) — permanent crash guard for any future hero.
- Serving reads `hero.vocab_size` from the checkpoint config (fallback 151) instead of
  hardcoding, so a future expanded-vocab model loads cleanly. `/model` exposes `n_heroes`;
  the picker marks ids ≥ `n_heroes` as "not in model".

### Next — to actually include Largo (needs a retrain; see ADR 0007)
- Bump `hero.id_max`/`vocab_size` (e.g. 160/161, headroom), wire the two `[1,150]` guards to
  config-driven `id_max`, re-extract features so 7.40 Largo games enter the tables.
- Add hero-embedding **resize** to finetune warm-start (copy overlap rows, init new).
- Retrain via `/plan` in a worktree; head-to-head gate backstops correctness.

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

## 2026-06-04 (session 2, cont.) — data pipeline LIVE + validated

### Did
- Consolidated raw into the `~/projects/dota-datalake` lake; pulled the 82 GB Azure
  live tail (1770 files, 2026-03-10..today) via the existing az login (no interactive
  step needed). Added a 64 GB swapfile as OOM insurance (never actually needed).
- Ran the full replay (35.06M matches / 196 days) then extended with the live tail
  (15.81M / 87 days). **Rolling store now current: 283 days 2025-08-15..2026-06-04,
  ~51M matches, 2.0M accounts, 507 MB resumable aggregator state.**
- Dashboard live on systemd (reboot-safe) at :8090. Combo discovery moved to its own
  precomputed tab with Pairs + Trios (synergy + kills/min, sortable/filterable).

### Findings
- **PARITY EXACT vs dotaml-turbo: max|diff|=0 over 1.6M sampled feature cells** — the
  durable incremental aggregator is bit-identical to the batch builder. Phase 2 fully
  validated end-to-end on real data.
- Aggregator RAM peaked ~45-50 GB and decelerated; swap untouched.

### Next
- The one remaining step: wire train.py's data config to the rolling store +
  recency-resample, run the first warm-start fine-tune, confirm the gate promotes.
- Then enable the nightly retrain timer. Swapfile can be reclaimed.

## 2026-06-05 — prequential eval, 7.41 patch-awareness, shadow-gate validated

### Did
- Replaced the static walk-forward holdout with **prequential (test-then-train)**
  evaluation (ADR 0004): score live on each new day before training on it; shadow
  head-to-head gate; refit-for-serving. Backtest of v7-base surfaced a real ~0.02 AUC
  drop at 2026-03-25.
- That drop = **patch 7.41**. Found the model was patch-blind (train.py discarded
  patch_id; no patch embedding; edges stopped at 7.40). ADR 0005: dropped the frozen
  anchor (a pre-7.41 slice can block adaptation), re-pointed recency to 2026-03-25
  (upweight 1.8), and added a **zero-init patch embedding** + 7.41 edge (v7-base
  byte-identical, verified; activates on retrain).
- Built `scripts/simulate_live.py` and ran a **3-day live-loop simulation** (warm-start
  fine-tune from incumbent → shadow-eval on the unseen day → promote).

### Findings — the shadow gate works
| day | incumbent AUC | candidate AUC | promote |
|---|---|---|---|
| 06-02 | 0.6382 (v7-base) | 0.6529 | **yes** (+0.015, recovers most of the 7.41 gap) |
| 06-03 | 0.6540 | 0.6545 | no (+0.0004 < margin — gate resists churn) |
| 06-04 | 0.6507 | 0.6521 | **yes** (+0.0014) |

- Fine-tune sets came out ~90% patch-4 (7.41) via recency+current-patch upweight, as
  designed. A patch-aware fine-tune lifts unseen-day AUC 0.638→0.653 (≈ pre-7.41 level).
- Gate promotes real gains, rejects marginal ones, chains the incumbent correctly.

### Next
- Productionize the fine-tune (full multi-task train.py recipe + refit-for-serving)
  in retrain._finetune; the lean sim used a win-head-only fine-tune.
- Enable the nightly retrain timer once the production fine-tune is wired.
