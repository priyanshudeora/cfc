"""Validated campaign and reviewer schemas."""

from typing import Literal
from pydantic import BaseModel, Field
from .timeline import SceneEdit, AudioEdit


class Scene(BaseModel):
    purpose: str = Field(default="", max_length=300)
    headline: str = Field(min_length=1, max_length=90)
    caption: str = Field(min_length=1, max_length=240)
    narration: str = Field(min_length=1, max_length=240)
    seconds: int = Field(ge=2, le=7)
    asset_ids: list[str] = Field(min_length=1, max_length=2)
    claim_ids: list[str] = Field(default_factory=list, max_length=8)
    visual_direction: str = Field(min_length=10, max_length=650)
    footnote: str = Field(default="", max_length=160)
    edit: SceneEdit = Field(default_factory=SceneEdit)


class AdPlan(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    video_type: Literal[
        "product_demo", "lifestyle_story", "product_reveal", "student_commute"
    ]
    concept: str = Field(min_length=10, max_length=450)
    music_direction: str = Field(min_length=10, max_length=240)
    assets_required: list[str] = Field(min_length=1, max_length=8)
    search_query: str = Field(default="", max_length=90)
    scenes: list[Scene] = Field(min_length=5, max_length=5)
    audio: AudioEdit = Field(default_factory=AudioEdit)


class CampaignPlan(BaseModel):
    summary: str = Field(min_length=1, max_length=650)
    ads: list[AdPlan] = Field(min_length=1, max_length=3)


class CopyReview(BaseModel):
    score: int = Field(ge=0, le=10)
    issues: list[str] = Field(default_factory=list, max_length=6)
    unsupported_claims: list[str] = Field(default_factory=list, max_length=6)
