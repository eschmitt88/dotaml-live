"""On-demand Claude explanation for a hero combo (Discover tab ✨ button).

One-shot, never precomputed or cached. Two credential paths, tried in order:

  1. Anthropic SDK — when ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN is set in the
     dashboard's environment.
  2. The `claude` CLI headless (-p) — the same pattern feedback_runner uses for
     triage/implement. This is what actually works on this box: the dashboard
     service has no API key env var, but the CLI carries its own login.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ..queries.lookups import hero_id, hero_id_to_attr, hero_id_to_roles

SDK_MODEL = "claude-haiku-4-5-20251001"   # cheap + fast; ~300-token answers
CLI_MODEL = "haiku"
CLI_TIMEOUT_S = 90

PROMPT = """\
You are a Dota 2 analyst. Explain briefly why these heroes synergize when queued
together (Turbo mode, casual stack with friends).

Combo: {heroes}
Model-estimated synergy: {synergy:+.2%} win-probability lift vs the heroes' individual baselines
{stats}
Ground the explanation in their abilities, roles, and timing windows — e.g. setup
into follow-up, lockdown into burst, save/sustain enabling a greedy core, or
shared power spikes. If the synergy number is negative or the win rate is low,
say honestly why the pairing may underperform despite looking fun.

Answer in 3-5 short sentences of plain prose. No headings, no bullet lists,
no preamble — start directly with the explanation."""


def _hero_blurb(name: str) -> str:
    """'Anti-Mage (agi — Carry, Escape, Nuker)'; falls back to the bare name."""
    hid = hero_id(name)
    if hid is None:
        return name
    attr = hero_id_to_attr().get(hid, "?")
    roles = ", ".join(hero_id_to_roles().get(hid, [])) or "?"
    return f"{name} ({attr} — {roles})"


def build_prompt(heroes: list[str], synergy: float,
                 avg_winprob: float | None = None, kpm: float | None = None) -> str:
    stats = []
    if avg_winprob is not None:
        stats.append(f"Combined win rate in the data: {avg_winprob:.1%}")
    if kpm is not None:
        stats.append(f"Average kills/min when paired: {kpm:.2f}")
    return PROMPT.format(heroes=" + ".join(_hero_blurb(h) for h in heroes),
                         synergy=synergy,
                         stats="\n".join(stats))


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
