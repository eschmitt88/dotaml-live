"""Feedback-queue sidecar store: lifecycle, dedup, reconcile."""

import pytest

from dotaml_live.serving import feedback_store as store


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "FEEDBACK_DIR", tmp_path / "feedback")


def test_text_item_lifecycle():
    meta = store.new_item("text", text="make the win prob bar bigger")
    fid = meta["id"]
    assert meta["status"] == "captured"
    assert meta["raw_text"] == "make the win prob bar bigger"
    assert meta["audio"] is None

    store.set_status(fid, "triaging")
    store.update(fid, ticket={"title": "Enlarge win prob bar", "summary": "s",
                              "details": "d", "area": "frontend", "acceptance": []})
    meta = store.set_status(fid, "triaged")
    assert meta["ticket"]["title"] == "Enlarge win prob bar"
    assert [h["status"] for h in meta["history"]] == ["captured", "triaging", "triaged"]

    assert store.list_items()[0]["id"] == fid
    store.delete_item(fid)
    assert store.list_items() == []


def test_dedup_same_text():
    a = store.new_item("text", text="same feedback")
    b = store.new_item("text", text="same feedback")
    assert a["id"] == b["id"]
    assert len(store.list_items()) == 1


def test_voice_item_audio_ext():
    webm = b"\x1aE\xdf\xa3" + b"\x00" * 64
    meta = store.new_item("voice", audio=webm)
    assert meta["audio"].endswith(".webm")
    assert store.audio_path(meta["id"]).read_bytes() == webm
    assert meta["raw_text"] is None


def test_bad_id_rejected():
    with pytest.raises(ValueError):
        store.load("../../etc/passwd")


def test_reconcile_marks_dead_runner_failed():
    meta = store.new_item("text", text="x")
    store.update(meta["id"], runner_pid=2 ** 22 + 12345)  # certainly not alive
    store.set_status(meta["id"], "implementing")
    store.reconcile()
    out = store.load(meta["id"])
    assert out["status"] == "failed"
    assert "runner died" in out["error"]


def test_comments_text_and_voice():
    meta = store.new_item("text", text="needs a comment")
    fid = meta["id"]
    assert meta["comments"] == []

    store.add_comment(fid, text="the button is still too small")
    webm = b"\x1aE\xdf\xa3" + b"\x00" * 64
    meta = store.add_comment(fid, audio=webm)

    assert [c["source"] for c in meta["comments"]] == ["text", "voice"]
    assert meta["comments"][0]["text"] == "the button is still too small"
    assert store.comment_audio_path(fid, 1).read_bytes() == webm
    with pytest.raises(KeyError):
        store.comment_audio_path(fid, 0)   # text comment has no audio

    audio_file = store.comment_audio_path(fid, 1)
    store.delete_item(fid)
    assert not audio_file.exists()


def test_status_validation():
    meta = store.new_item("text", text="y")
    with pytest.raises(AssertionError):
        store.set_status(meta["id"], "nonsense")


def test_comments_section_in_implement_prompt():
    from dotaml_live.serving import feedback_runner as runner

    # no comments → empty section, prompt unchanged
    assert runner._comments_section({"comments": []}) == ""
    assert runner._comments_section({}) == ""
    # untranscribed voice comment (no text yet) contributes nothing
    assert runner._comments_section(
        {"comments": [{"at": "t", "source": "voice", "text": None, "audio": "a.webm"}]}) == ""

    section = runner._comments_section(
        {"comments": [{"at": "2026-06-10T20:00:00Z", "source": "text",
                       "text": "make the bar red instead"}]})
    assert "## Follow-up Comments" in section
    assert "make the bar red instead" in section

    meta = {"comments": [{"at": "t", "source": "text", "text": "extra note"}]}
    prompt = runner.IMPLEMENT_PROMPT.format(
        branch="b", fid="f", title="t", summary="s", details="d", area="backend",
        acceptance="- a", raw="raw fb", comments_section=runner._comments_section(meta),
        worktree_state="", repo="/r", wt="/w", python="py")
    assert "## Follow-up Comments" in prompt
    assert "extra note" in prompt
    # without comments the rendered prompt is byte-identical to the old one
    bare = runner.IMPLEMENT_PROMPT.format(
        branch="b", fid="f", title="t", summary="s", details="d", area="backend",
        acceptance="- a", raw="raw fb", comments_section="",
        worktree_state="", repo="/r", wt="/w", python="py")
    assert "Follow-up" not in bare


