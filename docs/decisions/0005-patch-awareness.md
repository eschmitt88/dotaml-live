# 0005 — Patch-awareness; drop the frozen anchor

- Status: accepted
- Date: 2026-06-05
- Amends: ADR 0002 (frozen anchor) and 0004 (gate).

## Context

The prequential backtest of `v7-base` (ADR 0004) showed a ~0.02 AUC cliff at
**2026-03-25 that persists** — which the user identified as Dota **patch 7.41**
(dota2.com/patches/7.41). Reading the model confirmed two root causes:

1. **The model is patch-blind.** `models.py` has no patch embedding; `data.py` computes
   a `patch_id` but `train.py` unpacks it as `_patch_id` and never passes it to the
   model. And `PATCH_EDGES` stopped at 7.40, so all post-2025-12-16 data — including
   7.41 — was labeled patch_id 1. The model had no signal that the meta changed and no
   mechanism to use one, so it stays frozen on 7.40 correlations.
2. **The frozen anchor is the wrong tool here.** It is the last 2 weeks of *pre-7.41*
   data. As an anti-regression tripwire in a post-7.41 world it can wrongly **block a
   model that correctly adapts to 7.41** (different meta → expected "regression" on the
   old one), besides freezing ~2.8M stale matches.

## Decision

Three changes:

1. **Drop the frozen anchor** from `splits.yaml` and the promotion gate
   (`anchor_max_regression` removed). Prequential monitoring already catches real
   regressions on *current* data (it caught 7.41); the head-to-head gate covers
   "don't ship worse". Nothing is held out permanently.

2. **Re-point recency to the current patch.** `recency.current_patch_start = 2026-03-25`
   and a stronger `current_patch_upweight` (1.8) so retrains favor the live 7.41 meta.
   This is the primary, architecture-free remedy for the drop: the learned correlations
   shift via the training distribution.

3. **Make the model patch-aware** (so explicit conditioning is possible, not just
   implicit via reweighting):
   - `PATCH_EDGES` gains `("2026-03-25", 4)` → 7.41 = patch_id 4 (derived from dates at
     load time; no feature rebuild needed).
   - `models.py` gains a **zero-init** `patch_embed` (`nn.Embedding(patch_vocab, d_model)`)
     added as a per-match bias broadcast over all tokens in `encode()`. Zero-init means
     a checkpoint trained without it is **byte-for-byte unchanged** (verified: `v7-base`
     winprob 0.619618 before and after); the embedding only becomes active once a retrain
     learns non-zero rows.
   - `train.py` now passes `patch_id` to the model; `V7Foundation` supplies it (default
     `CURRENT_PATCH_ID = 4`) and loads `v7-base` with `strict=False`, asserting the only
     missing key is `patch_embed.weight`.

## Consequences

- `v7-base` serves identically today; patch conditioning + the 7.41 upweight take effect
  on the next retrain, which should close most of the 7.41 gap.
- This diverges the vendored model from dotaml-turbo's v7 (recorded; a re-vendor must
  re-apply the patch embedding). The model checkpoint format gains one tensor.
- `patch_vocab_size` is 8; ids 1–4 used, room for future patches.
