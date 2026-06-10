#!/usr/bin/env python3
"""Replay the screenshot draft detector over all ground-truth-labeled shots.

Reads data/screenshots/ sidecars (written by the dashboard, labeled via the
SPA or by Claude), re-runs detect_draft_bytes on each stored image, and
reports per-slot accuracy — the calibration signal for the detector's
score threshold / scale sweep.

Usage: .venv/bin/python scripts/eval_screenshot_detector.py [--thresh 0.62]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotaml_live.queries.lookups import hero_name           # noqa: E402
from dotaml_live.serving import screenshot, screenshot_store  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--thresh", type=float, default=0.55, help="per-slot score threshold")
    ap.add_argument("--harvest", action="store_true",
                    help="crop mismatched slots from labeled shots into "
                         "data/hero_portraits/variants/ (learns alternate avatars)")
    args = ap.parse_args()

    labeled = screenshot_store.list_shots("labeled")
    if not labeled:
        n_queue = len(screenshot_store.list_shots("unlabeled"))
        print(f"no labeled shots yet ({n_queue} unlabeled in the queue)")
        return 0

    if args.harvest:
        import cv2
        for meta in labeled:
            img = cv2.imread(str(screenshot_store.image_path(meta["id"])))
            paths_written = screenshot.harvest_variants(
                img, meta["ground_truth"]["radiant"], meta["ground_truth"]["dire"],
                tag=meta["id"])
            for p in paths_written:
                print(f"harvested {p.name}")
        print()

    slot_ok = slot_total = exact = 0
    for meta in labeled:
        img = screenshot_store.image_path(meta["id"]).read_bytes()
        out = screenshot.detect_draft_bytes(img, score_thresh=args.thresh)
        got = out["radiant"] + out["dire"]
        want = meta["ground_truth"]["radiant"] + meta["ground_truth"]["dire"]
        ok = sum(g == w for g, w in zip(got, want))
        slot_ok += ok
        slot_total += 10
        exact += got == want
        flag = "OK " if got == want else "MISS"
        print(f"{flag} {meta['id']}  {ok}/10 slots  ({out['elapsed_ms']} ms)")
        if got != want:
            for i, (g, w) in enumerate(zip(got, want)):
                if g != w:
                    side = "R" if i < 5 else "D"
                    print(f"     {side}{i % 5 + 1}: detected "
                          f"{hero_name(g) if g else '—'} ≠ truth {hero_name(w) if w else '—'}")

    print(f"\n{len(labeled)} shots @ thresh {args.thresh}: "
          f"{exact}/{len(labeled)} exact drafts, "
          f"{slot_ok}/{slot_total} slots ({100 * slot_ok / slot_total:.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
