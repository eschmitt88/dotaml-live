"""Feedback-queue endpoints: capture (text/voice) → ticket → approve →
implement → dev-preview → accept/discard. State lives in data/feedback/
sidecars; the heavy stages run in detached runner units (feedback_runner)."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse

from .. import feedback_store as store
from ..feedback_runner import cleanup_workspace, merge_probe, spawn_stage
from ..schemas import FeedbackRejectReq, FeedbackTextReq

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


def _get(fid: str) -> dict:
    try:
        return store.load(fid)
    except (KeyError, ValueError):
        raise HTTPException(404, f"unknown feedback item {fid}")


def _control_only() -> None:
    """Approve/reject/accept/retry/discard mutate the main checkout's workspace;
    a dev preview spawning them would tear down the very worktree it serves
    from. Previews are for capturing feedback and testing — curation happens on
    the main dashboard."""
    if os.environ.get("DOTAML_DEV_PREVIEW"):
        raise HTTPException(409, "this is a dev preview — approve / accept / "
                                 "retry / discard from the main dashboard (:8090)")


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
    items = store.list_items()
    for it in items:
        # would this branch still merge cleanly into master? (cached dry-run)
        if it["status"] in ("implemented", "failed") and it.get("branch"):
            it["merge_probe"] = merge_probe(it["branch"])
    return {"items": items}


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
    """Targeted comment on one ticket — JSON {text} or raw voice-memo bytes.
    Spawns a background pass that transcribes voice comments and folds the
    comment into a revised ticket right away (no retry needed to see either)."""
    meta = _get(fid)
    if meta["status"] in ("done", "rejected", "discarded"):
        raise HTTPException(409, f"cannot comment on an item in status {meta['status']}")
    if (request.headers.get("content-type") or "").startswith("application/json"):
        body = await request.json()
        text = (body.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "empty comment text")
        meta = store.add_comment(fid, text=text)
    else:
        data = await request.body()
        if len(data) < 1000:
            raise HTTPException(400, "audio too short — hold the mic a bit longer")
        meta = store.add_comment(fid, audio=data)
    spawn_stage("comment", fid)
    return meta


@router.get("/{fid}/comment/{idx}/audio")
def feedback_comment_audio(fid: str, idx: int):
    try:
        p = store.comment_audio_path(fid, idx)
    except (KeyError, ValueError):
        raise HTTPException(404, f"no audio for comment {idx} on {fid}")
    return FileResponse(p)


@router.post("/{fid}/approve")
def feedback_approve(fid: str):
    _control_only()
    meta = _get(fid)
    if meta["status"] != "triaged":
        raise HTTPException(409, f"cannot approve from status {meta['status']}")
    spawn_stage("implement", fid)
    return store.update(fid, error=None)


@router.post("/{fid}/reject")
def feedback_reject(fid: str, req: FeedbackRejectReq | None = None):
    _control_only()
    meta = _get(fid)
    if meta["status"] not in ("triaged", "failed", "captured"):
        raise HTTPException(409, f"cannot reject from status {meta['status']}")
    store.update(fid, reject_reason=(req.reason if req else None))
    return store.set_status(fid, "rejected")


@router.post("/{fid}/accept")
def feedback_accept(fid: str):
    _control_only()
    meta = _get(fid)
    if meta["status"] != "implemented":
        raise HTTPException(409, f"cannot accept from status {meta['status']}")
    spawn_stage("accept", fid)
    return meta


@router.post("/{fid}/resolve")
def feedback_resolve(fid: str):
    """Fold current master into the ticket's branch, claude resolving conflicts
    in the worktree (master is never touched). Cheaper than a full re-implement
    when other accepted tickets made the branch stale."""
    _control_only()
    meta = _get(fid)
    if meta["status"] not in ("implemented", "failed"):
        raise HTTPException(409, f"cannot resolve from status {meta['status']}")
    if not (meta.get("branch") and meta.get("worktree")
            and Path(meta["worktree"]).is_dir()):
        raise HTTPException(409, "no live worktree for this ticket — use retry instead")
    spawn_stage("resolve", fid)
    return store.update(fid, error=None)


@router.post("/{fid}/discard")
def feedback_discard(fid: str):
    _control_only()
    meta = _get(fid)
    if meta["status"] not in ("implemented", "failed", "triaged"):
        raise HTTPException(409, f"cannot discard from status {meta['status']}")
    cleanup_workspace(fid)
    return store.set_status(fid, "discarded")


@router.post("/{fid}/retry")
def feedback_retry(fid: str):
    """Re-enter the pipeline at the right place after a failure, or re-run the
    coding pass on an implemented item (e.g. after posting follow-up comments) —
    the runner stops the old preview and reuses its port."""
    _control_only()
    meta = _get(fid)
    if meta["status"] not in ("failed", "captured", "discarded", "implemented"):
        raise HTTPException(409, f"cannot retry from status {meta['status']}")
    if meta.get("ticket"):
        spawn_stage("implement", fid)
    else:
        spawn_stage("intake", fid)
    return store.update(fid, error=None)


@router.delete("/{fid}")
def feedback_delete(fid: str):
    _control_only()
    meta = _get(fid)
    if meta["status"] not in store.TERMINAL:
        raise HTTPException(409, f"cannot delete an item in status {meta['status']}")
    cleanup_workspace(fid)
    store.delete_item(fid)
    return {"deleted": fid}
