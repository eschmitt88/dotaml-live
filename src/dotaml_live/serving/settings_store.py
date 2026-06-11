"""Tiny JSON settings store — dashboard preferences that must survive page
reloads, dev-preview origins, and server restarts (e.g. the players' account
IDs, which used to live only in per-origin localStorage and vanished on every
preview port).

    data/settings.json    one flat dict, shallow-merged on every POST

The main app and the feedback dev previews share one file: previews run with
DOTAML_DATA pointed at the main repo's data/ directory.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading

from ..common import paths

_LOCK = threading.Lock()


def _path():
    return paths.DATA_DIR / "settings.json"


def load() -> dict:
    try:
        return json.loads(_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def update(partial: dict) -> dict:
    """Shallow-merge `partial` into the stored settings; a None value deletes
    the key. Returns the full settings dict after the merge."""
    with _LOCK:
        cur = load()
        for k, v in partial.items():
            if v is None:
                cur.pop(k, None)
            else:
                cur[k] = v
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".settings-")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(cur, f, indent=2)
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return cur
