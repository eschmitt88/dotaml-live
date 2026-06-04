# 0003 — Don't duplicate raw match storage; reference turbo's mirror in place

- Status: accepted
- Date: 2026-06-04

## Context

`dotaml-turbo` holds a large local mirror of the 7.40 snapshot on the SN850X:
**~81 GB raw turbo parquets** + **~9.4 GB derived** `*_extended` parquets. `dotaml-live`
needs the same historical matches to bootstrap its rolling window and continuous
training. Both repos sit on the **same filesystem** (`/dev/nvme1n1` → `/mnt/projects`).
Naively re-pulling the blob window or rebuilding the derived parquets would duplicate
~90 GB for no benefit.

## Decision

`dotaml-live` does **not** maintain its own copy of the historical raw mirror or
derived parquets.

1. **Historical raw → reference in place (read-only).** `pipeline.yaml:raw.historical_roots`
   points the vendored builders' `raw_roots` list at turbo's existing
   `data/history/turbo` and `…/snapshots/7.40-2025-12-16/raw/turbo`. Read-only; never
   copied or written.
2. **Live tail → the only raw dotaml-live owns.** The Phase-4 Azure consumer pulls
   **only days past the snapshot window** into `data/raw_tail/`. It never re-mirrors
   what turbo already has.
3. **Raw is transient.** Once a day is folded into the derived store + aggregator
   state, its raw is prunable. `raw.retain_days` bounds `raw_tail/` to the rolling
   window — steady-state raw footprint is ~window-width, not an ever-growing mirror.
4. **Derived store → seed by hardlink.** `bootstrap.seed_mode: hardlink` seeds the
   rolling store from turbo's `*_extended` parquets via hardlinks (same FS → shared
   inodes, ~0 bytes), then appends only new days. `copy`/`rebuild` remain available
   if the repos ever move to separate volumes.

## Consequences

- dotaml-live's net new storage is its registry (tens of MB) + the live tail +
  newly-appended derived days — not a second 90 GB copy.
- This depends on the same-filesystem invariant. If dotaml-live moves to a different
  volume, switch `seed_mode` to `copy` and give `raw.historical_roots` a synced path;
  hardlinks/in-place references won't span filesystems.
- Reading turbo's raw/derived in place is the "read the snapshot once as data"
  allowance of ADR 0001 — it does not make dotaml-live import turbo's code at runtime.
