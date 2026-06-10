#!/usr/bin/env python3
"""Download hero portrait images into data/hero_portraits/.

Two sources:

* Stock portraits — Steam CDN, 256x144:
  https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/<short>.png
  These are the renders Dota uses for the in-game topbar and pick-screen slots.

* Cosmetic variants (--variants) — personas, arcanas and other portrait-swapping
  cosmetics render a DIFFERENT topbar image that stock art can never match.
  The game ships them in the VPK as
  panorama/images/heroes/npc_dota_hero_<short>_<suffix>_png.vtex_c; the
  dotabase project (github.com/mdiller/dotabase) hosts the extracted PNGs at
  https://dotabase.dillerm.io/dota-vpk/... (128x72 — ample for the 56x32
  matcher templates). There is no directory listing, so we probe a known
  suffix family per hero and keep responses with PNG magic bytes (misses
  return the SPA's HTML with HTTP 200). Variants land in
  data/hero_portraits/variants/<hid>-vpk_<suffix>.png, the same pool the
  screenshot matcher's harvested templates live in.

Source of truth for the hero list: src/dotaml_live/queries/heroes.json.
Idempotent: skips files that already exist. Re-run after a new-hero patch or
when Valve ships a new persona/arcana (refresh heroes.json first).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HEROES_JSON = REPO_ROOT / "src" / "dotaml_live" / "queries" / "heroes.json"
OUT_DIR = REPO_ROOT / "data" / "hero_portraits"
VARIANTS_DIR = OUT_DIR / "variants"
CDN = "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/{short}.png"
VPK = "https://dotabase.dillerm.io/dota-vpk/panorama/images/heroes/npc_dota_hero_{short}_{suffix}_png.png"

# Cosmetic portrait-swap suffixes observed in the VPK. Probed per hero; only
# real PNGs are kept, so an over-broad list costs nothing but requests.
VARIANT_SUFFIXES = ("persona", "persona1", "persona2", "persona3",
                    "arcana", "arcana1", "arcana2",
                    "alt", "alt1", "alt2", "prestige",
                    "ti9", "ti10", "ti11")


def _get(url: str, timeout: int = 30) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read()
    except Exception:  # noqa: BLE001
        return None


def fetch_stock(heroes: dict) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ok, skipped, failed = 0, 0, []
    for hid_str, h in sorted(heroes.items(), key=lambda kv: int(kv[0])):
        short = h["name"].replace("npc_dota_hero_", "")
        dest = OUT_DIR / f"{int(hid_str)}.png"
        if dest.exists() and dest.stat().st_size > 0:
            skipped += 1
            continue
        data = _get(CDN.format(short=short))
        if data and data[:8] == b"\x89PNG\r\n\x1a\n":
            dest.write_bytes(data)
            ok += 1
        else:
            failed.append((int(hid_str), short))
    print(f"stock: downloaded {ok}, skipped {skipped} existing, failed {len(failed)}")
    for hid, short in failed:
        print(f"  FAIL hero {hid} ({short})")
    return len(failed)


def fetch_variants(heroes: dict) -> int:
    VARIANTS_DIR.mkdir(parents=True, exist_ok=True)
    jobs = []
    for hid_str, h in sorted(heroes.items(), key=lambda kv: int(kv[0])):
        short = h["name"].replace("npc_dota_hero_", "")
        for suffix in VARIANT_SUFFIXES:
            dest = VARIANTS_DIR / f"{int(hid_str)}-vpk_{suffix}.png"
            if not dest.exists():
                jobs.append((int(hid_str), short, suffix, dest))

    def probe(job):
        hid, short, suffix, dest = job
        data = _get(VPK.format(short=short, suffix=suffix))
        if not data or data[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        return (hid, short, suffix, dest, data)

    found, dupes = 0, 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = [r for r in pool.map(probe, jobs) if r]

    # drop byte-identical duplicates (e.g. _persona vs _persona1 aliases) and
    # variants identical to already-saved ones for the same hero
    seen: dict[int, set[str]] = {}
    for hid in {r[0] for r in results}:
        seen[hid] = {hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in VARIANTS_DIR.glob(f"{hid}-*.png")}
    for hid, short, suffix, dest, data in sorted(results, key=lambda r: (r[0], r[2])):
        digest = hashlib.sha256(data).hexdigest()
        if digest in seen.setdefault(hid, set()):
            dupes += 1
            continue
        seen[hid].add(digest)
        dest.write_bytes(data)
        print(f"  variant hero {hid:4d} {short}_{suffix}")
        found += 1
    print(f"variants: downloaded {found} new ({dupes} duplicate aliases skipped, "
          f"{len(jobs)} probed)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", action="store_true",
                    help="also probe the VPK mirror for persona/arcana portrait variants")
    args = ap.parse_args()

    with open(HEROES_JSON) as f:
        heroes = json.load(f)
    rc = fetch_stock(heroes)
    if args.variants:
        rc |= fetch_variants(heroes)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
