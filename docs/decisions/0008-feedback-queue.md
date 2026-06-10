# 0008 — Self-improving feedback queue (voice/text → ticket → claude → dev preview)

Date: 2026-06-10
Status: accepted

## Context

The dashboard accumulates improvement ideas faster than they get written down.
We want: speak or type feedback into the app itself, have it turned into a
concise ticket automatically, approve with one click, have the change
implemented autonomously, test it on a separate served instance, and accept it
into the live dashboard — with a visible record of what was completed.

## Decision

**Storage** — `data/feedback/<id>.json` sidecars (same pattern as the
screenshot queue; ADR-free filesystem store, no DB). Status machine:
`captured → transcribing → triaging → triaged → implementing → implemented →
accepting → done`, with `rejected | discarded | failed` exits.

**Transcription** — `faster-whisper` (`large-v3-turbo`, CPU int8 by default).
Chosen over the higher-accuracy NVIDIA Parakeet/NeMo stack because it is
pip-only (MIT), decodes browser MediaRecorder webm/opus and Safari m4a directly
via bundled PyAV (no system ffmpeg), and Whisper is the robustness champion on
noisy speech. CPU int8 because the venv's torch is cu13 while CTranslate2 wants
cuBLAS 12; a 60 s memo transcribes in seconds anyway. Weights live in
`data/feedback/whisper/` (SN850X, not the OS drive).

**Agent stages** — headless `claude -p` via detached **transient systemd user
units** (`systemd-run --user`), so a stage has its own cgroup and survives
dashboard restarts — including the restart that `accept` itself triggers.
Sidecar JSON is the only shared state; the API detects dead runners by pid.

- *triage*: read-only tools (`Read,Glob,Grep`), returns strict JSON
  (title ≤ 8 words, summary, details, area, acceptance criteria).
- *implement*: runs in a fresh **git worktree** under `.worktrees/feedback-<id>`
  on branch `feedback/<hash>-<slug>` with `--dangerously-skip-permissions`.
  Acceptable: LAN-only personal box, throwaway worktree, and every change still
  passes human review on the dev preview before merging. node_modules is
  hardlink-seeded (`cp -al`) for fast SPA builds.
- *accept*: merge `--no-ff` into master, rebuild the main SPA, drop
  worktree+branch, restart `dotaml-live-dashboard`.

**Dev preview** — after implementation, a second server instance is started
from the worktree on a free port in 8091–8099 (`PYTHONPATH=<worktree>/src`
overrides the editable install — verified; `DOTAML_DATA`/`DOTAML_REGISTRY`
point at the main checkout so it serves the live model). The Feedback tab links
to it for human testing; Accept/Discard tears it down.

**UI** — a fourth dashboard tab. Composer (textarea + MediaRecorder mic),
grouped queue (needs attention / awaiting approval / working / completed /
archive), live-tailing implementation log, action-needed badge on the tab.

## Consequences

- Voice capture requires a secure context: use `http://localhost:8090` or add
  the LAN origin to `chrome://flags/#unsafely-treat-insecure-origin-as-secure`.
- Merges land on local master un-pushed (agency: standard — user pushes).
- One implement run ≈ one claude session of cost; cost is recorded per ticket
  (`impl.cost_usd`) from the CLI result event.
- Parakeet-TDT remains the documented upgrade path if transcription accuracy
  ever disappoints (~6 % vs ~7.4 % avg WER, but NeMo dependency tree + ffmpeg).
