"""Holds the live V7Foundation and hot-reloads it when the registry 'live'
pointer (or its checkpoint) changes — so a promotion takes effect without
restarting the server.
"""

from __future__ import annotations

import json
import threading

import torch

from ..common import paths
from ..model.v7_inference import V7Foundation


def _resolve_device(cfg: str) -> str:
    if cfg and cfg != "auto":
        return cfg
    if torch.cuda.is_available():
        try:                                   # self-test (guards unsupported archs)
            x = torch.randn(8, 8, device="cuda")
            _ = (x @ x).sum().item()
            return "cuda"
        except Exception:
            return "cpu"
    return "cpu"


class ModelHolder:
    def __init__(self, device_cfg: str = "auto"):
        self.device = _resolve_device(device_cfg)
        self._lock = threading.Lock()
        self._f: V7Foundation | None = None
        self._dir = None
        self._mtime = None
        self.reload()

    def reload(self) -> None:
        with self._lock:
            model_dir = paths.live_model_dir()
            self._f = V7Foundation(model_dir=model_dir, device=self.device)
            self._dir = model_dir
            try:
                self._mtime = paths.model_pt(model_dir).stat().st_mtime
            except OSError:
                self._mtime = None

    def maybe_reload(self) -> bool:
        """Cheap stat check; reload if the live pointer or checkpoint moved."""
        model_dir = paths.live_model_dir()
        try:
            mtime = paths.model_pt(model_dir).stat().st_mtime
        except OSError:
            return False
        if model_dir != self._dir or mtime != self._mtime:
            self.reload()
            return True
        return False

    @property
    def f(self) -> V7Foundation:
        assert self._f is not None
        return self._f

    def info(self) -> dict:
        version = self._dir.name if self._dir else None
        manifest_path = paths.manifest_json(self._dir) if self._dir else None
        manifest = {}
        if manifest_path and manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text())
            except Exception:
                manifest = {}
        return {
            "version": version,
            "device": self.device,
            "model_dir": str(self._dir) if self._dir else None,
            "item_vocab_size": self._f.item_vocab_size if self._f else None,
            "manifest": manifest,
        }
