"""The five model-driven dashboard endpoints, served from the live model."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Request

from ...queries import queries, hero_combos as hc
from ...queries.build_optimizer import optimize_build, format_plan
from ...queries.lookups import hero_name, item_name
from ..schemas import (DraftReq, WinDurationReq, HeroPicksReq, ItemBuildReq, HeroCombosReq)

router = APIRouter(prefix="/api", tags=["queries"])


def _model(request: Request):
    holder = request.app.state.model
    holder.maybe_reload()       # pick up a promotion without a restart
    return holder.f


@router.post("/winprob")
def winprob(req: DraftReq, request: Request):
    f = _model(request)
    wp = queries.personal_winprob(f, heroes=req.heroes, account_ids=req.account_ids)
    lm = queries.lineup_matchup(f, req.heroes[:5], req.heroes[5:],
                                radiant_accounts=(req.account_ids or [None] * 10)[:5],
                                dire_accounts=(req.account_ids or [None] * 10)[5:])
    return {"radiant_win_prob": wp,
            "predicted_duration_sec": lm["predicted_duration_sec"],
            "predicted_duration_min": round(lm["predicted_duration_sec"] / 60.0, 1)}


@router.post("/hero-picks")
def hero_picks(req: HeroPicksReq, request: Request):
    f = _model(request)
    recs = queries.hero_pick_rec(
        f, known_radiant=req.known_radiant, known_dire=req.known_dire,
        my_side=req.my_side, account_id=req.account_id, top_k=req.top_k,
        candidate_heroes=req.candidate_heroes)
    return {"picks": [asdict(r) for r in recs]}


@router.post("/win-vs-duration")
def win_vs_duration(req: WinDurationReq, request: Request):
    f = _model(request)
    pts = queries.win_vs_duration(f, heroes=req.heroes, account_ids=req.account_ids,
                                  duration_minutes=req.duration_minutes)
    return {"curve": [asdict(p) for p in pts]}


@router.post("/item-build")
def item_build(req: ItemBuildReq, request: Request):
    f = _model(request)
    plan = optimize_build(f, draft=req.heroes, my_slot=req.my_slot,
                          t_max=req.t_max, beam_width=req.beam_width,
                          account_ids=req.account_ids)
    actions = []
    for a in plan.actions:
        d = asdict(a) if hasattr(a, "__dataclass_fields__") else dict(a)
        if "item_id" in d and d["item_id"] is not None:
            d["item_name"] = item_name(d["item_id"])
        actions.append(d)
    return {
        "actions": actions,
        "final_inventory": [{"item_id": i, "item_name": item_name(i)} for i in plan.final_inventory],
        "objective": plan.objective,
        "predicted_gpm": plan.predicted_gpm,
        "gold_at_end": plan.gold_at_end,
        "pretty": format_plan(plan),
    }


@router.post("/hero-combos")
def hero_combos(req: HeroCombosReq, request: Request):
    f = _model(request)
    combos = hc.hero_combos(f, pool=req.pool, size=req.size, mode=req.mode, top_k=req.top_k)
    return {"mode": req.mode, "size": req.size, "combos": [asdict(c) for c in combos]}


@router.get("/patch-status")
def patch_status():
    """Latest patch-watch result (data/patch_status.json, refreshed by the nightly
    cycle / `patch_watch` CLI). Drives the 'new patch' banner."""
    from ...pipeline import patch_watch
    return patch_watch.load_status()


@router.get("/combos-table")
def combos_table(request: Request):
    """Precomputed all-pairs discovery table (synergy + kills/min). Draft-independent;
    served as a static table the SPA sorts/filters client-side."""
    from ...queries import artifacts
    from ...common import paths
    request.app.state.model.maybe_reload()
    return artifacts.load_combos_table(str(paths.live_model_dir()))
