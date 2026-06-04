# 0003 — One canonical raw lake shared by both repos

- Status: accepted
- Date: 2026-06-04

## Context

`dotaml-turbo` held the raw match parquets inside its own tree: ~94 GB pre-patch
history (`data/history/turbo`) + ~81 GB patch-7.40 snapshot
(`data/snapshots/7.40-2025-12-16/raw/turbo`). `dotaml-live` needs the same matches to
bootstrap its rolling window and will pull *new* matches going forward. Keeping raw
inside one repo while a second repo consumes (and extends) it invites duplication and
an unclear source of truth.

Investigation showed the move is low-risk: the raw dirs are plain directories, `raw`
appears only as a `deps:` path in turbo's `dvc.yaml` (not a cached DVC output, no
`.dvc/cache`, no remote), and both repos sit on the same SN850X filesystem (so a move
is an instant rename). The user chose a single shared lake with turbo hard-repointed.

## Decision

Establish **`~/projects/dota-datalake`** as the canonical raw lake — owned by neither
repo, mirroring the upstream Azure account name. All raw bytes live there:

```
~/projects/dota-datalake/turbo/{history, snapshot-7.40, live}/
    year=YYYY/month=MM/day=DD/matches_*.parquet
```

- **Moved** turbo's `data/history/turbo` → `turbo/history` and
  `…/raw/turbo` → `turbo/snapshot-7.40` (175 GB, instant same-FS rename).
- **Hard-repointed** turbo: its 7 experiment `raw_roots` configs and the `dvc.yaml`
  dep now use the absolute lake paths (pathlib makes the absolute root win the
  `PROJECT_ROOT / r` join, so the builders resolve them correctly). Historical prose
  (READMEs, NOTES, proposals) was left as an accurate record of the past layout.
- **dotaml-live** references `turbo/history` + `turbo/snapshot-7.40` read-only and
  appends new matches to `turbo/live/` (the Phase-4 Azure consumer).
- Raw remains transient downstream: `raw.retain_days` prunes `live/` partitions once
  features are built. Derived parquets stay in each repo's processed tree (turbo's
  `*_extended` processed parquets did **not** move).

## Consequences

- One source of truth; no duplicate ~175 GB copy. dotaml-live's net new raw is only
  the live tail.
- The lake is not a git repo; it is data (DVC/backups), never committed.
- Depends on the same-filesystem invariant only for the *move* (already done) and for
  cheap future seeding. Consumers use absolute lake paths, so they work regardless.
- Supersedes the earlier draft of this ADR (in-place reference + hardlink seeding);
  the single-lake move makes that unnecessary.
