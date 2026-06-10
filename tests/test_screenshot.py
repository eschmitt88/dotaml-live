"""Screenshot draft-detection tests — synthetic screenshots composed from the
same CDN portraits the matcher templates come from, laid out on the measured
slot grids (screenshot.LAYOUTS), including multi-monitor captures where the
game is not centered in the image.

Requires data/hero_portraits/ (scripts/fetch_hero_portraits.py); skips if absent.
"""

from __future__ import annotations

import numpy as np
import pytest

from dotaml_live.common import paths

PORTRAITS = paths.DATA_DIR / "hero_portraits"

pytestmark = pytest.mark.skipif(
    not (PORTRAITS / "1.png").exists(),
    reason="hero portraits missing — run scripts/fetch_hero_portraits.py",
)

DRAFT = [1, 6, 22, 86, 129, 5, 11, 13, 14, 35]


def synth(draft, W=1920, H=1080, layout="topbar", game_cx=None, n_drawn=10, seed=0):
    """Compose a fake screenshot: noisy background + the 10 slot portraits on
    layout geometry, centered at game_cx (defaults to W/2)."""
    import cv2
    from dotaml_live.serving import screenshot as sc
    L = next(l for l in sc.LAYOUTS if l["name"] == layout)
    rng = np.random.default_rng(seed)
    img = rng.integers(15, 60, (H, W, 3)).astype(np.uint8)

    w = L["w_h"] * H
    p = L["p_w"] * w
    g = L["g_p"] * p
    y = L["y_h"] * H
    cx = W / 2 if game_cx is None else game_cx
    r5x = cx - g / 2
    centers = [r5x - (4 - i) * p for i in range(5)] + [r5x + g + i * p for i in range(5)]

    pw, ph = int(round(w)), int(round(w * 144 / 256))
    for k, (hid, c) in enumerate(zip(draft, centers)):
        if k >= n_drawn:
            continue
        x0, y0 = int(round(c - pw / 2)), int(round(y - ph / 2))
        tile = cv2.imread(str(PORTRAITS / f"{hid}.png"))
        img[y0:y0 + ph, x0:x0 + pw] = cv2.resize(tile, (pw, ph), interpolation=cv2.INTER_AREA)
    return img


@pytest.mark.parametrize("kw", [
    dict(W=1920, H=1080, layout="topbar"),               # single monitor, in-game
    dict(W=2560, H=1440, layout="topbar"),                # 1440p
    dict(W=1920, H=1080, layout="strategy"),              # pregame screen
    dict(W=3840, H=1080, layout="topbar", game_cx=960),   # dual monitor, game left
    dict(W=3840, H=1080, layout="strategy", game_cx=2880),  # dual monitor, game right
])
def test_detects_full_draft(kw):
    from dotaml_live.serving import screenshot as sc
    out = sc.detect_draft(synth(DRAFT, **kw))
    assert out["radiant"] == DRAFT[:5]
    assert out["dire"] == DRAFT[5:]
    assert out["layout"] == kw["layout"]


def test_partial_draft_pads_with_zero():
    from dotaml_live.serving import screenshot as sc
    out = sc.detect_draft(synth(DRAFT, n_drawn=7))   # 5 radiant + 2 dire picked
    assert out["radiant"] == DRAFT[:5]
    assert out["dire"] == DRAFT[5:7] + [0, 0, 0]


def test_empty_screen_detects_nothing():
    from dotaml_live.serving import screenshot as sc
    out = sc.detect_draft(synth(DRAFT, n_drawn=0))
    assert out["radiant"] == [0] * 5 and out["dire"] == [0] * 5
    assert out["detections"] == []


def test_jpeg_roundtrip_via_bytes():
    import cv2
    from dotaml_live.serving import screenshot as sc
    ok, enc = cv2.imencode(".jpg", synth(DRAFT), [cv2.IMWRITE_JPEG_QUALITY, 80])
    assert ok
    out = sc.detect_draft_bytes(enc.tobytes())
    assert out["radiant"] == DRAFT[:5] and out["dire"] == DRAFT[5:]


