"""Unit tests for the on-demand combo explanation (no network, no claude CLI)."""

from __future__ import annotations

import pytest

from dotaml_live.serving import combo_explain


def test_build_prompt_includes_heroes_and_stats():
    p = combo_explain.build_prompt(["Anti-Mage", "Crystal Maiden"],
                                   synergy=0.0312, avg_winprob=0.481, kpm=1.25)
    assert "Anti-Mage" in p and "Crystal Maiden" in p
    assert "+3.12%" in p
    assert "48.1%" in p
    assert "1.25" in p


def test_build_prompt_handles_unknown_hero_and_missing_stats():
    p = combo_explain.build_prompt(["Notahero", "Pudge"], synergy=-0.01)
    assert "Notahero" in p and "Pudge" in p
    assert "Combined win rate" not in p      # omitted when avg_winprob is None
    assert "-1.00%" in p


def test_build_prompt_includes_full_hero_kits():
    p = combo_explain.build_prompt(["Anti-Mage", "Crystal Maiden"], synergy=0.02)
    assert "Abilities:" in p
    # complete kit, ultimates included
    assert "Mana Break" in p and "Blink" in p and "Mana Void" in p
    assert "Crystal Nova" in p and "Frostbite" in p and "Freezing Field" in p
    assert "…" not in p                     # descriptions are not truncated


def _grid(lo: float, hi: float) -> list[float]:
    return [lo + (hi - lo) * i / 100 for i in range(101)]


def test_build_prompt_scale_line_for_pairs(monkeypatch):
    fake = {"synergy_scale": {"pairs": {"n": 7875, "q": _grid(-0.02, 0.04)},
                              "trios": {"n": 325000, "q": _grid(-0.03, 0.06)}}}
    monkeypatch.setattr(combo_explain, "load_combos_table", lambda d: fake)
    p = combo_explain.build_prompt(["Anti-Mage", "Crystal Maiden"], synergy=0.028)
    assert "For scale: across all 7,875 hero pairs" in p
    # linear grid -0.02..0.04 → 0.028 sits at (0.028+0.02)/0.06 = 80.0th pct
    assert "This pair is at the 80.0th percentile." in p
    assert "median +1.00%" in p


def test_build_prompt_scale_line_for_trios(monkeypatch):
    fake = {"synergy_scale": {"pairs": {"n": 7875, "q": _grid(-0.02, 0.04)},
                              "trios": {"n": 325000, "q": _grid(-0.03, 0.06)}}}
    monkeypatch.setattr(combo_explain, "load_combos_table", lambda d: fake)
    p = combo_explain.build_prompt(["Anti-Mage", "Crystal Maiden", "Pudge"],
                                   synergy=0.06)
    assert "across all 325,000 hero trios" in p
    assert "This trio is at the 100.0th percentile." in p


def test_build_prompt_omits_scale_line_without_anchors(monkeypatch):
    monkeypatch.setattr(combo_explain, "load_combos_table",
                        lambda d: {"computed": True, "combos": []})
    p = combo_explain.build_prompt(["Anti-Mage", "Crystal Maiden"], synergy=0.02)
    assert "For scale:" not in p
    assert "Anti-Mage" in p                 # prompt still builds


def test_build_prompt_falls_back_when_abilities_lookup_fails(monkeypatch):
    def boom(hid):
        raise OSError("hero_abilities.json unreadable")
    monkeypatch.setattr(combo_explain, "hero_id_to_abilities", boom)
    p = combo_explain.build_prompt(["Anti-Mage", "Pudge"], synergy=0.02)
    assert "Abilities:" not in p            # block omitted, prompt still builds
    assert "Anti-Mage" in p and "Pudge" in p


def test_explain_raises_readable_error_without_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(combo_explain, "_claude_bin",
                        lambda: str(tmp_path / "nonexistent-claude"))
    with pytest.raises(RuntimeError, match="claude CLI"):
        combo_explain.explain(["Anti-Mage", "Crystal Maiden"], synergy=0.02)


def test_explain_uses_sdk_when_key_present(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(combo_explain, "_via_sdk", lambda prompt: "because reasons")
    assert combo_explain.explain(["Pudge", "Dazzle"], synergy=0.01) == "because reasons"
