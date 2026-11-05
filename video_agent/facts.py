"""Conservative product-independent factual copy checks and number normalization."""

import re

SMALL = dict(
    zip(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split(),
        range(20),
    )
)
TENS = dict(
    zip(
        "twenty thirty forty fifty sixty seventy eighty ninety".split(),
        range(20, 100, 10),
    )
)
SAFE_COPY = {
    "start with the details",
    "a closer look",
    "in the details",
    "another perspective",
    "see it from another angle",
    "see the product for yourself",
    "take a closer look at the details",
    "a new day your own routine",
    "make space for what matters",
    "find your own reason to explore",
    "your day your story",
    "set the scene",
    "take a closer look",
    "find your reason",
    "something worth a closer look",
    "a different angle the same product",
    "see the details for yourself",
    "now take it all in",
    "look a little closer",
    "a different perspective",
    "meet the details",
    "the full picture",
    "here are the details",
    "look closer",
    "meet the product",
}


def normalized(text):
    words = re.findall(
        r"\d+(?:\.\d+)?|[^\W\d_]+", text.casefold().replace("-", " "), re.UNICODE
    )
    out, i = [], 0
    while i < len(words):
        if words[i] not in SMALL and words[i] not in TENS:
            out.append(words[i])
            i += 1
            continue
        total, chunk, j = 0, 0, i
        while j < len(words):
            word = words[j]
            if word in SMALL:
                chunk += SMALL[word]
            elif word in TENS:
                chunk += TENS[word]
            elif word == "hundred":
                chunk = max(chunk, 1) * 100
            elif word in ["thousand", "million"]:
                total += max(chunk, 1) * (1000 if word == "thousand" else 1000000)
                chunk = 0
            elif word == "and" and j + 1 < len(words) and words[j + 1] in SMALL | TENS:
                pass
            else:
                break
            j += 1
        number = str(total + chunk)
        if j + 1 < len(words) and words[j] == "point" and words[j + 1] in SMALL:
            number += "."
            j += 1
            while j < len(words) and words[j] in SMALL and SMALL[words[j]] < 10:
                number += str(SMALL[words[j]])
                j += 1
        out.append(number)
        i = j
    aliases = {
        "hours": "hour",
        "hrs": "hour",
        "minutes": "minute",
        "mins": "minute",
        "millimeters": "mm",
        "millimetres": "mm",
        "millimeter": "mm",
        "millimetre": "mm",
        "litres": "l",
        "liters": "l",
        "litre": "l",
        "liter": "l",
    }
    return " ".join(aliases.get(word, word) for word in out)


def copy_issues(plan, brief):
    facts = {
        key: normalized(value)
        for key, value in brief.get("approved_claims", {}).items()
    }
    safe = {
        normalized(value)
        for value in SAFE_COPY | set(brief.get("approved_creative_copy", []))
    }
    safe |= {normalized(brief.get(key, "")) for key in ["brand", "product", "variant"]}
    cta = normalized(brief.get("cta", ""))
    cta_copy = brief.get("cta", "")
    for key in ["brand", "product", "variant"]:
        if brief.get(key):
            cta_copy = re.sub(re.escape(brief[key]), " ", cta_copy, flags=re.I)
    factual = re.search(
        r"\b(?:warranty|lifetime|free|discount|save|hours?|minutes?|playback|battery|proven|certified|results?|guarantee|waterproof|anc)\b|[%₹$]",
        cta_copy,
        re.I,
    )
    identity_numbers = set(
        re.findall(
            r"\d+",
            normalized(brief.get("product", "") + " " + brief.get("variant", "")),
        )
    )
    if not factual and set(re.findall(r"\d+", cta)) <= identity_numbers:
        safe.add(cta)
    issues = []
    for number, ad in enumerate(plan.ads, 1):
        for index, scene in enumerate(ad.scenes, 1):
            selected = {key: facts[key] for key in scene.claim_ids if key in facts}
            for field in ["headline", "narration", "caption", "footnote"]:
                original = getattr(scene, field)
                value = normalized(original)
                if not value or value in safe:
                    continue
                matched = [
                    fact for fact in selected.values() if value == fact or fact in value
                ]
                # Headline shortening is allowed only when all of its words occur in a selected fact.
                if (
                    field == "headline"
                    and selected
                    and any(
                        set(value.split()) <= set(fact.split())
                        for fact in selected.values()
                    )
                ):
                    continue
                if not matched:
                    issues.append(
                        f"Fact review required: ad {number}, scene {index}, {field}: {original}"
                    )
                    continue
                remainder = value
                for fact in sorted(matched, key=len, reverse=True):
                    remainder = remainder.replace(fact, " ")
                if remainder.strip() and normalized(remainder) not in safe:
                    issues.append(
                        f"Fact review required: ad {number}, scene {index}, {field}: {original}"
                    )
            for fact in selected.values():
                spoken = normalized(scene.narration)
                # Never allow a qualified numeric fact to become an unqualified spoken promise.
                if set(re.findall(r"\d+(?:\.\d+)?", fact)) & set(
                    re.findall(r"\d+(?:\.\d+)?", spoken)
                ):
                    qualifiers = [
                        term for term in ["up to", "at least", "total"] if term in fact
                    ]
                    including = re.search(r"\bincluding\b.*", fact)
                    if including:
                        qualifiers.append(including.group(0))
                    if any(term not in spoken for term in qualifiers):
                        issues.append(
                            f"Ad {number}, scene {index}: preserve spoken qualifiers; a footnote cannot correct narration."
                        )
    return issues
