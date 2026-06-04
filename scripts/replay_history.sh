#!/usr/bin/env bash
# One-time full-history replay: build the complete rolling store + aggregator state
# from the canonical lake (history + snapshot-7.40, ~175 GB raw). This is the
# multi-hour job that seeds the aggregator; afterwards the nightly retrain only
# processes the new live tail (O(new matches)).
#
# Launched unbuffered (python -u) + nohup per ~/.claude/CLAUDE.md ML-job rules so
# the log streams and the run survives logout. Poll log.txt; resumable — re-run to
# continue from the last persisted watermark.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
mkdir -p data/logs
LOG="data/logs/replay_$(date +%Y%m%d_%H%M%S).log"
echo "replay -> $LOG  (tail -f to watch; resumable on re-run)"
nohup .venv/bin/python -u -m dotaml_live.pipeline.build_runner "$@" > "$LOG" 2>&1 &
echo "pid $! ; log $LOG"
