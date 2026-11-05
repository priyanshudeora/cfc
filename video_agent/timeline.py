"""Validated editing instructions. No raw FFmpeg filters or shell fragments."""

import copy
import re
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator
from .storage import digest


class TextLayout(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    x: float = Field(default=0.078, ge=0, le=0.8)
    y: float = Field(default=0.164, ge=0.14, le=0.17)
    width: float = Field(default=0.844, ge=0.3, le=0.92)
    size: int = Field(default=72, ge=36, le=100)


class SceneEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    mode: Literal["reused", "generated", "hybrid"] = "reused"
    trim_start: float = Field(default=0, ge=0, le=3600)
    trim_end: float | None = Field(default=None, gt=0, le=3600)
    x: float = Field(default=0.10, ge=0, le=0.7)
    y: float = Field(default=0.31, ge=0.30, le=0.55)
    width: float = Field(default=0.80, ge=0.2, le=0.95)
    height: float = Field(default=0.39, ge=0.15, le=0.6)
    fit: Literal["contain", "cover"] = "contain"
    zoom_start: float = Field(default=1, ge=1, le=1.3)
    zoom_end: float = Field(default=1.02, ge=1, le=1.3)
    pan_x: float = Field(default=0, ge=-0.2, le=0.2)
    pan_y: float = Field(default=0, ge=-0.2, le=0.2)
    mask: Literal["none", "wipe_left", "circle"] = "none"
    transition: Literal["cut", "fade"] = "cut"
    transition_seconds: float = Field(default=0.25, ge=0.1, le=0.5)
    text: TextLayout = Field(default_factory=TextLayout)
    generation_prompt: str = Field(default="", max_length=1500)
    seed: int = Field(default=1, ge=0, le=2147483647)

    @model_validator(mode="after")
    def boundaries(self):
        if self.x + self.width > 1 or self.y + self.height > 0.71:
            raise ValueError(
                "Keep the product panel inside the canvas and above the caption safe area."
            )
        if self.text.x + self.text.width > 1:
            raise ValueError("Headline text must remain within the canvas.")
        if self.trim_end is not None and self.trim_end <= self.trim_start:
            raise ValueError("Clip end must be after clip start.")
        if self.mode != "reused" and not self.generation_prompt.strip():
            raise ValueError(
                "Generated/hybrid scenes require an explicit generation prompt."
            )
        return self


class AudioEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    bpm: int = Field(default=108, ge=60, le=160)
    music_gain: float = Field(default=0.14, ge=0, le=0.4)
    voice_gain: float = Field(default=1, ge=0.25, le=1.5)


def ad_key(ad, brief):
    """Actual media dependencies, without campaign version or review identifiers."""
    from .config import RENDER_VERSION
    from .models import AdPlan

    value = AdPlan.model_validate(ad).model_dump()
    scenes = [
        {k: v for k, v in scene.items() if k not in ["visual_direction", "claim_ids"]}
        for scene in value["scenes"]
    ]
    used = {key for scene in scenes for key in scene["asset_ids"]}
    return digest(
        [
            RENDER_VERSION,
            scenes,
            value["audio"],
            {
                k: brief.get(k)
                for k in [
                    "brand",
                    "product",
                    "variant",
                    "accent_color",
                    "voice_backend",
                    "voice_id",
                    "voice_model",
                    "voice_speed",
                    "pronunciations",
                ]
            },
            {k: v for k, v in brief.get("asset_hashes", {}).items() if k in used},
            brief.get("generation")
            if any(scene["edit"]["mode"] != "reused" for scene in scenes)
            else None,
        ]
    )


def revise_timeline(plan, index, feedback):
    """A small explicit conversational editor; unrecognized instructions never silently pass."""
    revised = copy.deepcopy(plan)
    ad = revised["ads"][index]
    text = feedback.strip().lower()
    clauses = [
        part.strip(" .") for part in re.split(r";|\band\b", text) if part.strip(" .")
    ]
    targets = ad["scenes"]
    match = re.search(r"scene\s+([1-5])", text)
    if match:
        targets = [targets[int(match.group(1)) - 1]]
        clauses = [
            re.sub(r"(?:in |for )?scene\s+[1-5][:,]?\s*", "", part).strip()
            for part in clauses
        ]
    for clause in clauses:
        if clause in [
            "zoom in",
            "zoom out",
            "no zoom",
            "move product left",
            "move product right",
            "fade transitions",
            "cut transitions",
            "wipe reveal",
            "remove mask",
            "larger text",
            "smaller text",
        ]:
            for scene in targets:
                edit = SceneEdit.model_validate(scene.get("edit", {})).model_dump()
                if clause == "zoom in":
                    edit.update(zoom_start=1, zoom_end=1.15)
                elif clause == "zoom out":
                    edit.update(zoom_start=1.15, zoom_end=1)
                elif clause == "no zoom":
                    edit.update(zoom_start=1, zoom_end=1)
                elif clause == "move product left":
                    edit.update(x=0.02, width=0.78)
                elif clause == "move product right":
                    edit.update(x=0.20, width=0.78)
                elif clause == "fade transitions":
                    edit["transition"] = "fade"
                elif clause == "cut transitions":
                    edit["transition"] = "cut"
                elif clause == "wipe reveal":
                    edit["mask"] = "wipe_left"
                elif clause == "remove mask":
                    edit["mask"] = "none"
                elif clause == "larger text":
                    edit["text"]["size"] = min(100, edit["text"]["size"] + 12)
                elif clause == "smaller text":
                    edit["text"]["size"] = max(36, edit["text"]["size"] - 12)
                scene["edit"] = SceneEdit.model_validate(edit).model_dump()
        elif clause in [
            "no music",
            "quieter music",
            "louder music",
            "faster beat",
            "slower beat",
        ]:
            audio = AudioEdit.model_validate(ad.get("audio", {})).model_dump()
            if clause == "no music":
                audio["music_gain"] = 0
            elif clause == "quieter music":
                audio["music_gain"] = max(0, audio["music_gain"] * 0.5)
            elif clause == "louder music":
                audio["music_gain"] = min(0.4, audio["music_gain"] + 0.06)
            elif clause == "faster beat":
                audio["bpm"] = min(160, audio["bpm"] + 12)
            elif clause == "slower beat":
                audio["bpm"] = max(60, audio["bpm"] - 12)
            ad["audio"] = AudioEdit.model_validate(audio).model_dump()
        else:
            raise ValueError(
                "Unsupported local edit: "
                + clause
                + ". Use a supported timeline control, or request an AI script revision."
            )
    return revised
