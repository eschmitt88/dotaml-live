"""Unit tests for the continuous-training control plane (pure logic — no GPU)."""

from __future__ import annotations

from dotaml_live.training.promote import decide, GateInput
from dotaml_live.training.retrain import recency_weights

HALT = {"pure_pregame": {"value": 0.55, "direction": "below"},
        "gpm_probe": {"value": 0.30, "direction": "above"}}
CFG = {"beat_incumbent_margin": 0.0005, "anchor_max_regression": 0.005,
       "require_probes_pass": True}
INC = GateInput(fresh_auc=0.648, probes={"pure_pregame": 0.648, "gpm_probe": 0.08}, anchor_auc=0.649)


def _cand(auc, pp=None, anchor=0.649):
    return GateInput(fresh_auc=auc, probes={"pure_pregame": pp if pp is not None else auc,
                                            "gpm_probe": 0.08}, anchor_auc=anchor)


def test_gate_promotes_when_beats_and_passes():
    assert decide(_cand(0.652), INC, CFG, HALT).promote is True


def test_gate_rejects_when_not_beating():
    assert decide(_cand(0.6481), INC, CFG, HALT).promote is False


def test_gate_rejects_on_anchor_regression():
    assert decide(_cand(0.652, anchor=0.640), INC, CFG, HALT).promote is False


def test_gate_rejects_on_probe_failure():
    assert decide(_cand(0.652, pp=0.54), INC, CFG, HALT).promote is False


def test_gate_bootstrap_promotes_without_incumbent():
    assert decide(_cand(0.60), None, CFG, HALT).promote is True


def test_recency_weights_recent_and_patch():
    w = recency_weights(["2026-06-04", "2026-04-05", "2025-10-01"],
                        half_life_days=60, current_patch_upweight=1.3,
                        patch_start="2025-12-16", now="2026-06-04")
    assert abs(w["2026-06-04"] - 1.3) < 1e-9          # age 0 * psi
    assert w["2026-06-04"] > w["2026-04-05"] > w["2025-10-01"]   # monotone decay
    assert w["2025-10-01"] < 0.1                       # old + pre-patch (no upweight)
