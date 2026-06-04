# 0001 — Vendor the v7 model code; no runtime import of the research repo

- Status: accepted
- Date: 2026-06-04

## Context

`dotaml-live` is the first production/serving project, sibling to the research
projects under `~/projects/research/`. Its model, feature builders, and serving
query prototypes all originate in the research repo `dotaml-turbo`
(`experiments/2026-05-26-v7-unified-masked-multitask-740`). We must decide how the
production service depends on that code.

## Decision

**Vendor (copy) the code into `src/dotaml_live/`, pinned to a recorded source
commit.** `dotaml-live` never adds the research repo to `sys.path` or imports from
it at runtime.

Provenance and the re-vendor procedure are recorded in `VENDOR.md`
(`scripts/vendor_sync.sh`).

## Rationale

- **Scope boundary.** A production service must not depend on the live working tree
  of a research repo whose experiments dir churns constantly.
- **Reproducibility.** The served model is pinned to an exact source SHA.
- **Hardening freedom.** Serving requires changes (registry-relative paths,
  artifacts instead of val-parquet reads) that don't belong upstream in the
  experiment.

## Consequences

- Drift between vendored and upstream code is handled by an explicit re-vendor step,
  not automatically. The hardening edits are tracked in git so a re-vendor diff is
  reviewable.
- The Phase-1 bootstrap *reads* the snapshot + checkpoint once as data; afterward
  dotaml-live is self-contained.
