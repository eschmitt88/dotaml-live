#!/usr/bin/env bash
# Unattended continuation, to run once BOTH background jobs are done:
#   - replay_full  (feature history over history+snapshot, ends at 2026-03-09)
#   - blob_pull    (live tail 2026-03-10..today into the lake)
#
# It (1) resumes build_runner so it folds the freshly-pulled live tail into the
# rolling store + aggregator (O(new matches) from the watermark), bringing features
# fully current, then (2) runs the value-parity check against dotaml-turbo.
# Idempotent and resumable — safe to re-run.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
. .venv/bin/activate
mkdir -p data/logs
LOG="data/logs/post_replay_$(date +%Y%m%d_%H%M%S).log"
{
  echo "== extend rolling store with live tail =="
  python -u -m dotaml_live.pipeline.build_runner
  echo "== value-parity check vs dotaml-turbo =="
  python -u scripts/parity_check.py
} 2>&1 | tee "$LOG"
echo "post-replay done -> $LOG"
