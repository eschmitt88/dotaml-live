"""Pydantic request models for the dashboard JSON API. Hero/item identifiers are
integer IDs (the SPA resolves names via /meta)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DraftReq(BaseModel):
    heroes: list[int] = Field(..., min_length=10, max_length=10,
                              description="10 hero IDs: radiant[0:5] then dire[5:10]")
    account_ids: list[int | None] | None = None


class WinDurationReq(DraftReq):
    duration_minutes: list[float] | None = None


class HeroPicksReq(BaseModel):
    known_radiant: list[int] = Field(default_factory=list)
    known_dire: list[int] = Field(default_factory=list)
    my_side: str = "radiant"
    account_id: int | None = None
    top_k: int = 10
    candidate_heroes: list[int] | None = None


class ItemBuildReq(DraftReq):
    my_slot: int = Field(..., ge=0, le=9)
    t_max: int = 45
    beam_width: int = 32


class ShotLabelReq(BaseModel):
    """Ground-truth draft for a saved screenshot. 0 = slot empty in the shot."""
    radiant: list[int] = Field(..., min_length=5, max_length=5)
    dire: list[int] = Field(..., min_length=5, max_length=5)
    labeled_by: str = "human"      # "human" | "claude"


class HeroCombosReq(BaseModel):
    pool: list[int] | None = None
    size: int = Field(2, ge=2, le=3)
    mode: str = "synergy"          # "synergy" | "kills_per_min"
    top_k: int = 15
