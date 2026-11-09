"""Executable creation intent shared by planners, validation and the frontend."""

from .timeline import SceneEdit, FootageSettings, generation_dependency
from .storage import digest

CREATION_CHOICES = {
    "edit": "Edit existing images and videos",
    "generate": "Generate new AI footage",
    "combine": "Combine generated footage with supplied assets",
}


def stage_generation_issue(brief, ads, stage, *, new_jobs=None, remaining_jobs=None):
    """Shared UI/backend policy for the actual stage, independent of planning."""
    count = sum(
        s.get("edit", {}).get("mode", "reused") != "reused"
        for a in ads
        for s in a["scenes"]
    )
    if not count:
        return ""
    profile = brief.get("generation")
    if not profile:
        return "AI footage needs COMFY_PROFILE and COMFY_WORKFLOW in .env. Run Check local setup, configure a measured profile using docs/local-generation.md, then save and approve a fresh plan."
    if not profile.get("batch_enabled") and (stage == "batch" or count > 1):
        return "This CPU/experimental profile supports one sample scene only; campaign batches are disabled. Use Combine with one AI scene in the representative sample, or configure a measured batch-capable COMFY_PROFILE and save and approve a fresh plan."
    needed = count if new_jobs is None else new_jobs
    remaining = (
        brief.get("limits", {}).get("generation_jobs", 0)
        if remaining_jobs is None
        else remaining_jobs
    )
    if needed and remaining <= 0:
        return "Generation-job allowance is zero or exhausted. Set a finite CFC_DEFAULT_LIMITS_JSON generation_jobs allowance on the server, then use Review updated generation allowance and approve the new estimate. No AI scene will switch to image animation automatically."
    if needed > remaining:
        return f"This stage needs {needed} new AI jobs but only {remaining} remain. Update the server's CFC_DEFAULT_LIMITS_JSON generation_jobs allowance, then Review updated generation allowance and approve a fresh estimate."
    return ""


def apply_creation(plan, brief):
    """Compile a brief choice into executable instructions, without any inference."""
    mode = brief.get("creation_mode", "edit")
    if mode == "edit":
        return plan
    settings = FootageSettings.model_validate(
        brief["generation"]["settings"]
    ).model_dump()
    for ad in plan["ads"]:
        for index, scene in enumerate(ad["scenes"]):
            generated = (
                mode == "generate"
                or index == 0
                or scene.get("edit", {}).get("mode", "reused") != "reused"
            )
            scene["purpose"] = scene.get("purpose") or (
                "Introduce the product"
                if index == 0
                else "End with the approved call to action"
                if index == 4
                else "Show product details"
            )
            if not generated:
                continue
            asset = next(
                (a for a in scene["asset_ids"] if a.startswith("product_")),
                "product_main",
            )
            edit = SceneEdit.model_validate(scene.get("edit", {})).model_dump()
            edit.update(
                mode="generated",
                conditioning_asset=asset,
                generation_settings=edit.get("generation_settings") or settings,
                generation_prompt=edit.get("generation_prompt")
                or (
                    f"A product shot of {brief['product']}, {brief.get('variant', '')}. "
                    "Use the supplied image as the initial frame. A slow, restrained camera movement "
                    "and soft studio lighting. Keep the same silhouette, materials, colour and "
                    "proportions. Do not add text, people, packaging or product features. "
                    f"Scene purpose: {scene['purpose']}. Creative direction: {brief.get('style', '')}."
                ),
                seed=edit["seed"]
                if edit["seed"] != 1
                else index
                + 1
                + {
                    "product_demo": 100,
                    "lifestyle_story": 200,
                    "product_reveal": 300,
                }.get(ad["video_type"], 0),
            )
            scene["asset_ids"] = [asset]
            scene["edit"] = edit
        ad["assets_required"] = list(
            dict.fromkeys(
                [key for scene in ad["scenes"] for key in scene["asset_ids"]]
                + ["voiceover", "original_music", "captions"]
            )
        )
    return plan


def creation_issues(plan, brief):
    issues = []
    all_modes = [s.edit.mode for ad in plan.ads for s in ad.scenes]
    if brief.get("creation_mode") == "combine" and (
        all(m == "reused" for m in all_modes)
        or all(m == "generated" for m in all_modes)
    ):
        issues.append(
            "Combined creation needs generated footage and supplied-asset editing/compositing in the campaign."
        )
    for ad in plan.ads:
        for scene in ad.scenes:
            edit = scene.edit
            if edit.mode == "reused":
                if brief.get("creation_mode") == "generate":
                    issues.append(
                        "Generate mode requires generated footage in every scene."
                    )
                continue
            if brief.get("creation_mode") == "edit":
                issues.append(
                    "Choose generated or combined creation in the brief before requesting AI footage."
                )
            if not edit.generation_settings or not edit.conditioning_asset:
                issues.append(
                    "Generated/composited scenes need explicit settings and an identity conditioning image."
                )
            elif (
                edit.conditioning_asset not in scene.asset_ids
                or edit.conditioning_asset.startswith(
                    ("style_", "lifestyle_", "commute_")
                )
            ):
                issues.append(
                    "Conditioning must use a planned product identity image, not reference media or footage."
                )
            if not scene.purpose:
                issues.append("Generated scenes need a stated purpose.")
            if (
                edit.generation_settings
                and edit.generation_settings.model_dump()
                not in [
                    FootageSettings.model_validate(s).model_dump()
                    for s in brief.get("generation", {}).get("tested_settings", [])
                ]
            ):
                issues.append(
                    "Use output settings measured in this campaign's generation profile."
                )
    return issues


def generation_key(scene, brief):
    edit = SceneEdit.model_validate(scene["edit"])
    return digest(
        [
            "comfy-i2v-v1",
            edit.generation_prompt,
            edit.seed,
            edit.conditioning_asset,
            brief.get("asset_hashes", {}).get(edit.conditioning_asset),
            edit.generation_settings.model_dump() if edit.generation_settings else None,
            generation_dependency(brief),
        ]
    )
