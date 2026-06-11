"""Detached pipeline runner for the feedback queue.

Each stage runs as its own transient systemd user unit (its own cgroup), so it
survives dashboard restarts — including the restart that `accept` itself
triggers at the end. The sidecar JSON in data/feedback/ is the only shared
state; the API process just reads it.

Stages (python -m dotaml_live.serving.feedback_runner <stage> <id>):

    intake     voice → transcript (faster-whisper), then claude triage → ticket
    implement  git worktree + branch, claude implements the ticket, builds the
               SPA, starts a dev preview server on a free port (8091–8099)
    accept     stop preview, merge branch into master, rebuild main SPA,
               drop worktree+branch, restart the live dashboard service

The claude CLI is invoked headless (-p). Triage gets read-only tools to ground
the ticket in real file paths; implement runs with permissions skipped, which
is acceptable because it is confined to a throwaway worktree on this LAN-only
box and every change still passes through human review on the dev preview.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from ..common import config, paths
from . import feedback_store as store

REPO = paths.REPO_ROOT
WORKTREES = REPO / ".worktrees"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cfg() -> dict:
    return config.serving_config().get("feedback") or {}


def _claude_bin() -> str:
    return shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")


def _runner_env() -> dict:
    env = dict(os.environ)
    env["PATH"] = f"{Path.home()}/.local/bin:/usr/local/bin:/usr/bin:/bin"
    # the runner's own PYTHONPATH (pinned by spawn_stage) must not leak into
    # claude's shell — a `pytest` run inside a worktree would import this tree
    env.pop("PYTHONPATH", None)
    return env


# ---------------------------------------------------------------- spawning

def spawn_stage(stage: str, fid: str) -> None:
    """Launch a stage detached from the calling (dashboard) process.

    systemd-run gives the runner its own cgroup so `systemctl restart
    dotaml-live-dashboard` can't kill it; plain Popen is the fallback.

    The runner is pinned to the spawning process's code (PYTHONPATH) and data
    (DOTAML_*): a dev-preview instance must run its own worktree's stages, not
    whatever the main checkout's editable install happens to be.
    """
    cmd = [sys.executable, "-m", "dotaml_live.serving.feedback_runner", stage, fid]
    unit = f"dotaml-feedback-{stage}-{fid}"
    env = {
        "PATH": f"{Path.home()}/.local/bin:/usr/local/bin:/usr/bin:/bin",
        "PYTHONPATH": str(paths.SRC_DIR),
        "DOTAML_DATA": str(paths.DATA_DIR),
        "DOTAML_REGISTRY": str(paths.REGISTRY_DIR),
    }
    sd = ["systemd-run", "--user", "--collect", f"--unit={unit}",
          f"--working-directory={REPO}",
          *[f"--setenv={k}={v}" for k, v in env.items()],
          *cmd]
    try:
        subprocess.run(sd, check=True, capture_output=True, timeout=15)
    except (subprocess.SubprocessError, FileNotFoundError):
        subprocess.Popen(cmd, cwd=str(REPO), start_new_session=True,
                         env={**os.environ, **env},
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop_dev_server(meta: dict) -> None:
    dev = meta.get("dev") or {}
    # also stop the deterministic unit name, in case a crash left dev unset
    units = {dev.get("unit"), f"dotaml-feedback-dev-{meta['id']}" if meta.get("id") else None}
    for unit in filter(None, units):
        subprocess.run(["systemctl", "--user", "stop", unit],
                       capture_output=True, timeout=30)
    if dev.get("pid"):
        try:
            os.kill(int(dev["pid"]), 15)
        except (ProcessLookupError, PermissionError, ValueError):
            pass


def cleanup_workspace(fid: str) -> None:
    """Stop the preview and drop the worktree + branch. Safe to call anytime."""
    meta = store.load(fid)
    stop_dev_server(meta)
    if meta.get("worktree") and Path(meta["worktree"]).exists():
        subprocess.run(["git", "worktree", "remove", "--force", meta["worktree"]],
                       cwd=str(REPO), capture_output=True, timeout=60)
    if meta.get("branch"):
        subprocess.run(["git", "branch", "-D", meta["branch"]],
                       cwd=str(REPO), capture_output=True, timeout=30)
    store.update(fid, dev=None)


# ---------------------------------------------------------------- claude calls

TRIAGE_PROMPT = """\
You triage user feedback for dotaml-live, a personal Dota 2 draft-analysis web
dashboard (FastAPI backend in src/dotaml_live/, React+Vite SPA in frontend/src/
with Draft analysis / Combo discovery / Screenshots / Feedback tabs). Turn the
raw feedback below — possibly a rough voice transcript — into ONE concise,
well-scoped improvement ticket. You may read the repo to ground file references.

