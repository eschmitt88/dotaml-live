"""Voice-memo transcription via faster-whisper (CTranslate2 Whisper).

Chosen over NVIDIA Parakeet/NeMo for this box: pip-only install, MIT license,
decodes the browser's MediaRecorder output (webm/opus, Safari m4a) directly
through bundled PyAV — no system ffmpeg — and Whisper remains the robustness
champion on noisy/accented speech. Model weights download once into
data/feedback/whisper/ (kept on the SN850X, not the OS drive).

The model is lazy-loaded on first use; at a few clips a day there is no reason
to hold ~3 GB of VRAM permanently, so the runner process loads it, transcribes,
and exits. Tries CUDA fp16 first, falls back to CPU int8 (fine for 10–60 s
clips) if CTranslate2 has no kernel for the GPU arch.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..common import config, paths

log = logging.getLogger(__name__)


def transcribe_file(path: str | Path) -> str:
    from faster_whisper import WhisperModel

    fb = config.serving_config().get("feedback") or {}
    name = fb.get("whisper_model", "large-v3-turbo")
    device = fb.get("whisper_device", "cpu")
    root = str(paths.DATA_DIR / "feedback" / "whisper")

    def _run(model):
        segments, _ = model.transcribe(str(path), vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()

    if device != "cpu":
        try:
            return _run(WhisperModel(name, device="cuda", compute_type="float16",
                                     download_root=root))
        except Exception as e:             # noqa: BLE001 — CUDA/arch issues → CPU retry
            log.warning("cuda transcription failed (%s); retrying on cpu int8", e)
    return _run(WhisperModel(name, device="cpu", compute_type="int8", download_root=root))