def test_worktree_state_section_in_implement_prompt(tmp_path, monkeypatch):
    import subprocess

    from dotaml_live.serving import feedback_runner as runner

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    git("init", "-b", "master")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (repo / "f.txt").write_text("a\n")
    git("add", "-A")
    git("commit", "-m", "base")
    git("checkout", "-b", "feedback/x-test")
    (repo / "f.txt").write_text("b\n")
    git("add", "-A")
    git("commit", "-m", "change f")
    monkeypatch.setattr(runner, "REPO", repo)

    section = runner._worktree_state_section("feedback/x-test")
    assert "## Current worktree state" in section
    assert "change f" in section          # git log --oneline
    assert "f.txt" in section             # git diff --stat

    prompt = runner.IMPLEMENT_PROMPT.format(
        branch="b", fid="f", title="t", summary="s", details="d", area="backend",
        acceptance="- a", raw="raw fb", comments_section="",
        worktree_state=section, repo="/r", wt="/w", python="py")
    assert "## Current worktree state" in prompt

    # fresh ticket: no branch, branch even with master, or unknown branch → ''
    assert runner._worktree_state_section(None) == ""
    assert runner._worktree_state_section("master") == ""
    assert runner._worktree_state_section("no-such-branch") == ""


def test_stage_comment_transcribes_and_revises(monkeypatch):
    from dotaml_live.serving import feedback_runner as runner
    from dotaml_live.serving import transcribe

    meta = store.new_item("text", text="original feedback")
    fid = meta["id"]
    ticket = {"title": "Old title", "summary": "s", "details": "d",
              "area": "backend", "acceptance": []}
    store.update(fid, ticket=ticket)
    store.add_comment(fid, audio=b"\x1aE\xdf\xa3" + b"\x00" * 64)

    monkeypatch.setattr(transcribe, "transcribe_file", lambda p: "voice transcript")
    revised = dict(ticket, title="Revised title")
    seen = {}
    monkeypatch.setattr(runner, "_claude_revise", lambda m: seen.update(m) or revised)

    runner.stage_comment(fid)
    out = store.load(fid)
    assert out["comments"][0]["text"] == "voice transcript"      # transcript stored
    assert out["ticket"]["title"] == "Revised title"             # revision applied
    assert seen["comments"][0]["text"] == "voice transcript"     # revise saw transcript
    assert out["status"] == "captured"                           # status untouched
    assert out["error"] is None

    # a failing revision records an error but never fails the item
    def boom(m):
        raise RuntimeError("revision exploded")
    monkeypatch.setattr(runner, "_claude_revise", boom)
    store.add_comment(fid, text="another comment")
    runner.stage_comment(fid)
    out = store.load(fid)
    assert out["status"] == "captured"
    assert "revision exploded" in out["error"]

    # next successful pass clears the comment-stage error
    monkeypatch.setattr(runner, "_claude_revise", lambda m: revised)
    runner.stage_comment(fid)
    assert store.load(fid)["error"] is None


def test_merge_probe(tmp_path, monkeypatch):
    """Dry-run conflict detection against a scratch repo, plus head-pair cache."""
    import subprocess
    from dotaml_live.serving import feedback_runner as runner

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    git("init", "-b", "master")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (repo / "f.txt").write_text("base\n")
    git("add", "-A")
    git("commit", "-m", "base")
    git("checkout", "-b", "feat")
    (repo / "f.txt").write_text("feature side\n")
    git("commit", "-am", "feature change")
    git("checkout", "master")

    monkeypatch.setattr(runner, "REPO", repo)
    runner._PROBE_CACHE.clear()

    assert runner.merge_probe("feat") == {"clean": True, "conflicts": []}
    assert runner.merge_probe(None) is None
    assert runner.merge_probe("no-such-branch") is None

    # master moves onto the same lines → probe flips to conflict (cache must
    # not serve the stale clean verdict)
    (repo / "f.txt").write_text("master side\n")
    git("commit", "-am", "master change")
    probe = runner.merge_probe("feat")
    assert probe["clean"] is False
    assert probe["conflicts"] == ["f.txt"]
