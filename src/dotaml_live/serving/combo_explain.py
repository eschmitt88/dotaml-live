"""On-demand Claude explanation for a hero combo (Discover tab ✨ button).

One-shot, never precomputed or cached. Two credential paths, tried in order:

  1. Anthropic SDK — when ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN is set in the
     dashboard's environment.
  2. The `claude` CLI headless (-p) — the same pattern feedback_runner uses for
     triage/implement. This is what actually works on this box: the dashboard
     service has no API key env var, but the CLI carries its own login.
"""

from __future__ import annotations

import bisect
import os
import shutil
import subprocess
from pathlib import Path

from ..common import paths
from ..queries.artifacts import load_combos_table
from ..queries.lookups import (hero_id, hero_id_to_abilities, hero_id_to_attr,
                               hero_id_to_roles)

SDK_MODEL = "claude-haiku-4-5-20251001"   # cheap + fast; ~300-token answers
CLI_MODEL = "haiku"
CLI_TIMEOUT_S = 90

PROMPT = """\
You are a Dota 2 analyst. Explain briefly why these heroes synergize when queued
together (Turbo mode, casual stack with friends).

Combo: {heroes}
Model-estimated synergy: {synergy:+.2%} win-probability lift vs the heroes' individual baselines
{scale}{stats}
{abilities}
Ground the explanation in their abilities, roles, and timing windows — e.g. setup
into follow-up, lockdown into burst, save/sustain enabling a greedy core, or
shared power spikes. If the synergy number is negative or the win rate is low,
say honestly why the pairing may underperform despite looking fun.

Answer in 3-5 short sentences of plain prose. No headings, no bullet lists,
no preamble — start directly with the explanation."""


# The full ability catalog for a hero pair runs ~1-9k chars (~2k tokens worst
# case), so no formatting-level clipping is needed. This cap exists only to
# guard against a pathologically bloated/corrupt hero_abilities.json dragging
# a massive payload into the prompt.
ABILITIES_BLOCK_MAX_CHARS = 100_000


def _hero_blurb(name: str) -> str:
    """'Anti-Mage (agi — Carry, Escape, Nuker)'; falls back to the bare name."""
    hid = hero_id(name)
    if hid is None:
        return name
    attr = hero_id_to_attr().get(hid, "?")
    roles = ", ".join(hero_id_to_roles().get(hid, [])) or "?"
    return f"{name} ({attr} — {roles})"


def _squash(text: str) -> str:
    return " ".join(text.split())


_SIZE_KEY = {2: "pairs", 3: "trios"}
_SIZE_NOUN = {2: "pair", 3: "trio"}


def _percentile(q: list[float], x: float) -> float:
    """Percentile of x against a sorted p0..p100 quantile grid (interpolated)."""
    i = bisect.bisect_right(q, x)
    if i == 0:
        return 0.0
    if i >= len(q):
        return 100.0
    lo, hi = q[i - 1], q[i]
    frac = 0.0 if hi <= lo else (x - lo) / (hi - lo)
    return (i - 1 + frac) * 100.0 / (len(q) - 1)


def _scale_line(heroes: list[str], synergy: float) -> str:
    """One 'For scale:' line anchoring the synergy number in the all-combos
    distribution — without it the model misreads e.g. +2.9% (top-1% of all
    pairs) as 'modest'. Anchors come from the precomputed table's
    synergy_scale quantile grids; any miss yields an empty string and the
    prompt degrades to its prior form."""
    try:
        table = load_combos_table(str(paths.live_model_dir()))
        scale = (table.get("synergy_scale") or {})[_SIZE_KEY[len(heroes)]]
        q, n = scale["q"], scale["n"]
        pct = _percentile(q, synergy)
        noun = _SIZE_NOUN[len(heroes)]
        return (f"For scale: across all {n:,} hero {noun}s, synergy ranges "
                f"{q[0]:+.2%} to {q[-1]:+.2%} (median {q[50]:+.2%}); top-decile "
                f"starts at {q[90]:+.2%} and top-1% at {q[99]:+.2%}. This {noun} "
                f"is at the {pct:.1f}th percentile.\n")
    except Exception:
        return ""


def _abilities_block(heroes: list[str]) -> str:
    """'Abilities:' section — each hero's complete kit, full descriptions.

    Heroes missing from hero_abilities.json are simply skipped (their blurb
    still carries attr + roles); no data at all yields an empty string, so the
    prompt degrades to exactly its pre-abilities form.
    """
    lines = []
    total = 0
    for name in heroes:
        try:
            hid = hero_id(name)
            abilities = hero_id_to_abilities(hid) if hid is not None else []
        except Exception:
            abilities = []
        if not abilities:
            continue
        hero_lines = [f"- {name}:"]
        hero_lines += [f"  - {_squash(a)}" for a in abilities]
        hero_chars = sum(len(l) + 1 for l in hero_lines)
        if total + hero_chars > ABILITIES_BLOCK_MAX_CHARS:
            break
        lines += hero_lines
        total += hero_chars
    return "Abilities:\n" + "\n".join(lines) if lines else ""


def build_prompt(heroes: list[str], synergy: float,
                 avg_winprob: float | None = None, kpm: float | None = None) -> str:
    stats = []
    if avg_winprob is not None:
        stats.append(f"Combined win rate in the data: {avg_winprob:.1%}")
    if kpm is not None:
        stats.append(f"Average kills/min when paired: {kpm:.2f}")
    return PROMPT.format(heroes=" + ".join(_hero_blurb(h) for h in heroes),
                         synergy=synergy,
                         scale=_scale_line(heroes, synergy),
                         stats="\n".join(stats),
                         abilities=_abilities_block(heroes))


def _have_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _via_sdk(prompt: str) -> str:
    import anthropic
    client = anthropic.Anthropic()
    try:
        msg = client.messages.create(
            model=SDK_MODEL, max_tokens=300,
            messages=[{"role": "user", "content": prompt}])
    except anthropic.APIError as e:
        raise RuntimeError(f"Anthropic API error: {getattr(e, 'message', e)}") from e
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    if not text:
        raise RuntimeError("Anthropic API returned an empty response")
    return text


def _claude_bin() -> str:
    return shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")

def _cli_env() -> dict:
    env = dict(os.environ)
    env["PATH"] = f"{Path.home()}/.local/bin:/usr/local/bin:/usr/bin:/bin"
    return env


def _via_cli(prompt: str) -> str:
    cmd = [_claude_bin(), "-p", prompt, "--model", CLI_MODEL]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=CLI_TIMEOUT_S, env=_cli_env())
    except FileNotFoundError:
        raise RuntimeError("no ANTHROPIC_API_KEY set and the claude CLI is not installed")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"claude CLI timed out after {CLI_TIMEOUT_S}s")
    if out.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {out.stderr.strip()[:300] or 'unknown error'}")
    text = out.stdout.strip()
    if not text:
        raise RuntimeError("claude CLI returned an empty response")
    return text


def explain(heroes: list[str], synergy: float,
            avg_winprob: float | None = None, kpm: float | None = None) -> str:
    """Generate the explanation, raising RuntimeError with a readable reason."""
    prompt = build_prompt(heroes, synergy, avg_winprob, kpm)
    if _have_api_key():
        return _via_sdk(prompt)
    return _via_cli(prompt)
