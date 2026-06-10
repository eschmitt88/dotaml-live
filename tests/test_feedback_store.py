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


def test_status_validation():
    meta = store.new_item("text", text="y")
    with pytest.raises(AssertionError):
        store.set_status(meta["id"], "nonsense")