FEEDBACK (verbatim):
\"\"\"{raw}\"\"\"

Reply with ONLY a JSON object, no fences, no prose:
{{"title": "<imperative, max 8 words>",
  "summary": "<1-2 sentences: what the user wants and why>",
  "details": "<concrete guidance for the implementer: what to change and where (real paths), behavior, edge cases>",
  "area": "<frontend|backend|model|pipeline|other>",
  "acceptance": ["<up to 4 short, testable criteria>"]}}
"""


def _claude_triage(raw_text: str) -> dict:
    fb = _cfg()
    cmd = [_claude_bin(), "-p", TRIAGE_PROMPT.format(raw=raw_text),
           "--output-format", "json",
           "--allowedTools", "Read", "Glob", "Grep",
           "--model", fb.get("triage_model", "sonnet")]
    out = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                         timeout=600, env=_runner_env())
    if out.returncode != 0:
        raise RuntimeError(f"claude triage failed: {out.stderr.strip()[:500]}")
    result = json.loads(out.stdout).get("result", "")
    m = re.search(r"\{.*\}", result, re.DOTALL)
    if not m:
        raise RuntimeError(f"triage returned no JSON: {result[:300]}")
    ticket = json.loads(m.group(0))
    for k in ("title", "summary", "details", "area", "acceptance"):
        ticket.setdefault(k, "" if k != "acceptance" else [])
    ticket["title"] = str(ticket["title"]).strip()[:80] or "Untitled improvement"
    return ticket


IMPLEMENT_PROMPT = """\
You are the automated implementer for the dotaml-live repo, working inside a
dedicated git worktree (your current directory) on branch {branch}.
Implement exactly the approved ticket below — nothing more.

TICKET {fid}
title: {title}
summary: {summary}
details: {details}
area: {area}
acceptance criteria:
{acceptance}

ORIGINAL USER FEEDBACK (verbatim, may be a rough voice transcript):
\"\"\"{raw}\"\"\"
{comments_section}
REPO ORIENTATION
- FastAPI backend: src/dotaml_live/serving/ (app.py, routes/, schemas.py); domain
  logic in src/dotaml_live/queries|model|features|pipeline.
- React SPA: frontend/src/App.jsx, api.js, styles.css (Vite, dark theme — reuse
  the existing CSS variables and component patterns).
- Tests: tests/ (pytest).

RULES
- Modify files ONLY inside this worktree. Never touch data/, registry/, or the
  main checkout at {repo}.
- Match the existing code style; keep the change minimal and focused.
- If you change Python, run: PYTHONPATH={wt}/src {python} -m pytest tests/ -q
- If you change the frontend, rebuild it:
  cd frontend && npm install --no-audit --no-fund && npm run build
  (node_modules is pre-seeded; the build must succeed.)
- Commit ALL changes on this branch: git add -A && git commit -m "feedback {fid}: {title}"
- Finally write .feedback-summary.md in the worktree root (do NOT commit it):
  2-6 markdown bullets — what changed and exactly how to test it in the
  dashboard UI at the dev preview.
