"""Product-independent briefs, validated uploads and a free local starting plan."""

from io import BytesIO
from pathlib import Path
import hashlib
import re
from urllib.parse import urlparse
from PIL import Image, UnidentifiedImageError
from .storage import save_json

FORMATS = ["product_demo", "lifestyle_story", "product_reveal"]


def build_brief(
    product,
    audience,
    goal,
    *,
    brand="",
    variant="",
    claims=None,
    cta="",
    style="Energetic, clear and realistic",
    competitors=None,
    product_url="",
    accent_color="#00AABD",
    use_footage=False,
    **voice,
):
    product, audience, goal = product.strip(), audience.strip(), goal.strip()
    if not product or not audience or not goal:
        raise ValueError("Add a product name, audience and campaign goal.")
    if len(product) > 80 or len(brand.strip()) > 80 or len(variant.strip()) > 100:
        raise ValueError(
            "Use a product and brand name up to 80 characters and variant up to 100."
        )
    cta = cta.strip() or f"Explore {product}."
    if len(cta) > 90 or len(cta.split()) > 10:
        raise ValueError(
            "Keep the ending call to action within 90 characters and 10 words so it fits in four seconds."
        )
    from .validation import spoken_word_count

    if spoken_word_count(cta) > 10:
        raise ValueError(
            "Shorten the ending call to action so it can be spoken clearly in four seconds."
        )
    lines = claims.splitlines() if isinstance(claims, str) else list(claims or [])
    lines = list(
        dict.fromkeys(str(line).strip() for line in lines if str(line).strip())
    )
    if len(lines) > 12 or any(len(line) > 160 for line in lines):
        raise ValueError(
            "Add up to 12 approved facts, each at most 160 characters. Keep all qualifiers."
        )
    product_url = product_url.strip()
    if product_url and (
        urlparse(product_url).scheme not in ["http", "https"]
        or not urlparse(product_url).netloc
    ):
        raise ValueError("Use a full http:// or https:// product page URL.")
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", accent_color):
        raise ValueError("Choose a valid accent colour.")
    names = (
        competitors.splitlines()
        if isinstance(competitors, str)
        else list(competitors or [])
    )
    return dict(
        schema_version=2,
        product=product,
        brand=brand.strip(),
        variant=variant.strip(),
        audience=audience,
        goal=goal,
        cta=cta,
        style=style.strip(),
        language="English",
        count=3,
        duration=20,
        aspect_ratio="9:16",
        formats=FORMATS.copy(),
        approved_claims={f"claim_{i}": fact for i, fact in enumerate(lines, 1)},
        competitors=[str(n).strip() for n in names if str(n).strip()][:3],
        product_url=product_url,
        accent_color=accent_color,
        use_footage=bool(use_footage),
        **voice,
    )


def prepare_images(main, detail=None, references=(), allow_incomplete=False):
    """Validate everything before creating campaign files. Preserve original image bytes."""
    if not main and not allow_incomplete:
        raise ValueError("Upload a main product image to create a campaign.")
    references = list(references or [])
    if len(references) > 5:
        raise ValueError("Use up to five optional style reference images.")
    prepared = []
    for asset_id, upload, use in [
        ("product_main", main, "identity"),
        ("product_detail", detail or main, "identity"),
        *[(f"style_{i}", item, "style_only") for i, item in enumerate(references, 1)],
    ]:
        if upload is None:
            continue
        name, content = upload
        if not isinstance(content, bytes) or not 0 < len(content) <= 20 * 1024 * 1024:
            raise ValueError(
                "Each product or reference image must be between 1 byte and 20 MB."
            )
        try:
            with Image.open(BytesIO(content)) as image:
                if (
                    image.format not in ["PNG", "JPEG", "WEBP"]
                    or image.width * image.height > 40_000_000
                ):
                    raise ValueError(
                        "Use PNG, JPEG or WebP images up to 40 megapixels."
                    )
                image.verify()
                fmt, size = image.format, image.size
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
            raise ValueError(
                "An uploaded image could not be read. Use PNG, JPEG or WebP."
            ) from error
        suffix = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[fmt]
        prepared.append(
            (
                dict(
                    asset_id=asset_id,
                    use=use,
                    title=Path(name).name[:120],
                    file=asset_id
                    + "-"
                    + hashlib.sha256(content).hexdigest()[:12]
                    + suffix,
                    size=list(size),
                    sha256=hashlib.sha256(content).hexdigest(),
                ),
                content,
            )
        )
    return prepared


