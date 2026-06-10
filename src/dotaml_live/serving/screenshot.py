"""Draft detection from a Dota 2 screenshot.

Multi-scale template matching of hero portraits against the top strip of a
screenshot, then slot-grid fitting. Works on the in-game topbar and the
pregame/strategy screen, including ultrawide and multi-monitor captures
(the game's horizontal position is inferred, never assumed to be centered).

Pipeline:
  1. Coarse scan — match every hero template across a height-relative scale
     sweep over the top strip; collect high-confidence ANCHORS (stock-art
     portraits match 0.83-0.98 at the right scale).
  2. Layout classification — portrait width / slot pitch / clock gap follow
     one of two measured layouts (LAYOUTS below, calibrated on real
     3840x1080 captures): the in-game topbar or the strategy screen.
  3. Grid fit — anchors pin the two banks of five slots; missing slots
     (heroes not yet picked, or rendered with alternate-avatar cosmetics)
     are located geometrically, trying every feasible slot assignment and
     keeping the hypothesis whose 10 slot crops score best.
  4. Per-slot resolution — each slot crop is matched against ALL templates
     (stock portraits + harvested variants) and takes the argmax.

Alternate avatars (persona/arcana cosmetics swap the portrait art entirely)
cannot match stock CDN art; harvest_variants() crops such slots out of
ground-truth-labeled screenshots (the labeling queue, see screenshot_store)
into data/hero_portraits/variants/, after which those skins match like any
stock portrait. scripts/eval_screenshot_detector.py --harvest drives this.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from pathlib import Path

import cv2
import numpy as np

from ..common import paths
from ..queries.lookups import hero_id_to_name

PORTRAITS_DIR = paths.DATA_DIR / "hero_portraits"
VARIANTS_DIR = PORTRAITS_DIR / "variants"      # harvested alt-avatar crops

# Canonical template geometry. Source art is 256x144 (16:9). Matching uses an
# inner crop: the game trims portrait edges differently per context (slanted
# topbar parallelograms, neighbor overlap, badge overlays at corners).
TPL_W = 56
TPL_H = 32
INNER_X = 8
INNER_Y = 5

# Measured slot-grid layouts (calibrated on real 1080p captures; all values
# scale with capture height H, so monitor count / game position drop out).
#   w_h  — portrait width / H
#   p_w  — slot pitch / portrait width (topbar portraits overlap: < 1)
#   g_p  — clock-gap (R5 center -> D1 center) / pitch
#   y_h  — portrait center y / H
LAYOUTS = (
    {"name": "topbar",   "w_h": 0.0602, "p_w": 0.951, "g_p": 4.375, "y_h": 0.0204},
    {"name": "strategy", "w_h": 0.1074, "p_w": 1.069, "g_p": 3.186, "y_h": 0.0361},
)

# Height-relative scale sweep: portrait width / H, covering both layouts
# with ~4.5% steps (matching is scale-sensitive: ±8% off costs ~0.15 score).
SCALE_FRACS = tuple(0.050 * 1.045 ** i for i in range(19))    # 0.050 .. 0.110+


@dataclass
class Detection:
    hero_id: int
    hero_name: str
    score: float
    x: int              # center, original-image px
    y: int
    width: int          # matched portrait width, original-image px
    side: str           # 'radiant' | 'dire'


# ---------------------------------------------------------------- templates


def _dir_sig() -> tuple:
    sig = []
    for d in (PORTRAITS_DIR, VARIANTS_DIR):
        if d.exists():
            sig += [(p.name, p.stat().st_mtime_ns) for p in sorted(d.glob("*.png"))]
    return tuple(sig)


_TPL_CACHE: tuple[tuple, dict] | None = None


def _to_template(img: np.ndarray) -> np.ndarray:
    img = cv2.resize(img, (TPL_W, TPL_H), interpolation=cv2.INTER_AREA)
    return img[INNER_Y:TPL_H - INNER_Y, INNER_X:TPL_W - INNER_X]


def _templates() -> dict[int, list[np.ndarray]]:
    """hero_id -> [stock template, *variant templates]. Re-reads on dir change."""
    global _TPL_CACHE
    sig = _dir_sig()
    if _TPL_CACHE is not None and _TPL_CACHE[0] == sig:
        return _TPL_CACHE[1]
    out: dict[int, list[np.ndarray]] = {}
    for hid in hero_id_to_name():
        p = PORTRAITS_DIR / f"{hid}.png"
        img = cv2.imread(str(p), cv2.IMREAD_COLOR) if p.exists() else None
        if img is not None:
            out[hid] = [_to_template(img)]
    if VARIANTS_DIR.exists():
        for p in sorted(VARIANTS_DIR.glob("*.png")):
            try:
                hid = int(p.name.split("-", 1)[0])
            except ValueError:
                continue
            img = cv2.imread(str(p), cv2.IMREAD_COLOR)
            if img is not None and hid in out:
                out[hid].append(_to_template(img))
    _TPL_CACHE = (sig, out)
    return out


def templates_available() -> int:
    return len(_templates())


# ------------------------------------------------------------- coarse scan


def _scan_anchors(img: np.ndarray, strip_frac: float, anchor_thresh: float,
                  scale_fracs: tuple[float, ...]) -> list[dict]:
    """Sweep scales over the top strip; return per-hero best matches above
    anchor_thresh: [{hid, score, x, y, w}] in original-image coordinates."""
    tpls = _templates()
    H, W = img.shape[:2]
    strip = img[: max(TPL_H + 1, int(H * strip_frac))]

    best: dict[int, dict] = {}

    def _match(args):
        hid, tpl, small, f = args
        res = cv2.matchTemplate(small, tpl, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        return hid, float(score), loc, tpl.shape, f

    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 4)) as pool:
        for frac in scale_fracs:
            pw = frac * H                    # candidate portrait width, px
            f = TPL_W / pw                   # strip resize factor
            sw, sh = int(round(W * f)), int(round(strip.shape[0] * f))
            if sh < TPL_H or sw < TPL_W:
                continue
            small = cv2.resize(strip, (sw, sh), interpolation=cv2.INTER_AREA)
            jobs = ((hid, ts[0], small, f) for hid, ts in tpls.items())
            for hid, score, loc, tshape, f_ in pool.map(_match, jobs):
                if score <= max(anchor_thresh, best.get(hid, {}).get("score", 0)):
                    continue
                best[hid] = {
                    "hid": hid, "score": score,
                    "x": (loc[0] + tshape[1] / 2) / f_,
                    "y": (loc[1] + tshape[0] / 2) / f_,
                    "w": pw,
                }
    return sorted(best.values(), key=lambda a: -a["score"])


# --------------------------------------------------------------- grid fit


def _consensus_anchors(anchors: list[dict]) -> list[dict]:
    """Keep anchors agreeing with the strongest ones on row y and width."""
    if not anchors:
        return []
    top = anchors[: max(3, len(anchors) // 2)]
    y0 = float(np.median([a["y"] for a in top]))
    w0 = float(np.median([a["w"] for a in top]))
    keep = [a for a in anchors
            if abs(a["y"] - y0) < 0.5 * w0 and 0.8 < a["w"] / w0 < 1.25]
    # spatial NMS: two heroes can't share a slot (false positives over
    # nameplates/badges overlap a real portrait and score lower)
    keep.sort(key=lambda a: -a["score"])
    nms: list[dict] = []
    for a in keep:
        if all(abs(a["x"] - b["x"]) >= 0.55 * w0 for b in nms):
            nms.append(a)
    return sorted(nms, key=lambda a: a["x"])


def _classify_layout(w: float, H: int) -> dict:
    return min(LAYOUTS, key=lambda L: abs(np.log(w / (L["w_h"] * H))))


def _match_slot(img: np.ndarray, x: float, y: float, w: float) -> tuple[int, float]:
    """Argmax hero for one slot: crop around the slot center with margin,
    micro-sweep scale, match every template (stock + variants)."""
    tpls = _templates()
    H, W = img.shape[:2]
    h = w * TPL_H / TPL_W
    x0, x1 = int(max(0, x - 0.65 * w)), int(min(W, x + 0.65 * w))
    y0, y1 = int(max(0, y - 0.75 * h)), int(min(H, y + 0.75 * h))
    crop = img[y0:y1, x0:x1]
    if crop.shape[0] < 8 or crop.shape[1] < 8:
        return 0, 0.0
    best_hid, best_score = 0, 0.0
    for ms in (0.94, 1.0, 1.065):
        f = TPL_W / (w * ms)
        sw, sh = int(round(crop.shape[1] * f)), int(round(crop.shape[0] * f))
        if sw < TPL_W - 2 * INNER_X or sh < TPL_H - 2 * INNER_Y:
            continue
        small = cv2.resize(crop, (sw, sh), interpolation=cv2.INTER_AREA)
        for hid, ts in tpls.items():
            for tpl in ts:
                if small.shape[0] < tpl.shape[0] or small.shape[1] < tpl.shape[1]:
                    continue
                _, score, _, _ = cv2.minMaxLoc(
                    cv2.matchTemplate(small, tpl, cv2.TM_CCOEFF_NORMED))
                if score > best_score:
                    best_hid, best_score = hid, float(score)
    return best_hid, best_score


def _fit_grid(anchors: list[dict], layout: dict, H: int,
              img: np.ndarray) -> list[tuple[float, float, float]] | None:
    """Place the 10 slot centers from consensus anchors. Returns
    [(x, y, w)] * 10 (radiant slots 0-4 left to right, then dire) or None."""
    if len(anchors) < 2:
        return None
    w = float(np.median([a["w"] for a in anchors]))
    y = float(np.median([a["y"] for a in anchors]))
    p = layout["p_w"] * w
    g = layout["g_p"] * p
    xs = [a["x"] for a in anchors]

    def span(group) -> float:
        return group[-1]["x"] - group[0]["x"]

    def slots_from(r5x: float) -> list[tuple[float, float, float]]:
        d1x = r5x + g
        return ([(r5x - (4 - i) * p, y, w) for i in range(5)]
                + [(d1x + i * p, y, w) for i in range(5)])

    def grid_ok(slots, side_anchors, offset) -> bool:
        # a surviving false anchor may not snap — require a 60% majority
        cs = [s[0] for s in slots[offset:offset + 5]]
        snapped = sum(min(abs(a["x"] - c) for c in cs) < 0.25 * p for a in side_anchors)
        return snapped >= max(1, round(0.6 * len(side_anchors)))

    # Candidate grids. A bank holds 5 slots (span <= 4p), and the strategy
    # screen's clock gap (3.19p) is within noise of a 3-slot within-bank gap,
    # so don't classify pairs greedily — enumerate every feasible boundary
    # (and missing-slot split around the clock) and let the slot crops vote.
    cands: list[list[tuple[float, float, float]]] = []
    for i in range(len(xs) - 1):
        left, right = anchors[: i + 1], anchors[i + 1:]
        if span(left) > 4.3 * p or span(right) > 4.3 * p:
            continue
        d = xs[i + 1] - xs[i]
        m = round((d - g) / p)            # slots missing around the clock
        if not (0 <= m <= 8) or abs(d - g - m * p) > 0.30 * p:
            continue
        for a in range(0, min(m, 4) + 1):
            if m - a > 4:
                continue
            slots = slots_from(left[-1]["x"] + a * p)
            if grid_ok(slots, left, 0) and grid_ok(slots, right, 5):
                cands.append(slots)
    if not cands and span(anchors) <= 4.3 * p:
        # all anchors may be a single bank (other side empty or all-cosmetic):
        # try both sides x every alignment of the anchors within the bank
        k = round(span(anchors) / p)      # slots spanned by the anchors
        for j in range(0, 5 - k):         # leftmost anchor at slot j
            r5x_rad = xs[0] + (4 - j) * p              # anchors are radiant
            r5x_dire = xs[0] - j * p - g               # anchors are dire
            for r5x, off in ((r5x_rad, 0), (r5x_dire, 5)):
                slots = slots_from(r5x)
                if grid_ok(slots, anchors, off):
                    cands.append(slots)

    seen: set[int] = set()
    hyps = []
    for slots in cands:
        key = int(round(slots[0][0] / (0.1 * p)))
        if key in seen:
            continue
        seen.add(key)
        score = sum(_match_slot(img, *s)[1] for s in slots)
        hyps.append((score, slots))
    return max(hyps)[1] if hyps else None


# --------------------------------------------------------------- main API


def detect_draft(img: np.ndarray, *,
                 strip_frac: float = 0.12,
                 anchor_thresh: float = 0.75,
                 score_thresh: float = 0.55,
                 scale_fracs: tuple[float, ...] = SCALE_FRACS) -> dict:
    """Detect the 10-hero draft in a screenshot (BGR array).

    Returns {radiant: [5 ids, 0 = empty/unknown], dire: [...], detections,
    slots, layout, elapsed_ms, image_size}. score_thresh gates the final
    per-slot argmax (slots below it report hero 0).
    """
    t0 = time.monotonic()
    if not _templates():
        raise RuntimeError(
            f"no hero portraits at {PORTRAITS_DIR} — run scripts/fetch_hero_portraits.py")

    H, W = img.shape[:2]
    raw = _scan_anchors(img, strip_frac, anchor_thresh, scale_fracs)
    anchors = _consensus_anchors(raw)
    names = hero_id_to_name()

    layout = slots = None
    if anchors:
        layout = _classify_layout(float(np.median([a["w"] for a in anchors])), H)
        slots = _fit_grid(anchors, layout, H, img)

    accepted: list[Detection] = []
    slot_meta = []
    if slots is not None:
        used: set[int] = set()
        resolved = [_match_slot(img, *s) for s in slots]
        # assign best-scoring slots first so a hero can't occupy two slots
        for i in sorted(range(10), key=lambda i: -resolved[i][1]):
            hid, score = resolved[i]
            if score < score_thresh or hid in used:
                resolved[i] = (0, score)
                continue
            used.add(hid)
        for i, ((hid, score), (x, y, w)) in enumerate(zip(resolved, slots)):
            side = "radiant" if i < 5 else "dire"
            slot_meta.append({"slot": i, "x": int(x), "y": int(y), "w": int(w),
                              "hero_id": hid, "score": round(score, 4), "side": side})
            if hid:
                accepted.append(Detection(hid, names.get(hid, f"hero_{hid}"),
                                          round(score, 4), int(x), int(y), int(w), side))
    else:
        # fallback: no grid (too few anchors) — report anchors split at W/2
        for a in anchors[:10]:
            side = "radiant" if a["x"] < W / 2 else "dire"
            if sum(1 for d in accepted if d.side == side) >= 5:
                continue
            accepted.append(Detection(a["hid"], names.get(a["hid"], "?"),
                                      round(a["score"], 4),
                                      int(a["x"]), int(a["y"]), int(a["w"]), side))

    def bank(side: str) -> list[int]:
        if slots is not None:
            return [s["hero_id"] for s in slot_meta if s["side"] == side]
        ids = [d.hero_id for d in sorted(
            (d for d in accepted if d.side == side), key=lambda d: d.x)]
        return (ids + [0] * 5)[:5]

    return {
        "radiant": bank("radiant"),
        "dire": bank("dire"),
        "detections": [asdict(d) for d in sorted(accepted, key=lambda d: d.x)],
        "slots": slot_meta,
        "layout": layout["name"] if (layout and slots is not None) else None,
        "elapsed_ms": int((time.monotonic() - t0) * 1000),
        "image_size": [W, H],
    }


def detect_draft_bytes(data: bytes, **kw) -> dict:
    """Decode an encoded image (png/jpg/webp bytes) and run detect_draft."""
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("could not decode image — send a PNG/JPEG screenshot")
    return detect_draft(img, **kw)


# ----------------------------------------------------- variant harvesting


def harvest_variants(img: np.ndarray, gt_radiant: list[int], gt_dire: list[int],
                     tag: str, *, min_agree: int = 5) -> list[Path]:
    """Crop alternate-avatar portraits out of a ground-truth-labeled screenshot.

    For every fitted slot whose detection disagrees with ground truth (or
    scored below threshold), save the slot crop as a template variant for the
    ground-truth hero. Requires the grid fit to be corroborated by >= min_agree
    slots agreeing with ground truth, so a bad grid can't poison templates.
    Returns the written paths. Idempotent per (hero, tag).
    """
    out = detect_draft(img)
    if out["layout"] is None:
        return []
    gt = list(gt_radiant) + list(gt_dire)
    got = out["radiant"] + out["dire"]
    if sum(1 for g, w_ in zip(got, gt) if g == w_ and w_ != 0) < min_agree:
        return []

    VARIANTS_DIR.mkdir(parents=True, exist_ok=True)
    H, W = img.shape[:2]
    written = []
    for s, want in zip(out["slots"], gt):
        if want == 0 or s["hero_id"] == want:
            continue
        x, y, w = s["x"], s["y"], s["w"]
        h = w * 144 / 256
        x0, x1 = int(max(0, x - w / 2)), int(min(W, x + w / 2))
        y0, y1 = int(max(0, y - h / 2)), int(min(H, y + h / 2))
        crop = img[y0:y1, x0:x1]
        if crop.shape[0] < 16 or crop.shape[1] < 16:
            continue
        p = VARIANTS_DIR / f"{want}-{tag}.png"
        cv2.imwrite(str(p), crop)
        written.append(p)
    return written
