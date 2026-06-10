"""Sidecar store for the user-feedback → improvement-ticket queue.

A feedback item is spoken or typed into the dashboard's Feedback tab and moves
through a small status machine, driven by detached runner processes
(feedback_runner.py) so dashboard restarts never orphan a stage:

    data/feedback/<id>.json        sidecar (single source of truth)
    data/feedback/<id>.webm|m4a    voice memo as recorded (voice items)
    data/feedback/<id>.impl.log    formatted log of the implementation run

Status machine:

    captured → transcribing → triaging → triaged ──(reject)──→ rejected
                                            │
                                        (approve)
                                            ↓
                 failed ←───────────── implementing → implemented ──(discard)──→ discarded
                                                          │
                                                      (accept)
                                                          ↓
                                                      accepting → done

`failed` can be reached from any runner stage; /retry re-enters the pipeline at
the right place. Items in ACTIVE statuses carry the runner's pid so the API can
detect a dead runner and surface it as `failed`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ..common import paths

FEEDBACK_DIR = paths.DATA_DIR / "feedback"
_FID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{8}$")

STATUSES = {"captured", "transcribing", "triaging", "triaged", "rejected",
            "implementing", "implemented", "accepting", "done", "discarded", "failed"}
# statuses owned by a live runner process (carry runner_pid)
ACTIVE = {"transcribing", "triaging", "implementing", "accepting"}
# statuses where deleting the item is safe
TERMINAL = {"triaged", "rejected", "done", "discarded", "failed", "captured"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _check_fid(fid: str) -> str:
    if not _FID_RE.match(fid):
        raise ValueError(f"bad feedback id: {fid!r}")
    return fid


def _meta_path(fid: str) -> Path:
    return FEEDBACK_DIR / f"{_check_fid(fid)}.json"


def log_path(fid: str) -> Path:
    return FEEDBACK_DIR / f"{_check_fid(fid)}.impl.log"


def _audio_ext(data: bytes) -> str:
    if data[:4] == b"\x1aE\xdf\xa3":
        return "webm"                      # EBML → webm/opus (Chrome, Firefox)
    if data[4:8] == b"ftyp":
        return "m4a"                       # MP4 container (Safari)
    if data[:4] == b"OggS":
        return "ogg"
    return "webm"


def load(fid: str) -> dict:
    p = _meta_path(fid)
    if not p.exists():
        raise KeyError(fid)
    return json.loads(p.read_text())


def save(meta: dict) -> dict:
    """Atomic write — the API process and a detached runner may both write."""
    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    p = _meta_path(meta["id"])
    fd, tmp = tempfile.mkstemp(dir=str(FEEDBACK_DIR), suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(meta, indent=1))
    os.replace(tmp, p)
    return meta


def update(fid: str, **fields) -> dict:
    meta = load(fid)
    meta.update(fields)
    return save(meta)


def set_status(fid: str, status: str, error: str | None = None) -> dict:
    assert status in STATUSES, status
    meta = load(fid)
    meta["status"] = status
    meta["error"] = error
    meta.setdefault("history", []).append({"status": status, "at": _now_iso()})
    return save(meta)


def new_item(source: str, text: str | None = None, audio: bytes | None = None) -> dict:
    """Create a `captured` item. Content-hash dedup like the screenshot store."""
    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    payload = audio if audio is not None else (text or "").encode()
    h = hashlib.sha256(payload).hexdigest()[:8]
    existing = sorted(FEEDBACK_DIR.glob(f"*-{h}.json"))
    if existing:
        return json.loads(existing[-1].read_text())

    fid = f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{h}"
    audio_name = None
    if audio is not None:
        audio_name = f"{fid}.{_audio_ext(audio)}"
        (FEEDBACK_DIR / audio_name).write_bytes(audio)
    meta = {
        "id": fid,
        "created": _now_iso(),
        "source": source,                  # "voice" | "text"
        "audio": audio_name,
        "raw_text": text,                  # transcript fills this for voice items
        "status": "captured",
        "error": None,
        "ticket": None,                    # {title, summary, details, area, acceptance[]}
        "branch": None,
        "worktree": None,
        "impl": None,                      # {started, finished, commits, summary, cost_usd}
        "dev": None,                       # {port, unit, url_path}
        "merge_commit": None,
        "comments": [],                    # [{at, source, text, audio}]
        "runner_pid": None,
        "history": [{"status": "captured", "at": _now_iso()}],
    }
    return save(meta)


def list_items() -> list[dict]:
    """All sidecars, newest first."""
    if not FEEDBACK_DIR.exists():
        return []
    items = []
    for p in sorted(FEEDBACK_DIR.glob("*.json"), reverse=True):
        if _FID_RE.match(p.stem):
            items.append(json.loads(p.read_text()))
    return items


def audio_path(fid: str) -> Path:
    meta = load(fid)
    if not meta.get("audio"):
        raise KeyError(fid)
    return FEEDBACK_DIR / meta["audio"]


def add_comment(fid: str, text: str | None = None, audio: bytes | None = None) -> dict:
    """Append a targeted comment (typed or voice memo) to one feedback item."""
    meta = load(fid)
    comments = meta.setdefault("comments", [])
    audio_name = None
    if audio is not None:
        audio_name = f"{fid}.c{len(comments)}.{_audio_ext(audio)}"
        (FEEDBACK_DIR / audio_name).write_bytes(audio)
    comments.append({
        "at": _now_iso(),
        "source": "voice" if audio is not None else "text",
        "text": text,
        "audio": audio_name,
    })
    return save(meta)


def comment_audio_path(fid: str, idx: int) -> Path:
    comments = load(fid).get("comments") or []
    if not (0 <= idx < len(comments)) or not comments[idx].get("audio"):
        raise KeyError(fid)
    return FEEDBACK_DIR / comments[idx]["audio"]


def delete_item(fid: str) -> None:
    meta = load(fid)
    if meta.get("audio"):
        (FEEDBACK_DIR / meta["audio"]).unlink(missing_ok=True)
    for c in meta.get("comments") or []:
        if c.get("audio"):
            (FEEDBACK_DIR / c["audio"]).unlink(missing_ok=True)
    log_path(fid).unlink(missing_ok=True)
    _meta_path(fid).unlink(missing_ok=True)


def reconcile() -> None:
    """Mark items whose runner died mid-stage as failed (called from the list API)."""
    for meta in list_items():
        if meta["status"] in ACTIVE and meta.get("runner_pid"):
            try:
                os.kill(int(meta["runner_pid"]), 0)
            except (ProcessLookupError, PermissionError):
                set_status(meta["id"], "failed",
                           f"runner died during '{meta['status']}' — use retry")
