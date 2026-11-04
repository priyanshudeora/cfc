"""Readable campaign scripts."""


def plan_markdown(plan):
    lines = ["# Campaign plan", "", plan["summary"]]
    for number, ad in enumerate(plan["ads"], 1):
        lines += [
            "",
            f"## {number}. {ad['title']}",
            ad["concept"],
            "**Music:** " + ad["music_direction"],
            "**Assets:** " + ", ".join(ad["assets_required"]),
        ]
        elapsed = 0
        for scene in ad["scenes"]:
            end = elapsed + scene["seconds"]
            lines += [
                "",
                f"### {elapsed:02d}–{end:02d}s: {scene['headline']}",
                "**Visual:** " + scene["visual_direction"],
                "**Voiceover:** " + scene["narration"],
                "**Caption:** " + scene["caption"],
            ]
            if scene["footnote"]:
                lines += ["**Qualification:** " + scene["footnote"]]
            elapsed = end
    return "\n".join(lines)
