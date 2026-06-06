"""Smoke/regression tests for the dashboard API — runs on the live (v7-base) model.

Requires the bootstrap artifacts (scripts/bootstrap_from_snapshot.py) to exist in
registry/v7-base. Skips gracefully if the checkpoint or artifacts are absent.
"""

from __future__ import annotations

import pytest

from dotaml_live.common import paths

pytestmark = pytest.mark.skipif(
    not paths.model_pt(paths.live_model_dir()).exists()
    or not paths.player_feature_store(paths.live_model_dir()).exists(),
    reason="live checkpoint or bootstrap artifacts missing",
)


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from dotaml_live.serving.app import create_app
    return TestClient(create_app())


@pytest.fixture(scope="module")
def draft():
    from dotaml_live.queries.lookups import hero_id
    return [hero_id(n) for n in [
        "Anti-Mage", "Drow Ranger", "Zeus", "Rubick", "Mars",
        "Crystal Maiden", "Shadow Fiend", "Puck", "Pudge", "Sniper"]]


def test_health_model_meta(client):
    from dotaml_live.training import registry
    assert client.get("/health").json()["status"] == "ok"
    m = client.get("/model").json()
    # serves whatever is currently promoted (v7-base, or a fine-tuned ft-* model)
    assert m["version"] == (registry.live_version() or "v7-base")
    assert m["item_vocab_size"] == 305
    meta = client.get("/meta").json()
    assert len(meta["heroes"]) > 100 and len(meta["items"]) > 100


def test_winprob(client, draft):
    r = client.post("/api/winprob", json={"heroes": draft}).json()
    assert 0.0 <= r["radiant_win_prob"] <= 1.0
    assert r["predicted_duration_min"] > 0


def test_win_vs_duration(client, draft):
    r = client.post("/api/win-vs-duration",
                    json={"heroes": draft, "duration_minutes": [20, 30, 40]}).json()
    assert len(r["curve"]) == 3
    assert all(0.0 <= p["win_prob"] <= 1.0 for p in r["curve"])


def test_hero_picks(client, draft):
    r = client.post("/api/hero-picks",
                    json={"known_radiant": draft[:4], "known_dire": draft[5:],
                          "my_side": "radiant", "top_k": 5}).json()
    assert len(r["picks"]) == 5
    assert r["picks"] == sorted(r["picks"], key=lambda p: -p["mean_winprob"])


def test_item_build(client, draft):
    r = client.post("/api/item-build", json={"heroes": draft, "my_slot": 0, "t_max": 35}).json()
    assert len(r["actions"]) > 0
    assert len(r["final_inventory"]) <= 6


def test_hero_combos_modes(client, draft):
    syn = client.post("/api/hero-combos",
                      json={"pool": draft, "size": 2, "mode": "synergy", "top_k": 5}).json()
    assert syn["combos"] and all(c["joint_winprob"] is not None for c in syn["combos"])
    kpm = client.post("/api/hero-combos",
                      json={"pool": draft, "size": 2, "mode": "kills_per_min", "top_k": 3}).json()
    assert kpm["combos"] and all(c["kills_per_min"] is not None for c in kpm["combos"])
