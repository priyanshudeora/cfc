"""Estimates and independently metered resource allowances, with no invented API prices."""

from .control import DEFAULT_LIMITS
import math


def rates_for(values=None):
    values = values or {}
    if set(values) - {"model_tokens", "speech_characters", "generation_jobs"} or any(
        not isinstance(v, (int, float))
        or isinstance(v, bool)
        or not math.isfinite(v)
        or not 0 <= v <= 1_000_000_000
        for v in values.values()
    ):
        raise ValueError("Use nonnegative finite unit prices in micro USD per unit.")
    return dict(values)


def charge(usage, rates=None):
    rates = rates or {}
    value = dict(usage)
    value["cost_microusd"] = sum(
        round(value.get(key, 0) * float(rates.get(key, 0)))
        for key in ["model_tokens", "speech_characters", "generation_jobs"]
    )
    return value


def limits_for(values=None):
    limits = dict(DEFAULT_LIMITS, **(values or {}))
    if set(limits) - set(DEFAULT_LIMITS) or any(
        not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 1_000_000_000
        for v in limits.values()
    ):
        raise ValueError(
            "Usage limits must be nonnegative integer allowances for supported resources."
        )
    return limits


def estimate(brief, ads):
    external_voice = brief.get("voice_backend") == "elevenlabs"
    chars = sum(len(scene["narration"]) for ad in ads for scene in ad["scenes"])
    generations = sum(
        scene.get("edit", {}).get("mode", "reused") != "reused"
        for ad in ads
        for scene in ad["scenes"]
    )

    counts = charge(
        dict(
            speech_characters=chars if external_voice else 0,
            generation_jobs=generations,
        ),
        brief.get("usage_rates"),
    )
    return dict(
        **counts,
        render_seconds=3
        * sum(scene["seconds"] for ad in ads for scene in ad["scenes"]),
        voice_provider="ElevenLabs account credits"
        if external_voice
        else "Local Kokoro",
        currency_estimate=(
            f"USD {counts['cost_microusd'] / 1_000_000:.4f} at supplied rates"
            if brief.get("usage_rates")
            else "Not priced: account rates are not inferred"
        )
        if external_voice or generations
        else "No metered speech API charge; local compute cost not estimated",
        generation_provider="Optional local ComfyUI"
        if generations
        else "Reused assets / image-based motion graphics",
    )


def footage_estimate(ads):
    """Describe native generation separately from final scene/export duration."""
    scenes = [s for ad in ads for s in ad["scenes"]]
    generated = [s for s in scenes if s["edit"]["mode"] != "reused"]
    specs = []
    for scene in generated:
        settings = scene["edit"].get("generation_settings")
        if not settings:
            continue
        native = settings["frames"] / settings["fps"]
        item = dict(
            resolution=f"{settings['width']} × {settings['height']}",
            frames=settings["frames"],
            fps=settings["fps"],
            native_seconds=round(native, 3),
            scene_seconds=scene["seconds"],
            loops=scene["seconds"] > native,
        )
        if item not in specs:
            specs.append(item)
    return dict(
        generated_scenes=len(generated),
        reused_scenes=len(scenes) - len(generated),
        sample_jobs=sum(s["edit"]["mode"] != "reused" for s in ads[0]["scenes"])
        if ads
        else 0,
        remaining_jobs=sum(
            s["edit"]["mode"] != "reused" for ad in ads[1:] for s in ad["scenes"]
        ),
        clips=specs,
    )
