"""Pure checks for claims, timing, assets and captions."""

import re
from .models import CampaignPlan
from .prompts import BANNED_COPY
from .facts import normalized, copy_issues
from .generation_contract import creation_issues


def word_count(text):
    return sum((any((char.isalnum() for char in word)) for word in text.split()))


def spoken_word_count(text):
    expanded = text
    terms = {
        "141": "one forty one",
        "42": "forty two",
        "75": "seventy five",
        "IPX4": "I P X four",
        "forty-one": "forty one",
    }
    for source, target in terms.items():
        expanded = re.sub(
            "\\b" + re.escape(source) + "\\b", target, expanded, flags=re.I
        )
    return word_count(expanded)


def scene_copy_issues(scene, label, is_cta=False):
    issues = []
    if not is_cta and word_count(scene.headline) > 6:
        issues.append(label + ": shorten headline to 6 words.")
    if normalized(scene.caption) != normalized(scene.narration):
        issues.append(label + ": caption must exactly transcribe narration.")
    if spoken_word_count(scene.narration) > scene.seconds * 2.6:
        issues.append(label + ": narration exceeds 2.6 words per second.")
    text = " ".join([scene.headline, scene.caption, scene.narration]).casefold()
    matched = [phrase for phrase in BANNED_COPY if phrase in text]
    if matched:
        issues.append(label + ": replace filler phrase " + repr(matched[0]))
    return issues


def language_copy_issues(plan, brief):
    issues = []
    for number, ad in enumerate(plan.ads, 1):
        for index, scene in enumerate(ad.scenes):
            text = scene.headline + scene.caption + scene.narration
            if re.search("[ऀ-ॿ]", text):
                issues.append(f"Ad {number}, scene {index + 1}: English only.")
    return issues


COMMON_CLAIMS = [
    "ANC",
    "noise cancellation",
    "waterproof",
    "underwater",
    "80ms",
    "7 hours per charge",
    "seamless pairing",
    "crystal clear",
    "premium sound",
    "immersive sound",
    "clear audio",
    "powerful sound",
    "impressive audio",
    "all-day battery",
    "best bass",
    "improve focus",
    "discount",
]


def claim_copy_issues(plan, brief):
    if brief.get("schema_version", 1) >= 2:
        return generic_claim_issues(plan, brief)
    issues = []
    for number, ad in enumerate(plan.ads, 1):
        for scene in ad.scenes:
            text = " ".join(
                [scene.headline, scene.caption, scene.narration, scene.footnote]
            )
            for claim in COMMON_CLAIMS:
                if re.search("\\b" + re.escape(claim) + "\\b", text, re.I):
                    issues.append(f"Ad {number}: forbidden claim: {claim}.")
            issues.extend(scene_asset_claim_issues(scene, brief, f"Ad {number}"))
    return issues


def scene_asset_claim_issues(scene, brief, label):
    issues = []
    allowed = {"product_open", "product_closed", "commute_walk", "commute_transit"}
    if set(scene.asset_ids) - allowed:
        issues.append(label + ": unknown/style-only asset ID.")
    if set(scene.claim_ids) - set(brief["approved_claims"]):
        issues.append(label + ": claim ID is not approved.")
    spoken = scene.narration.casefold()
    visible = " ".join([scene.headline, scene.caption, scene.footnote]).casefold()
    if "42" in visible or "forty-two" in spoken or "battery_42" in scene.claim_ids:
        if not all((term in spoken for term in ["up to", "total", "case"])):
            issues.append(
                label + ": speak up to 42 hours TOTAL INCLUDING THE CHARGING CASE."
            )
        if not all(
            (term in scene.footnote.casefold() for term in ["up to", "total", "case"])
        ):
            issues.append(
                label + ": battery footnote needs up-to/total/case qualifier."
            )
    if "75" in visible or "seventy-five" in spoken or "charge_5_75" in scene.claim_ids:
        if not all((term in spoken for term in ["up to", "charg", "playback"])):
            issues.append(label + ": qualify charging time versus up-to playback.")
    return issues


def plan_copy_issues(plan, brief):
    issues, hooks = ([], [])
    if len(plan.ads) != brief["count"]:
        issues.append("Return the requested ad count.")
    if brief["count"] == 3 and [a.video_type for a in plan.ads] != brief["formats"]:
        issues.append("Return videos in the formats and order specified by the brief.")
    for number, ad in enumerate(plan.ads, 1):
        label, last = (f"Ad {number}", ad.scenes[-1])
        hooks.append(ad.scenes[0].headline.strip().casefold())
        if sum((s.seconds for s in ad.scenes)) != 20:
            issues.append(label + ": total must be exactly 20s.")
        if (
            last.headline != brief["cta"]
            or last.narration != brief["cta"]
            or last.seconds < 3
        ):
            issues.append(
                label + ": >=3s end card; exact CTA headline AND narration only."
            )
        if ad.video_type == "student_commute" and (
            not 2 <= word_count(ad.search_query) <= 12
        ):
            issues.append(label + ": concrete stock query required.")
        used = {asset for scene in ad.scenes for asset in scene.asset_ids}
        if used - set(ad.assets_required):
            issues.append(label + ": assets_required misses a scene asset.")
        if (
            brief.get("schema_version", 1) < 2
            and ad.video_type != "student_commute"
            and any((a.startswith("commute_") for a in used))
        ):
            issues.append(label + ": only commute uses stock.")
        for index, scene in enumerate(ad.scenes):
            issues.extend(
                scene_copy_issues(scene, f"{label}, scene {index + 1}", index == 4)
            )
    if len(hooks) != len(set(hooks)):
        issues.append("Distinct opening hooks required.")
    return (
        issues
        + language_copy_issues(plan, brief)
        + claim_copy_issues(plan, brief)
        + (copy_issues(plan, brief) if brief.get("schema_version", 1) >= 2 else [])
        + (numeric_claim_issues(plan) if brief.get("schema_version", 1) < 2 else [])
        + creation_issues(plan, brief)
    )


