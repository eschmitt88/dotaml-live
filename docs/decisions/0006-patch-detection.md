# 0006 — Detect patches, don't retrain off-cadence on them

- Status: accepted
- Date: 2026-06-06
- Amends: ADR 0005 (drops its "patch may trigger an off-cadence retrain" idea).

## Context

ADR 0005 floated an off-cadence retrain when a patch drops. In practice the same-day
(and next-few-days) post-patch data is too thin to give the model much, and the
nightly/weekly cadence already folds the new patch's data in as it accumulates. So an
immediate patch-triggered retrain isn't worth the complexity.

What a patch *does* need promptly is its **date in the patch-edge list** — so `patch_id`
labels are correct and the recency current-patch boundary tracks the live meta. That
was a hardcoded list nobody would remember to update.

## Decision

- **No patch-triggered retrain.** The cadence adapts on its own.
- **Centralize the edge list** in `config/patches.yaml` (single source; `common/patches.py`
  loads it, with a fallback to the historical edges). The feature builder and the
  training loader both read it. The recency current-patch boundary auto-tracks the
  latest edge.
- **Detect new patches** (`pipeline/patch_watch.py`) by diffing OpenDota's patch
  constant against our edges (a patch dated after our latest edge is "new"). It:
  - runs in the nightly cycle and logs a prominent warning,
  - writes `data/patch_status.json`, surfaced as a **banner on the dashboard**,
  - and `--add` appends detected patches to `config/patches.yaml` (next free patch_id).

## Consequences

- Adding a patch is one edit (or `patch_watch --add`), picked up everywhere; the next
  retrain labels those matches with the new patch_id and upweights them via recency.
- The model's `patch_embed` has 8 slots (ids 1–4 used). After a few more patches it
  must be grown — `add_edge` raises when the vocab is exhausted, flagging the need.
- Detection only (not auto-add by default): a wrong auto-detected date would mislabel
  data, so a human (or an explicit `--add`) confirms.
