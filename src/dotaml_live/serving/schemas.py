"""Pydantic request models for the dashboard JSON API. Hero/item identifiers are
integer IDs (the SPA resolves names via /meta)."""

from __future__ import annotations

from typing import Literal

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


class FeedbackTextReq(BaseModel):
    """Typed feedback for the improvement queue."""
    text: str = Field(..., max_length=8000)


class FeedbackRejectReq(BaseModel):
    reason: str | None = None


class FeedbackTicketPatchReq(BaseModel):
    """Partial ticket edit — only the non-null fields are merged in."""
    title: str | None = Field(None, max_length=80)
    summary: str | None = None
    details: str | None = None
    area: Literal["frontend", "backend", "model", "pipeline", "other"] | None = None
    acceptance: list[str] | None = None


class FeedbackApproveReq(BaseModel):
    """Per-ticket implementation overrides (null -> global default)."""
    implement_model: Literal["sonnet", "opus", "haiku"] | None = None
    implement_effort: Literal["low", "medium", "high"] | None = None


class HeroCombosReq(BaseModel):
    pool: list[int] | None = None
    size: int = Field(2, ge=2, le=3)
    mode: str = "synergy"          # "synergy" | "kills_per_min"
    top_k: int = 15


class ComboExplainReq(BaseModel):
    """One combo row from the Discover table; heroes by localized name."""
    heroes: list[str] = Field(..., min_length=2, max_length=3)
    synergy: float
    avg_winprob: float | None = None
    kpm: float | None = None