def generic_claim_issues(plan, brief):
    """Validate this campaign's facts/assets without inheriting any product's specs."""
    issues = []
    facts = brief.get("approved_claims", {})
    identity = set(brief.get("asset_ids", [])) - {
        key for key in brief.get("asset_ids", []) if key.startswith("style_")
    }
    allowed = identity | (
        {"lifestyle_clip", "lifestyle_detail"} if brief.get("use_footage") else set()
    )
    product_context = " ".join(
        str(brief.get(key, "")) for key in ["product", "brand", "variant", "cta"]
    )
    normal = normalized
    for number, ad in enumerate(plan.ads, 1):
        for scene in ad.scenes:
            label = f"Ad {number}: "
            if set(scene.asset_ids) - allowed:
                issues.append(
                    label
                    + "use only this campaign's identity images or selected lifestyle footage."
                )
            if len(scene.asset_ids) != 1:
                issues.append(
                    label + "choose one visual source per scene for the local renderer."
                )
            if set(scene.claim_ids) - set(facts):
                issues.append(label + "claim ID is not approved.")
            authorized = " ".join(facts.get(key, "") for key in scene.claim_ids)
            visible = " ".join([scene.headline, scene.narration, scene.footnote])
            approved_numbers = set(
                re.findall(
                    r"\d+(?:[.,]\d+)?", normalized(authorized + " " + product_context)
                )
            )
            for value in re.findall(r"\d+(?:[.,]\d+)?", normalized(visible)):
                if value not in approved_numbers:
                    issues.append(
                        label + f"unapproved number or specification: {value}."
                    )
            for key in scene.claim_ids:
                fact = facts.get(key)
                if fact and normal(fact) not in normal(visible):
                    issues.append(
                        label
                        + "quote the full approved fact in narration or a footnote, including qualifiers."
                    )
                if (
                    fact
                    and re.search(r"\d", scene.narration)
                    and set(re.findall(r"\d+", fact))
                    & set(re.findall(r"\d+", scene.narration))
                ):
                    for qualifier in ["up to", "at least", "total"]:
                        if (
                            qualifier in fact.casefold()
                            and qualifier not in scene.narration.casefold()
                        ):
                            issues.append(
                                label
                                + f"keep the '{qualifier}' qualifier in spoken copy."
                            )
            for phrase in COMMON_CLAIMS:
                if (
                    re.search(r"\b" + re.escape(phrase) + r"\b", visible, re.I)
                    and phrase.casefold() not in authorized.casefold()
                ):
                    issues.append(
                        label + "claim is not in the selected approved facts: " + phrase
                    )
            if re.search(
                r"[₹$]|\b(?:INR|Rs\.?|MRP)\s*\d|\d+\s*%\s*off", visible, re.I
            ) and not re.search(
                r"[₹$]|\b(?:INR|Rs\.?|MRP)\s*\d|\d+\s*%\s*off", authorized, re.I
            ):
                issues.append(
                    label + "prices and discounts require an explicit approved fact."
                )
    return issues


def numeric_claim_issues(plan):
    """Reject obvious invented specs/prices in manual copy as well as AI drafts."""
    issues = []
    for ad in plan.ads:
        for scene in ad.scenes:
            text = " ".join(
                [scene.headline, scene.caption, scene.narration, scene.footnote]
            )
            if re.search(r"₹|\$|\b(?:INR|Rs\.?|MRP)\s*\d|\d+\s*%\s*off", text, re.I):
                issues.append("Prices and discounts are not approved product claims.")
            for value, unit in re.findall(
                r"\b(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?|mm)\b", text, re.I
            ):
                allowed = (
                    {8}
                    if unit.lower() == "mm"
                    else {42}
                    if unit.lower().startswith("h")
                    else {5, 75}
                )
                if float(value) not in allowed:
                    issues.append(f"Unapproved product specification: {value} {unit}.")
            if any(
                value != "4" for value in re.findall(r"\bIPX\s*(\d+)\b", text, re.I)
            ):
                issues.append("Only IPX4 sweat and water resistance is approved.")
    return issues


def factual_review_issues(review):
    terms = [
        "unsupported",
        "unverified",
        "invented",
        "not in the facts",
        "not supported",
        "not provided",
        "performance benefit",
    ]
    return review.unsupported_claims + [
        issue
        for issue in review.issues
        if any((term in issue.casefold() for term in terms))
    ]


def fit_narration_timing(candidate, brief=None):
    if brief and brief.get("schema_version", 1) >= 2:
        return candidate
    ads = candidate.ads if isinstance(candidate, CampaignPlan) else [candidate]
    for ad in ads:
        for scene in ad.scenes:
            if "battery_42" in scene.claim_ids:
                scene.footnote = (
                    "Up to 42 hours total playback, including the charging case."
                )
            if "charge_5_75" in scene.claim_ids:
                scene.footnote = "5 minutes charging: up to 75 minutes playback."
    return candidate
