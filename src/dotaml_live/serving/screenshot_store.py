"""Sidecar store for pasted screenshots + ground-truth draft labels.

Every screenshot POSTed to /api/draft-from-screenshot is persisted here so the
detector can be calibrated/evaluated against real Dota captures later:

    data/screenshots/<id>.png|jpg     the image as received
    data/screenshots/<id>.json       sidecar: detection output + ground truth

A sidecar with ``ground_truth: null`` is an item in the labeling queue. Labels
come from either:
  * a human — the SPA's "confirm ground truth" flow on the Draft tab, or the
    Screenshots tab's review button; or
  * Claude — a session reads the unlabeled images visually and POSTs
    /api/screenshots/<id>/label with labeled_by="claude".

Ground truth is the full 10-slot draft (radiant[5] + dire[5], 0 = slot empty
in the screenshot). scripts/eval_screenshot_detector.py replays the detector
over all labeled shots and reports accuracy.

Duplicate pastes are deduped by content hash (same image → same shot id).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from ..common import paths

SHOTS_DIR = paths.DATA_DIR / "screenshots"
_SID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{8}$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _check_sid(sid: str) -> str:
    if not _SID_RE.match(sid):
        raise ValueError(f"bad shot id: {sid!r}")
    return sid


def _meta_path(sid: str) -> Path:
    return SHOTS_DIR / f"{_check_sid(sid)}.json"


def _load(sid: str) -> dict:
    p = _meta_path(sid)
    if not p.exists():
        raise KeyError(sid)
    return json.loads(p.read_text())


def _ext_from_magic(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:2] == b"\xff\xd8":
        return "jpg"
    return "img"


def image_path(sid: str) -> Path:
    meta = _load(sid)
    return SHOTS_DIR / meta["image"]


def save_shot(data: bytes, detection: dict) -> dict:
    """Persist image + detection sidecar; returns the sidecar dict.

    Content-hash dedup: re-pasting the same screenshot returns the existing
    record (preserving any ground truth already attached to it).
    """
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256(data).hexdigest()[:8]
    existing = sorted(SHOTS_DIR.glob(f"*-{h}.json"))
    if existing:
        return json.loads(existing[-1].read_text())

    sid = f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{h}"
    ext = _ext_from_magic(data)
    (SHOTS_DIR / f"{sid}.{ext}").write_bytes(data)
    meta = {
        "id": sid,
        "image": f"{sid}.{ext}",
        "created": _now_iso(),
        "detected": detection,
        "ground_truth": None,
        "labeled_by": None,
        "labeled_at": None,
    }
    _meta_path(sid).write_text(json.dumps(meta, indent=1))
    return meta


def list_shots(status: str = "all") -> list[dict]:
    """All sidecars, newest first. status: all | unlabeled | labeled."""
    if not SHOTS_DIR.exists():
        return []
    shots = [json.loads(p.read_text()) for p in sorted(SHOTS_DIR.glob("*.json"), reverse=True)]
    if status == "unlabeled":
        shots = [s for s in shots if s["ground_truth"] is None]
    elif status == "labeled":
        shots = [s for s in shots if s["ground_truth"] is not None]
    return shots


def set_label(sid: str, radiant: list[int], dire: list[int], labeled_by: str) -> dict:
    if len(radiant) != 5 or len(dire) != 5:
        raise ValueError("ground truth must be 5 radiant + 5 dire hero IDs (0 = empty slot)")
    meta = _load(sid)
    meta["ground_truth"] = {"radiant": [int(x) for x in radiant],
                            "dire": [int(x) for x in dire]}
    meta["labeled_by"] = labeled_by
    meta["labeled_at"] = _now_iso()
    _meta_path(sid).write_text(json.dumps(meta, indent=1))
    return meta


def delete_shot(sid: str) -> None:
    meta = _load(sid)
    (SHOTS_DIR / meta["image"]).unlink(missing_ok=True)
    _meta_path(sid).unlink(missing_ok=True)