def test_bad_bytes_raise():
    from dotaml_live.serving import screenshot as sc
    with pytest.raises(ValueError):
        sc.detect_draft_bytes(b"not an image")


def test_harvest_variants(tmp_path, monkeypatch):
    """A slot rendered with unknown art is learned from ground truth, then
    matches on the next detection."""
    import cv2
    from dotaml_live.serving import screenshot as sc

    monkeypatch.setattr(sc, "VARIANTS_DIR", tmp_path / "variants")
    sc._TPL_CACHE = None

    img = synth(DRAFT)
    # repaint slot R1 with art the matcher can't know (inverted portrait)
    L = next(l for l in sc.LAYOUTS if l["name"] == "topbar")
    w = L["w_h"] * 1080
    p, g, y = L["p_w"] * w, L["g_p"] * L["p_w"] * w, L["y_h"] * 1080
    r1 = 960 - g / 2 - 4 * p
    pw, ph = int(round(w)), int(round(w * 144 / 256))
    x0, y0 = int(round(r1 - pw / 2)), int(round(y - ph / 2))
    img[y0:y0 + ph, x0:x0 + pw] = 255 - img[y0:y0 + ph, x0:x0 + pw]

    before = sc.detect_draft(img)
    assert before["radiant"][0] != DRAFT[0]          # unknown art -> miss

    written = sc.harvest_variants(img, DRAFT[:5], DRAFT[5:], tag="test")
    assert len(written) == 1 and written[0].name.startswith(f"{DRAFT[0]}-")

    after = sc.detect_draft(img)
    assert after["radiant"] == DRAFT[:5] and after["dire"] == DRAFT[5:]
    sc._TPL_CACHE = None


def test_api_endpoint_and_label_queue(tmp_path, monkeypatch):
    import cv2
    from fastapi.testclient import TestClient
    from dotaml_live.serving import screenshot_store
    from dotaml_live.serving.app import create_app

    monkeypatch.setattr(screenshot_store, "SHOTS_DIR", tmp_path)
    client = TestClient(create_app())
    ok, enc = cv2.imencode(".png", synth(DRAFT))

    # detection + auto-save into the labeling queue
    r = client.post("/api/draft-from-screenshot", content=enc.tobytes())
    assert r.status_code == 200
    body = r.json()
    assert body["radiant"] == DRAFT[:5] and body["dire"] == DRAFT[5:]
    sid = body["shot_id"]
    assert sid and body["already_labeled"] is False
    assert client.post("/api/draft-from-screenshot", content=b"junk").status_code == 400

    # re-pasting the same bytes dedups to the same shot
    assert client.post("/api/draft-from-screenshot",
                       content=enc.tobytes()).json()["shot_id"] == sid

    # queue listing + image retrieval
    shots = client.get("/api/screenshots?status=unlabeled").json()["shots"]
    assert [s["id"] for s in shots] == [sid]
    img = client.get(f"/api/screenshots/{sid}/image")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"

    # label it -> moves to labeled, dedup now reports already_labeled
    r = client.post(f"/api/screenshots/{sid}/label",
                    json={"radiant": DRAFT[:5], "dire": DRAFT[5:], "labeled_by": "claude"})
    assert r.status_code == 200 and r.json()["labeled_by"] == "claude"
    assert client.get("/api/screenshots?status=unlabeled").json()["shots"] == []
    assert client.get("/api/screenshots?status=labeled").json()["shots"][0]["id"] == sid
    assert client.post("/api/draft-from-screenshot",
                       content=enc.tobytes()).json()["already_labeled"] is True

    # delete clears both image and sidecar
    assert client.delete(f"/api/screenshots/{sid}").status_code == 200
    assert client.get("/api/screenshots").json()["shots"] == []
    assert client.get(f"/api/screenshots/{sid}/image").status_code == 404
    assert client.get("/api/screenshots/../../etc/passwd/image").status_code == 404
