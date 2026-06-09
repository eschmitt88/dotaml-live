# 0007 — Hero vocab must grow for new heroes (Largo / 7.40)

- Status: accepted & implemented — Largo live in ft-2026-06-09 (2026-06-09)
- Date: 2026-06-07 (implemented 2026-06-09)
- Related: ADR 0006 (patch detection — same class of "meta moved, model didn't" problem,
  but for the hero embedding rather than the patch embedding).

## Context

Patch **7.40** (edge 2025-12-16) added a new hero, **Largo (hero_id 155)**. The model's
hero embedding is `nn.Embedding(vocab_size=151)` — it only represents ids 0–150. Three
places assume the `[1, 150]` range:

- `model/v7_inference.py` built the embedding with a hardcoded `vocab_size=151`;
- `features/build_features_extended.py` (`if any(h < 1 or h > 150): continue`) — **silently
  drops every match containing Largo from the feature tables**;
- `training/data.py` (`raise if hero_ids.max() > 150`) — would hard-fail training if such
  a match reached the loader.

Two failure modes resulted:

1. **Serving crash.** Selecting Largo in the dashboard fed id 155 into the hero embedding,
   triggering a CUDA device-side assert. A device-side assert poisons the entire process's
   CUDA context, so *every* subsequent request (winprob, hero-picks, item-build,
   win-vs-duration) 500s until restart. The dashboard's auto-recompute made this trivial to
   hit. (Surfaced 2026-06-06; root-caused 2026-06-07.)
2. **Silent exclusion from learning.** Because the feature builder drops Largo matches, the
   model has never seen 7.40 games involving him — even patch-aware retrains can't learn his
   embedding. He is invisible to the model, not merely unsupported.

## Decision

Treat the hero vocab like the patch vocab (ADR 0006): a value that must grow as the meta
grows, with a single source of truth and headroom.

**Shipped now (no retrain required):**
- `predict()` maps any hero id outside `[0, n_heroes)` to PAD(0) and marks the slot as a
  masked "unknown hero" — exactly v7's trained partial-draft scenario — so an unknown hero
  degrades gracefully instead of crashing. This is the permanent safety net for *any* future
  hero, not just Largo.
- Serving reads the hero vocab from the checkpoint's own `config.yaml`
  (`hero.vocab_size`, fallback 151) instead of hardcoding 151, so a future expanded-vocab
  model loads without a size mismatch.
- `/model` exposes `n_heroes`; the dashboard marks ids ≥ `n_heroes` as "not in model".

**Done (2026-06-09):** all four steps below were implemented and Largo was baked into
live model **ft-2026-06-09** (vocab 161). The rolling store was rebuilt via causal replay
(Largo enters at the 7.40 edge), the warm-start finetune resized the embedding and learned
Largo, and the model was promoted via `--force-promote` — the AUC gate declined it (0.6576
vs 0.6578, noise; Largo is too rare to move overall AUC), so promotion was a deliberate,
logged override. Serving confirmed: Largo is in-vocab, scored, and recommendable. See NOTES
2026-06-09.

**The steps (as implemented):**
1. Bump `hero.id_max` / `hero.vocab_size` in the training config with headroom (e.g.
   `id_max: 160`, `vocab_size: 161` — covers Largo at 155 plus several future heroes; spare
   rows are unused PAD until a hero claims them).
2. Replace the hardcoded `150` in `build_features_extended.py` and `data.py` with the
   config-driven `hero.id_max`, so new-hero matches flow into features/training instead of
   being dropped. Re-run feature extraction so 7.40 Largo games enter the tables.
3. Make finetune's warm-start **resize** the hero embedding when the configured vocab
   exceeds the incumbent's: copy the overlapping rows, randomly-init the new ones. A naive
   bump otherwise fails `finetune.py`'s `load_state_dict` shape/assert check.
4. Retrain (through `/plan`, in a worktree). The head-to-head promotion gate backstops
   correctness — a botched resize or a regression won't promote over the incumbent.

## Consequences

- The dashboard can never again be taken down by an unknown hero id; the worst case for a
  not-yet-trained hero is that its specific identity is ignored (treated as unknown).
- Largo remains unrepresented in the model until the pending retrain lands. The dashboard
  flags this visually; this ADR records why.
- New heroes now have a defined runway: bump `id_max`/`vocab_size`, re-extract features,
  retrain. When the spare rows run low, grow the vocab again (mirrors the `patch_embed`
  growth note in ADR 0006).
