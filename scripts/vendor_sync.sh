#!/usr/bin/env bash
# Re-vendor v7 model/builder/query code from the dotaml-turbo research repo.
# See VENDOR.md. Pinned source commit recorded there; this copies the raw files.
# After running, review `git diff` and re-apply any hardening edits the upstream
# change disturbed (registry-relative paths, artifact reads).
set -euo pipefail

TURBO="${1:-$HOME/projects/research/dotaml-turbo}"
V7="$TURBO/experiments/2026-05-26-v7-unified-masked-multitask-740"
LIVE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKG="$LIVE/src/dotaml_live"

echo "Vendoring from: $TURBO ($(git -C "$TURBO" rev-parse --short HEAD))"

cp "$V7/models.py"                 "$PKG/model/models.py"
cp "$V7/serve/v7_inference.py"     "$PKG/model/v7_inference.py"
cp "$V7/serve/queries.py"          "$PKG/queries/queries.py"
cp "$V7/serve/build_optimizer.py"  "$PKG/queries/build_optimizer.py"
cp "$V7/serve/lookups.py"          "$PKG/queries/lookups.py"
cp "$V7/serve/heroes.json"         "$PKG/queries/heroes.json"
cp "$V7/serve/items.json"          "$PKG/queries/items.json"
cp "$TURBO/experiments/2026-05-24-foundation-v3-740/build_features_extended.py"  "$PKG/features/"
cp "$TURBO/experiments/2026-05-24-foundation-v3-740/build_rich_cols_extended.py" "$PKG/features/"
cp "$V7/train.py"  "$PKG/training/train.py"
cp "$V7/data.py"   "$PKG/training/data.py"
cp "$V7/mae.py"    "$PKG/training/mae.py"
cp "$V7/probes.py" "$PKG/training/probes.py"

echo "Done. Review 'git diff' and re-apply hardening edits noted in VENDOR.md."
