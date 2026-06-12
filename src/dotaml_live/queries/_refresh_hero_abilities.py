#!/usr/bin/env python3
"""Regenerate hero_abilities.json from the OpenDota constants endpoints.

Joins constants/hero_abilities (hero -> ability slugs) with constants/abilities
(slug -> display name + description) into a single file keyed by the hero's
npc name (the `name` field in heroes.json):

    {"npc_dota_hero_antimage": [{"dname": "Mana Break", "desc": "Burns..."}, ...]}

Talents, facets, and hidden/placeholder abilities are dropped — the file feeds
the combo-explanation prompt (lookups.hero_id_to_abilities), which only wants
the handful of real abilities that define how a hero plays.

Run after a patch (or from a nightly cron) to keep the file current:

    python -m dotaml_live.queries._refresh_hero_abilities
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ABILITIES_URL = "https://api.opendota.com/api/constants/abilities"
HERO_ABILITIES_URL = "https://api.opendota.com/api/constants/hero_abilities"
OUT_PATH = Path(__file__).resolve().parent / "hero_abilities.json"
DESC_MAX_CHARS = 400  # bound the file; prompts clip much shorter anyway


def _fetch_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "dotaml-live"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def build(hero_abilities: dict, abilities: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for npc_name, h in hero_abilities.items():
        entries = []
        raw = h.get("abilities", [])
        # Monkey King nests his transform pair as a sub-list — flatten it
        slugs = [s for item in raw for s in (item if isinstance(item, list) else [item])]
        for slug in slugs:
            if "hidden" in slug or slug.startswith("special_bonus"):
                continue
            ab = abilities.get(slug, {})
            dname, desc = ab.get("dname"), ab.get("desc")
            if not dname or not desc:
                continue  # placeholder / unreleased ability
            desc = " ".join(desc.split())
            if len(desc) > DESC_MAX_CHARS:
                desc = desc[: DESC_MAX_CHARS - 1].rstrip() + "…"
            entries.append({"dname": dname, "desc": desc})
        if entries:
            out[npc_name] = entries
    return out


def main() -> int:
    data = build(_fetch_json(HERO_ABILITIES_URL), _fetch_json(ABILITIES_URL))
    if len(data) < 100:  # Dota has 120+ heroes; a tiny result means a bad fetch
        print(f"refusing to overwrite: only {len(data)} heroes resolved", file=sys.stderr)
        return 1
    with open(OUT_PATH, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    n_abilities = sum(len(v) for v in data.values())
    print(f"wrote {OUT_PATH.name}: {len(data)} heroes, {n_abilities} abilities")
    return 0


if __name__ == "__main__":
    sys.exit(main())
