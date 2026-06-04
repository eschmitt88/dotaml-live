# dotaml-live

Live, self-updating Dota 2 **Turbo** service built on the `v7-unified-masked-multitask`
foundation. First production/serving project sibling to `~/projects/research/`.

Three subsystems:

1. **Data pipeline** — consume recent matches from the Azure blob landing zone
   (`dota2datalake/matches/turbo`, populated by the upstream DotaDB collector — we
   never re-hit the Steam API), append to a rolling window, build features
   incrementally, keep a sealed eval holdout.
2. **Continuous training** — periodic v7 warm-start fine-tune on the rolling window;
   model registry with **head-to-head, probe-gated** promotion to `live`.
3. **Dashboard** (the deliverable) — FastAPI JSON API + Vite/React SPA serving four
   model-driven views from the live model:
   - **Item build** — time-integrated `optimize_build` (duration-integrated, selling, component-aware).
   - **Top hero picks** — `hero_pick_rec` over partial drafts (known/masked allies & enemies).
   - **Win-vs-duration** curve for a draft.
   - **Top hero combos** — pair/trio, optimizable for kills/min or synergy (team win-prob lift vs independent baseline).

See `docs/decisions/` for the vendor boundary and the walk-forward holdout policy,
and `VENDOR.md` for code provenance. The plan lives at
`~/.claude/plans/toasty-drifting-treasure.md`.

## Status

Phase 0 (scaffold + vendor) — in progress. Snapshot-first: stands up on
dotaml-turbo's existing 7.40 snapshot + checkpoint before the live Azure consumer.

## Layout

```
src/dotaml_live/{model,features,queries,pipeline,training,serving,common}
registry/<version>/{model.pt,config.yaml,item_vocab.json,*artifacts}  + live pointer
data/                rolling store + aggregator state (DVC, not git)
frontend/            Vite + React SPA
systemd/             dashboard + nightly retrain units
```