def store_images(folder, prepared):
    folder.mkdir(parents=True, exist_ok=True)
    for asset, content in prepared:
        (folder / asset["file"]).write_bytes(content)
    save_json(folder / "asset_manifest.json", {"assets": [a for a, _ in prepared]})


def template_plan(brief):
    """A truthful editable draft using only this brief; no API or media generation."""
    facts = list(brief["approved_claims"].items())
    from .facts import normalized

    cta_claims = [
        key for key, fact in facts if normalized(brief["cta"]) == normalized(fact)
    ]

    def fact_scene(index, seconds, fallback):
        if index >= len(facts):
            return fallback, [], ""
        key, fact = facts[index]
        spoken = fact if len(fact.split()) <= int(seconds * 2.2) else fallback
        return spoken, [key], fact

    first, ids1, note1 = fact_scene(0, 5, "Take a closer look at the details.")
    second, ids2, note2 = fact_scene(1, 4, "See the product for yourself.")
    scripts = [
        (
            "product_demo",
            "Details worth a look",
            "A clear product tour built around the supplied images and approved facts.",
            [
                "Start with the details.",
                first,
                second,
                "See it from another angle.",
                brief["cta"],
            ],
            [
                "Start with the details",
                "A closer look",
                "In the details",
                "Another perspective",
                brief["cta"],
            ],
        ),
        (
            "lifestyle_story",
            "Part of your story",
            "An everyday story for the selected audience, using product inserts and optional licensed lifestyle footage.",
            [
                "A new day. Your own routine.",
                "Make space for what matters.",
                "Take a closer look at the details.",
                "Find your own reason to explore.",
                brief["cta"],
            ],
            [
                "Your day, your story",
                "Set the scene",
                "Take a closer look",
                "Find your reason",
                brief["cta"],
            ],
        ),
        (
            "product_reveal",
            "The closer look",
            "A graphic mask reveal and deliberate cuts between the actual uploaded product images.",
            [
                "Something worth a closer look.",
                "A different angle. The same product.",
                "See the details for yourself.",
                "Now, take it all in.",
                brief["cta"],
            ],
            [
                "Look a little closer",
                "A different perspective",
                "Meet the details",
                "The full picture",
                brief["cta"],
            ],
        ),
    ]
    ads = []
    for kind, title, concept, narration, headlines in scripts:
        scenes = []
        for i, seconds in enumerate([3, 5, 4, 4, 4]):
            image = "product_detail" if i in [1, 2] else "product_main"
            if (
                kind == "lifestyle_story"
                and brief.get("use_footage")
                and i in [0, 1, 3]
            ):
                image = "lifestyle_clip"
            scenes.append(
                dict(
                    headline=headlines[i],
                    caption=narration[i],
                    narration=narration[i],
                    seconds=seconds,
                    asset_ids=[image],
                    claim_ids=ids1
                    if kind == "product_demo" and i == 1
                    else ids2
                    if kind == "product_demo" and i == 2
                    else cta_claims
                    if i == 4
                    else [],
                    footnote=note1
                    if kind == "product_demo" and i == 1
                    else note2
                    if kind == "product_demo" and i == 2
                    else "",
                    visual_direction=f"Use {image} with a slow pan and a clean text panel. Preserve the exact supplied product; end on a complete product view.",
                )
            )
        ads.append(
            dict(
                video_type=kind,
                title=title,
                concept=concept,
                music_direction="Original low-volume instrumental beat, with voice ducking and a gentle ending.",
                assets_required=list(
                    dict.fromkeys([a for s in scenes for a in s["asset_ids"]])
                )
                + ["voiceover", "original_music", "captions"],
                search_query="",
                scenes=scenes,
            )
        )
    from .models import CampaignPlan

    result = dict(
        summary=f"Three distinct {brief['product']} concepts for {brief['audience']}. Review this local starting draft before creating media.",
        ads=ads,
    )
    for ad in result["ads"]:
        ad["audio"] = {
            "bpm": 100
            if ad["video_type"] == "lifestyle_story"
            else 112
            if ad["video_type"] == "product_reveal"
            else 108
        }
        if ad["video_type"] == "product_reveal":
            ad["scenes"][0]["edit"] = {"mask": "wipe_left"}
    return CampaignPlan.model_validate(result).model_dump()