"""


COMMENTS_SECTION = """
## Follow-up Comments
The user posted these comments on the ticket after reviewing it (possibly after
a previous implementation pass). They refine or correct the ticket above —
incorporate them into this pass:
{comments}
"""


def _comments_section(meta: dict) -> str:
    lines = []
    for c in meta.get("comments") or []:
        text = (c.get("text") or "").strip()
        if not text:
            continue
        lines.append(f"- [{c.get('at', '?')}, {c.get('source', 'text')}] {text}")
    if not lines:
        return ""
    return COMMENTS_SECTION.format(comments="\n".join(lines))


def _fmt_stream_line(line: str) -> list[str]:
    """Render one claude stream-json line as human-readable log lines."""
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        return [line.rstrip()] if line.strip() else []
    out = []
    if ev.get("type") == "assistant":
        for block in ev.get("message", {}).get("content", []):
            if block.get("type") == "text" and block.get("text", "").strip():
                out.append(block["text"].strip())
            elif block.get("type") == "tool_use":
                inp = json.dumps(block.get("input", {}))
                out.append(f"▸ {block.get('name')} {inp[:160]}")
    elif ev.get("type") == "result":
        cost = ev.get("total_cost_usd")
        out.append(f"— done: {ev.get('subtype')}"
                   + (f" (${cost:.2f})" if isinstance(cost, (int, float)) else ""))
    return out


def _claude_implement(meta: dict, wt: Path, log_file: Path) -> dict:
    fb = _cfg()
    t = meta["ticket"]
    prompt = IMPLEMENT_PROMPT.format(
        branch=meta["branch"], fid=meta["id"], title=t["title"], summary=t["summary"],
        details=t["details"], area=t["area"],
        acceptance="\n".join(f"- {a}" for a in t["acceptance"]) or "- (none given)",
        raw=meta.get("raw_text") or "", comments_section=_comments_section(meta),
        repo=REPO, wt=wt, python=sys.executable)
    cmd = [_claude_bin(), "-p", prompt, "--dangerously-skip-permissions",
           "--output-format", "stream-json", "--verbose"]
    if fb.get("implement_model"):
        cmd += ["--model", fb["implement_model"]]
    timeout = float(fb.get("implement_timeout_minutes", 60)) * 60

    info = {"started": _now_iso(), "cost_usd": None}
    deadline = time.monotonic() + timeout
    with open(log_file, "a") as lf:
        lf.write(f"=== implement run {_now_iso()} (branch {meta['branch']}) ===\n")
        lf.flush()
        proc = subprocess.Popen(cmd, cwd=str(wt), text=True, env=_runner_env(),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for line in proc.stdout:
            if time.monotonic() > deadline:
                proc.kill()
                raise RuntimeError(f"implementation timed out after {timeout / 60:.0f} min")
            try:
                ev = json.loads(line)
                if ev.get("type") == "result":
                    info["cost_usd"] = ev.get("total_cost_usd")
            except json.JSONDecodeError:
                pass
            for fmt in _fmt_stream_line(line):
                lf.write(fmt + "\n")
            lf.flush()
        rc = proc.wait(timeout=60)
    if rc != 0:
        raise RuntimeError(f"claude implement exited {rc} — see log")
    info["finished"] = _now_iso()
    return info


# ---------------------------------------------------------------- stages

def stage_intake(fid: str) -> None:
    meta = store.update(fid, runner_pid=os.getpid())
    if meta.get("audio") and not meta.get("raw_text"):
        store.set_status(fid, "transcribing")
        from . import transcribe
        text = transcribe_file_safe(transcribe, store.audio_path(fid))
        meta = store.update(fid, raw_text=text)
    if not (meta.get("raw_text") or "").strip():
        raise RuntimeError("empty feedback — nothing transcribed/typed")
    store.set_status(fid, "triaging")
    ticket = _claude_triage(meta["raw_text"])
    store.update(fid, ticket=ticket)
    store.set_status(fid, "triaged")


def transcribe_file_safe(transcribe_mod, path: Path) -> str:
    text = transcribe_mod.transcribe_file(path)
    if not text:
        raise RuntimeError("transcription produced no text — re-record?")
    return text


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:32] or "change"


def _free_port(taken: set[int], prefer: int | None = None) -> int:
    lo, hi = (_cfg().get("dev_ports") or [8091, 8099])
    candidates = ([int(prefer)] if prefer else []) + list(range(int(lo), int(hi) + 1))
    for port in candidates:
        if port in taken:
            continue
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError(f"no free dev port in {lo}-{hi}")


def _ensure_spa_built(wt: Path, log_file: Path) -> None:
    if (wt / "frontend" / "dist" / "index.html").exists():
        return
    with open(log_file, "a") as lf:
        lf.write("— building SPA (dist missing in worktree)\n")
        lf.flush()
        # hardlink-copy node_modules if the implementer didn't install its own
        if not (wt / "frontend" / "node_modules").exists() and \
           (REPO / "frontend" / "node_modules").exists():
            subprocess.run(["cp", "-al", str(REPO / "frontend" / "node_modules"),
                            str(wt / "frontend" / "node_modules")], check=True, timeout=120)
        subprocess.run(["npm", "run", "build"], cwd=str(wt / "frontend"), check=True,
                       stdout=lf, stderr=subprocess.STDOUT, timeout=600, env=_runner_env())


def _start_dev_server(fid: str, wt: Path, prefer_port: int | None = None) -> dict:
    taken = {(m.get("dev") or {}).get("port") for m in store.list_items()}
    port = _free_port({p for p in taken if p}, prefer=prefer_port)
    unit = f"dotaml-feedback-dev-{fid}"
    boot = ("import uvicorn; from dotaml_live.serving.app import create_app; "
            f"uvicorn.run(create_app(), host='0.0.0.0', port={port})")
    cmd = ["systemd-run", "--user", "--collect", f"--unit={unit}",
           f"--working-directory={wt}",
           f"--setenv=PYTHONPATH={wt}/src",
           f"--setenv=DOTAML_DATA={REPO}/data",
           f"--setenv=DOTAML_REGISTRY={REPO}/registry",
           "--setenv=DOTAML_DEV_PREVIEW=1",
           sys.executable, "-c", boot]
    subprocess.run(cmd, check=True, capture_output=True, timeout=15)
    deadline = time.monotonic() + 180          # model load can take a while
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2):
                return {"port": port, "unit": unit}
        except OSError:
            time.sleep(2)
    subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True)
    raise RuntimeError("dev preview server failed its health check — see journalctl "
                       f"--user -u {unit}")


def stage_implement(fid: str) -> None:
    meta = store.load(fid)
    if not meta.get("ticket"):
        raise RuntimeError("no ticket — run triage first")
    store.update(fid, runner_pid=os.getpid())
    store.set_status(fid, "implementing")

    # re-implement: keep the dev preview URL stable by reusing the prior port
    prev_port = (meta.get("dev") or {}).get("port")

    slug = _slug(meta["ticket"]["title"])
    branch = f"feedback/{fid.rsplit('-', 1)[-1]}-{slug}"
    wt = WORKTREES / f"feedback-{fid}"
    cleanup_workspace(fid)                      # stop old preview, drop worktree+branch
    WORKTREES.mkdir(exist_ok=True)
    subprocess.run(["git", "worktree", "add", "-b", branch, str(wt), "master"],
                   cwd=str(REPO), check=True, capture_output=True, timeout=120)
    meta = store.update(fid, branch=branch, worktree=str(wt), impl=None)

    # voice comments need a transcript before they can go into the prompt
    comments = meta.get("comments") or []
    if any(c.get("audio") and not c.get("text") for c in comments):
        from . import transcribe
        for i, c in enumerate(comments):
            if c.get("audio") and not c.get("text"):
                c["text"] = transcribe.transcribe_file(store.comment_audio_path(fid, i))
        meta = store.update(fid, comments=comments)

    # seed node_modules so the implementer's npm install/build is fast
    if (REPO / "frontend" / "node_modules").exists():
        subprocess.run(["cp", "-al", str(REPO / "frontend" / "node_modules"),
                        str(wt / "frontend" / "node_modules")], timeout=120)

    log_file = store.log_path(fid)
    info = _claude_implement(meta, wt, log_file)

    n = subprocess.run(["git", "rev-list", "--count", "master..HEAD"], cwd=str(wt),
                       capture_output=True, text=True, timeout=30)
    commits = int(n.stdout.strip() or 0)
    if commits == 0:
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=str(wt),
                               capture_output=True, text=True, timeout=30).stdout.strip()
        dirty = "\n".join(l for l in dirty.splitlines()
                          if ".feedback-summary.md" not in l and "node_modules" not in l)
        if dirty:
            subprocess.run(["git", "add", "-A", ":!.feedback-summary.md"], cwd=str(wt),
                           capture_output=True, timeout=30)
            subprocess.run(["git", "commit", "-m",
                            f"feedback {fid}: {meta['ticket']['title']} (auto-commit)"],
                           cwd=str(wt), capture_output=True, timeout=30)
            commits = 1
        else:
            raise RuntimeError("implementer produced no changes — see log")
    info["commits"] = commits

    summary_p = wt / ".feedback-summary.md"
    info["summary"] = summary_p.read_text().strip() if summary_p.exists() else None

    _ensure_spa_built(wt, log_file)
    dev = _start_dev_server(fid, wt, prefer_port=prev_port)
    store.update(fid, impl=info, dev=dev)
    store.set_status(fid, "implemented")


def stage_accept(fid: str) -> None:
    meta = store.load(fid)
    if meta["status"] not in ("implemented", "accepting"):
        raise RuntimeError(f"cannot accept from status {meta['status']}")
    store.update(fid, runner_pid=os.getpid())
    store.set_status(fid, "accepting")
    stop_dev_server(meta)

    title = meta["ticket"]["title"]
    merge = subprocess.run(
        ["git", "merge", "--no-ff", meta["branch"], "-m", f"feedback: {title} ({fid})"],
        cwd=str(REPO), capture_output=True, text=True, timeout=120)
    if merge.returncode != 0:
        subprocess.run(["git", "merge", "--abort"], cwd=str(REPO),
                       capture_output=True, timeout=60)
        raise RuntimeError(f"merge failed:\n{merge.stdout}\n{merge.stderr}")

    with open(store.log_path(fid), "a") as lf:
        lf.write(f"=== accept {_now_iso()}: merged {meta['branch']}, rebuilding SPA ===\n")
        lf.flush()
        subprocess.run(["npm", "install", "--no-audit", "--no-fund"],
                       cwd=str(REPO / "frontend"), check=True, stdout=lf,
                       stderr=subprocess.STDOUT, timeout=600, env=_runner_env())
        subprocess.run(["npm", "run", "build"], cwd=str(REPO / "frontend"), check=True,
                       stdout=lf, stderr=subprocess.STDOUT, timeout=600, env=_runner_env())

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO),
                          capture_output=True, text=True, timeout=30).stdout.strip()
    cleanup_workspace(fid)
    store.update(fid, merge_commit=head, worktree=None)
    store.set_status(fid, "done")
    # safe: this runner lives in its own transient unit, not the dashboard's cgroup
    subprocess.run(["systemctl", "--user", "restart", "dotaml-live-dashboard"],
                   capture_output=True, timeout=120)


STAGES = {"intake": stage_intake, "implement": stage_implement, "accept": stage_accept}


def main() -> None:
    stage, fid = sys.argv[1], sys.argv[2]
    fn = STAGES.get(stage)
    if fn is None:
        store.set_status(fid, "failed",
                         f"stage '{stage}' unknown to the runner's code at "
                         f"{Path(__file__).resolve().parent} — spawned by a newer API?")
        sys.exit(2)
    try:
        fn(fid)
    except Exception as e:                     # noqa: BLE001 — surface in the queue UI
        store.set_status(fid, "failed", f"{stage}: {e}")
        raise


if __name__ == "__main__":
    main()
