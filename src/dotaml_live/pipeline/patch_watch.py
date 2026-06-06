"""Detect new Dota patches so their date enters the edge list (config/patches.yaml).

We deliberately do NOT trigger an off-cadence retrain on a patch — same-day post-patch
data is too thin to help; the nightly/weekly cadence adapts as the data accumulates.
The only thing a patch needs from us promptly is its DATE in the patch-edge list (so
patch_id labels + the recency current-patch boundary track the live meta).

Source: OpenDota's patch constant (major patches, clean dates) — already a dependency.
A patch is "new" if its date is after our latest known edge. `check()` writes
data/patch_status.json (read by the dashboard banner + the nightly cycle).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dotaml_live.common import patches, paths  # noqa: E402

OPENDOTA_PATCH_URL = "https://api.opendota.com/api/constants/patch"
STATUS_FILE = paths.DATA_DIR / "patch_status.json"


def fetch_opendota_patches(timeout: int = 20) -> list[dict]:
    req = urllib.request.Request(OPENDOTA_PATCH_URL,
                                 headers={"User-Agent": "dotaml-live/0.1 (+patch-watch)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    out = []
    for p in data:
        name, date = p.get("name"), p.get("date")
        if name and date:
            out.append({"name": str(name), "date": str(date)[:10]})  # ISO -> YYYY-MM-DD
    return out


def new_patches(official: list[dict] | None = None) -> list[dict]:
    """Official patches dated AFTER our latest known edge (= not yet in the list)."""
    official = official if official is not None else fetch_opendota_patches()
    latest = patches.current_patch_start()
    return [p for p in official if p["date"] > latest]


def check(write: bool = True) -> dict:
    try:
        official = fetch_opendota_patches()
    except Exception as e:  # noqa: BLE001
        return {"checked": False, "error": str(e)}
    status = {"checked": True, "latest_known": patches.latest_patch(),
              "new_patches": new_patches(official), "n_known_edges": len(patches.edges())}
    if write:
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATUS_FILE.write_text(json.dumps(status, indent=2))
    return status


def add_detected() -> list[dict]:
    """Append every detected new patch to config/patches.yaml. Returns what was added."""
    added = []
    for p in sorted(new_patches(), key=lambda p: p["date"]):
        nid = patches.add_edge(p["date"], p["name"])
        added.append({**p, "patch_id": nid})
    return added


def load_status() -> dict:
    return json.loads(STATUS_FILE.read_text()) if STATUS_FILE.exists() else {"checked": False}


def main() -> None:
    ap = argparse.ArgumentParser(description="Check for new Dota patches vs config/patches.yaml")
    ap.add_argument("--add", action="store_true",
                    help="append detected new patches to config/patches.yaml")
    args = ap.parse_args()
    st = check()
    if not st["checked"]:
        print(f"[patch_watch] check failed: {st.get('error')}")
        return
    lk = st["latest_known"]
    if st["new_patches"]:
        print("[patch_watch] NEW PATCH(ES) detected — not in the edge list:")
        for p in st["new_patches"]:
            print(f"    {p['name']}  ({p['date']})")
        if args.add:
            for a in add_detected():
                print(f"    added {a['name']} {a['date']} -> patch_id {a['patch_id']}")
            print("    config/patches.yaml updated; next retrain labels these matches.")
        else:
            print("    run with --add to append them (or edit config/patches.yaml).")
    else:
        print(f"[patch_watch] up to date — latest patch {lk['name']} ({lk['date']})")


if __name__ == "__main__":
    main()
