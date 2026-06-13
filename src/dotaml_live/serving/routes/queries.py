"""The five model-driven dashboard endpoints, served from the live model."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request

from ...queries import queries, hero_combos as hc
from ...queries.build_optimizer import optimize_build, format_plan
from ...queries.lookups import hero_name, item_name
from ..schemas import (DraftReq, WinDurationReq, HeroPicksReq, ItemBuildReq, HeroCombosReq,
                       ComboExplainReq, ShotLabelReq)

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


@router.get("/hero-stats/{hero_id}")
def hero_stats(hero_id: int, request: Request):
    """Solo baseline win rate for one hero: P(win) with only that hero filled in
    and the other nine slots masked, averaged over the hero placed on Radiant vs
    Dire. Mirrors the side-averaged win rate used in the Combo Discovery table."""
    f = _model(request)
    if hero_id < 1 or hero_id >= f.n_heroes:
        raise HTTPException(404, f"hero {hero_id} not in the active model")
    subset = [(hero_id,)]
    rad = float(hc._radiant_winprob_batch(f, subset)[0])
    dire = float(1.0 - hc._radiant_winprob_batch(f, subset, side="dire")[0])
    return {"hero_id": hero_id, "hero_name": hero_name(hero_id),
            "win_rate_radiant": rad, "win_rate_dire": dire,
            "win_rate_avg": (rad + dire) / 2.0}


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


@router.post("/combos/explain")
def combos_explain(req: ComboExplainReq):
    """On-demand Claude explanation of why a combo synergizes (one-shot, no cache)."""
    from .. import combo_explain
    try:
        text = combo_explain.explain(heroes=req.heroes, synergy=req.synergy,
                                     avg_winprob=req.avg_winprob, kpm=req.kpm)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    return {"explanation": text}


@router.post("/draft-from-screenshot")
async def draft_from_screenshot(request: Request):
    """Detect the 10-hero draft from a Dota screenshot (raw PNG/JPEG body).

    Pure OpenCV template matching against the topbar / pick-screen slots —
    no model involved. The SPA pastes or drops a screenshot here and fills
    the draft board from the response. Every screenshot is persisted to the
    labeling queue (data/screenshots/) for later ground-truth calibration.
    """
    from .. import screenshot, screenshot_store
    data = await request.body()
    if not data:
        raise HTTPException(400, "empty body — send the screenshot bytes")
    try:
        out = screenshot.detect_draft_bytes(data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    meta = screenshot_store.save_shot(data, out)
    out["shot_id"] = meta["id"]
    out["already_labeled"] = meta["ground_truth"] is not None
    return out


@router.get("/screenshots")
def screenshots_list(status: str = "all"):
    """The screenshot labeling queue. status: all | unlabeled | labeled."""
    from .. import screenshot_store
    return {"shots": screenshot_store.list_shots(status)}


@router.get("/screenshots/{sid}/image")
def screenshot_image(sid: str):
    from fastapi.responses import FileResponse
    from .. import screenshot_store
    try:
        p = screenshot_store.image_path(sid)
    except (KeyError, ValueError):
        raise HTTPException(404, f"unknown shot {sid}")
    media = "image/png" if p.suffix == ".png" else "image/jpeg"
    return FileResponse(p, media_type=media)


@router.post("/screenshots/{sid}/label")
def screenshot_label(sid: str, req: ShotLabelReq):
    from .. import screenshot_store
    try:
        meta = screenshot_store.set_label(sid, req.radiant, req.dire, req.labeled_by)
    except KeyError:
        raise HTTPException(404, f"unknown shot {sid}")
    except ValueError as e:
        raise HTTPException(400, str(e))

    # learn alternate-avatar portraits from the fresh label, off the request path
    import logging
    import threading

    def _harvest():
        import cv2
        from .. import screenshot
        try:
            img = cv2.imread(str(screenshot_store.image_path(sid)))
            if img is not None:
                for p in screenshot.harvest_variants(img, req.radiant, req.dire, tag=sid):
                    logging.getLogger(__name__).info("harvested template variant %s", p.name)
        except Exception:
            logging.getLogger(__name__).exception("variant harvest failed for %s", sid)

    threading.Thread(target=_harvest, daemon=True).start()
    return meta


@router.delete("/screenshots/{sid}")
def screenshot_delete(sid: str):
    from .. import screenshot_store
    try:
        screenshot_store.delete_shot(sid)
    except (KeyError, ValueError):
        raise HTTPException(404, f"unknown shot {sid}")
    return {"deleted": sid}


@router.get("/settings")
def settings_get():
    """Dashboard preferences shared across browsers, ports, and dev previews
    (data/settings.json) — e.g. the saved players + account IDs."""
    from .. import settings_store
    return settings_store.load()


@router.post("/settings")
def settings_set(partial: dict):
    """Shallow-merge the posted keys into the stored settings (None deletes)."""
    from .. import settings_store
    return settings_store.update(partial)


@router.get("/patch-status")
def patch_status():
    """Latest patch-watch result (data/patch_status.json, refreshed by the nightly
    cycle / `patch_watch` CLI). Drives the 'new patch' banner."""
    from ...pipeline import patch_watch
    return patch_watch.load_status()


@router.get("/combos-table")
def combos_table(request: Request):
    """Precomputed all-pairs discovery table (synergy + kills/min + win rate).
    Draft-independent; served as a static table the SPA sorts/filters client-side."""
    from ...queries import artifacts
    from ...common import paths
    request.app.state.model.maybe_reload()
    table = artifacts.load_combos_table(str(paths.live_model_dir()))
    rows = table.get("combos") or []
    if table.get("computed") and rows and "avg_winprob" not in rows[0]:
        # table predates avg_winprob — recalculate win rates with the live model
        from ...queries import combos_precompute
        combos_precompute.backfill_avg_winprob(paths.live_model_dir(),
                                               f=request.app.state.model.f)
        table = artifacts.load_combos_table(str(paths.live_model_dir()))
    return table
