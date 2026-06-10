"""FastAPI dashboard service — serves the four model-driven views from the live
v7 model. LAN-only; the SPA in frontend/ consumes this JSON API.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from ..common import config, paths
from ..queries.lookups import (hero_id_to_name, hero_id_to_attr, hero_id_to_roles,
                               item_id_to_info)
from .model_loader import ModelHolder
from .routes import feedback as feedback_routes
from .routes import queries as queries_routes


@lru_cache(maxsize=1)
def _meta_payload() -> dict:
    heroes = [{"id": hid, "name": name,
               "attr": hero_id_to_attr().get(hid, "?"),
               "roles": hero_id_to_roles().get(hid, [])}
              for hid, name in sorted(hero_id_to_name().items()) if hid >= 1]
    items = [{"id": iid, "name": info["dname"], "cost": info["cost"]}
             for iid, info in sorted(item_id_to_info().items())
             if info.get("cost", 0) > 0]
    return {"heroes": heroes, "items": items}


def create_app() -> FastAPI:
    cfg = config.serving_config()
    app = FastAPI(title="dotaml-live", version="0.1.0")
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    app.state.model = ModelHolder(device_cfg=cfg.get("device", "auto"))
    app.include_router(queries_routes.router)
    app.include_router(feedback_routes.router)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/model")
    def model():
        return app.state.model.info()

    @app.get("/meta")
    def meta():
        """Hero + item catalogs for the SPA pickers."""
        return _meta_payload()

    # Serve the built SPA if present (frontend/dist). In dev, run vite separately.
    dist = paths.REPO_ROOT / "frontend" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="spa")

    return app


def main() -> None:
    import uvicorn
    cfg = config.serving_config()
    uvicorn.run(create_app(), host=cfg.get("host", "0.0.0.0"), port=int(cfg.get("port", 8090)))


if __name__ == "__main__":
    main()
