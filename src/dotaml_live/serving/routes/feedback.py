"""Feedback-queue endpoints: capture (text/voice) → ticket → approve →
implement → dev-preview → accept/discard. State lives in data/feedback/
sidecars; the heavy stages run in detached runner units (feedback_runner)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse

from .. import feedback_store as store
from ..feedback_runner import cleanup_workspace, spawn_stage
from ..schemas import FeedbackRejectReq, FeedbackTextReq

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


def _get(fid: str) -> dict:
    try:
        return store.load(fid)
    except (KeyError, ValueError):
        raise HTTPException(404, f"unknown feedback item {fid}")


@router.post("/text")
def feedback_text(req: FeedbackTextReq):
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "empty feedback text")
    meta = store.new_item("text", text=text)
    if meta["status"] == "captured":
        spawn_stage("intake", meta["id"])
    return meta


@router.post("/audio")
async def feedback_audio(request: Request):
    """Raw voice-memo bytes (MediaRecorder webm/opus, Safari m4a)."""
    data = await request.body()
    if len(data) < 1000:
        raise HTTPException(400, "audio too short — hold the mic a bit longer")
    meta = store.new_item("voice", audio=data)
    if meta["status"] == "captured":
        spawn_stage("intake", meta["id"])
    return meta


@router.get("")
def feedback_list():
    store.reconcile()
    return {"items": store.list_items()}


@router.get("/{fid}/log")
def feedback_log(fid: str, tail: int = 400):
    _get(fid)
    p = store.log_path(fid)
    if not p.exists():
        return PlainTextResponse("")
    lines = p.read_text(errors="replace").splitlines()
    return PlainTextResponse("\n".join(lines[-tail:]))


@router.get("/{fid}/audio")
def feedback_audio_file(fid: str):
    try:
        p = store.audio_path(fid)
    except (KeyError, ValueError):
        raise HTTPException(404, f"no audio for {fid}")
    return FileResponse(p)


@router.post("/{fid}/comment")
async def feedback_comment(fid: str, request: Request):
    """Targeted comment on one ticket — JSON {text} or raw voice-memo bytes."""
    meta = _get(fid)
    if meta["status"] in ("done", "rejected", "discarded"):
        raise HTTPException(409, f"cannot comment on an item in status {meta['status']}")
    if (request.headers.get("content-type") or "").startswith("application/json"):
        body = await request.json()
        text = (body.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "empty comment text")
        return store.add_comment(fid, text=text)
    data = await request.body()
    if len(data) < 1000:
        raise HTTPException(400, "audio too short — hold the mic a bit longer")
    return store.add_comment(fid, audio=data)


@router.get("/{fid}/comment/{idx}/audio")
def feedback_comment_audio(fid: str, idx: int):
    try:
        p = store.comment_audio_path(fid, idx)
    except (KeyError, ValueError):
        raise HTTPException(404, f"no audio for comment {idx} on {fid}")
    return FileResponse(p)


@router.post("/{fid}/approve")
def feedback_approve(fid: str):
    meta = _get(fid)
    if meta["status"] != "triaged":
        raise HTTPException(409, f"cannot approve from status {meta['status']}")
    spawn_stage("implement", fid)
    return store.update(fid, error=None)


@router.post("/{fid}/reject")
def feedback_reject(fid: str, req: FeedbackRejectReq | None = None):
    meta = _get(fid)
    if meta["status"] not in ("triaged", "failed", "captured"):
        raise HTTPException(409, f"cannot reject from status {meta['status']}")
    store.update(fid, reject_reason=(req.reason if req else None))
    return store.set_status(fid, "rejected")


@router.post("/{fid}/accept")
def feedback_accept(fid: str):
    meta = _get(fid)
    if meta["status"] != "implemented":
        raise HTTPException(409, f"cannot accept from status {meta['status']}")
    spawn_stage("accept", fid)
    return meta


@router.post("/{fid}/discard")
def feedback_discard(fid: str):
    meta = _get(fid)
    if meta["status"] not in ("implemented", "failed", "triaged"):
        raise HTTPException(409, f"cannot discard from status {meta['status']}")
    cleanup_workspace(fid)
    return store.set_status(fid, "discarded")


@router.post("/{fid}/retry")
def feedback_retry(fid: str):
    """Re-enter the pipeline at the right place after a failure."""
    meta = _get(fid)
    if meta["status"] not in ("failed", "captured", "discarded"):
        raise HTTPException(409, f"cannot retry from status {meta['status']}")
    if meta.get("ticket"):
        spawn_stage("implement", fid)
    else:
        spawn_stage("intake", fid)
    return store.update(fid, error=None)


@router.delete("/{fid}")
def feedback_delete(fid: str):
    meta = _get(fid)
    if meta["status"] not in store.TERMINAL:
        raise HTTPException(409, f"cannot delete an item in status {meta['status']}")
    cleanup_workspace(fid)
    store.delete_item(fid)
    return {"deleted": fid}
