# Vendored code provenance

`dotaml-live` is a **production/serving** project. It does **not** import from the
sibling research repo `dotaml-turbo` at runtime. The v7 model, feature builders,
and serving query prototypes are **copied** (vendored) into `src/dotaml_live/` and
hardened here. This file records where they came from.

## Source

- Repo: `~/projects/research/dotaml-turbo`
- Pinned commit: `3ce41202faf0989c4f34e052e394d5c6950deea2` (2026-06-03)
- Foundation experiment: `experiments/2026-05-26-v7-unified-masked-multitask-740`

## Vendored files → destination

| Source (relative to dotaml-turbo) | Destination | Notes |
|---|---|---|
| `…/v7-…-740/models.py` | `src/dotaml_live/model/models.py` | verbatim |
| `…/v7-…-740/serve/v7_inference.py` | `src/dotaml_live/model/v7_inference.py` | hardened: registry-relative paths, package import of `models` |
| `…/v7-…-740/serve/queries.py` | `src/dotaml_live/queries/queries.py` | hardened imports |
| `…/v7-…-740/serve/build_optimizer.py` | `src/dotaml_live/queries/build_optimizer.py` | hardened: duration PMF from artifact, not val parquet |
| `…/v7-…-740/serve/lookups.py` | `src/dotaml_live/queries/lookups.py` | hardened: player features + hero prior from artifacts, not val parquet |
| `…/v7-…-740/serve/{heroes,items}.json` | `src/dotaml_live/queries/` | OpenDota constants snapshot |
| `…/foundation-v3-740/build_features_extended.py` | `src/dotaml_live/features/` | verbatim (adapted to durable aggregator in Phase 2) |
| `…/foundation-v3-740/build_rich_cols_extended.py` | `src/dotaml_live/features/` | verbatim |
| `…/v7-…-740/{train,data,mae,probes}.py` | `src/dotaml_live/training/` | `train.py` gains warm-start + recency weighting in Phase 3 |
| `…/v7-…-740/config.yaml` | `registry/v7-base/config.yaml` | base model config |
| `…/rich-supervision-…-740/results/item_vocab.json` | `registry/v7-base/item_vocab.json` | 305-item vocab |
| `…/v7-…-740/results/pretrain_encoder_v7_unified.pt` | `registry/v7-base/model.pt` | trained checkpoint (26 MB, DVC) |

## Re-vendoring

Run `scripts/vendor_sync.sh` to re-copy from the source repo after a new v7 train.
It re-applies the copies above; the hardening edits are tracked in git, so review
the diff and re-apply any that the upstream change disturbed.
