"""Multimodal Gemini planner and Tavily research, independent of the UI."""

import base64
import json
import time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from .models import *
from .prompts import *
from .validation import *
from .storage import read_json, save_json, digest
from .connections import api_json


class Planner:
    def __init__(self, folder, keys, assets, note, model="gemini-2.5-flash"):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.cache_dir = self.folder / "research_cache"
        self.cache_dir.mkdir(exist_ok=True)
        self.keys, self.assets, self.note, self.model = keys, assets, note, model

    def gemini_request(self, payload):
        key = self.keys.get("GEMINI_API_KEY") or ""
        if not key:
            raise RuntimeError("Gemini API key missing")
        for attempt in range(3):
            try:
                return api_json(
                    "Gemini",
                    "POST",
                    "https://generativelanguage.googleapis.com/v1beta/interactions",
                    headers={"x-goog-api-key": key, "Api-Revision": "2026-05-20"},
                    json=payload,
                )
            except RuntimeError as error:
                if (
                    not any((code in str(error) for code in ["HTTP 503", "HTTP 502"]))
                    or attempt == 2
                ):
                    raise
                self.note(f"Gemini temporarily unavailable; retry {attempt + 1}/2.")
                time.sleep(2 * (attempt + 1))

    def planning_input(self, prompt, schema):
        if schema not in (CampaignPlan, AdPlan):
            return prompt
        parts = [{"type": "text", "text": prompt}]
        for asset in self.assets.values():
            path = Path(asset["path"])
            mime = {".png": "image/png", ".webp": "image/webp"}.get(
                path.suffix.lower(), "image/jpeg"
            )
            label = f"Asset {asset['asset_id']}: {asset['use']}; {asset['title']}"
            parts.append({"type": "text", "text": label})
            parts.append(
                {
                    "type": "image",
                    "mime_type": mime,
                    "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                }
            )
        self.note(
            f"Writer receives {len(self.assets)} campaign images, labelled as identity or style references."
        )
        return parts

    def model_json(self, prompt, schema, system=None):
        result = self.gemini_request(
            {
                "model": self.model,
                "input": self.planning_input(prompt, schema),
                "system_instruction": system or BASE_SYSTEM,
                "store": False,
                "response_format": {
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": schema.model_json_schema(),
                },
            }
        )
        text = " ".join(
            (
                b.get("text", "")
                for s in result.get("steps", [])
                if s.get("type") == "model_output"
                for b in s.get("content", [])
                if b.get("type") == "text"
            )
        )
        return schema.model_validate_json(text)

    def search_competitor(self, name):
        return api_json(
            "Tavily",
            "POST",
            "https://api.tavily.com/search",
            headers={
                "Authorization": "Bearer " + (self.keys.get("TAVILY_API_KEY") or "")
            },
            json={
                "query": name + " product features marketing",
                "search_depth": "basic",
                "max_results": 1,
                "include_answer": False,
            },
        )

    def research_competitors(self, names):
        sources = []
        for name in names[:3]:
            cache = self.cache_dir / f"research-{digest(name)}.json"
            try:
                data = (
                    read_json(cache) if cache.exists() else self.search_competitor(name)
                )
                if not cache.exists():
                    save_json(cache, data)
                for hit in data.get("results", [])[:1]:
                    sources.append(
                        {
                            "competitor": name,
                            "title": hit.get("title", ""),
                            "url": hit.get("url", ""),
                            "excerpt": hit.get("content", "")[:1000],
                            "checked_at": datetime.fromtimestamp(
                                cache.stat().st_mtime, ZoneInfo("Asia/Calcutta")
                            ).isoformat(),
                        }
                    )
            except RuntimeError as error:
                sources.append({"competitor": name, "error": str(error)})
        self.note(f"Research complete: {sum(('url' in s for s in sources))} sources")
        return sources

    def review_draft(self, candidate, brief, context):
        if isinstance(candidate, AdPlan):
            plan = CampaignPlan(summary="Selected ad revision", ads=[candidate])
            checked_brief = dict(brief, count=1)
        else:
            plan, checked_brief = (candidate, brief)
        issues = plan_copy_issues(plan, checked_brief)
        if issues:
            return CopyReview(score=0, issues=issues[:6])
        payload = {
            "brief": checked_brief,
            "plan": plan.model_dump(),
            "direction": CREATIVE_DIRECTION,
            "feedback": context.get("feedback", ""),
            "other_ads": context.get("other_ads", []),
        }
        review = self.model_json(
            json.dumps(payload, ensure_ascii=False), CopyReview, REVIEW_SYSTEM
        )
        if factual_review_issues(review):
            review.score = min(review.score, 5)
        return review

    def write_reviewed(self, payload, schema, system, brief):
        draft, fixes = (None, [])
        for attempt in range(3):
            request = dict(payload, required_fixes=fixes, previous_draft=draft)
            candidate = self.model_json(
                json.dumps(request, ensure_ascii=False), schema, system
            )
            candidate = fit_narration_timing(candidate, brief)
            review = self.review_draft(candidate, brief, payload)
            save_json(
                self.folder / f"draft-{attempt + 1}.json",
                {"plan": candidate.model_dump(), "review": review.model_dump()},
            )
            if review.score >= 8:
                return (candidate, dict(review.model_dump(), status="passed"))
            fixes = factual_review_issues(review) + review.issues or [
                "Improve the draft against the rubric."
            ]
            draft = candidate.model_dump()
            self.note(f"Copy review: {review.score}/10; " + "; ".join(fixes[:2]))
        raise RuntimeError(
            "Script needs review after three drafts: " + "; ".join(fixes[:2])
        )

    def make_plan(self, brief, sources, feedback="", previous_plan=None):
        payload = {
            "task": "Propose three videos; human plan approval is required before media.",
            "brief": brief,
            "research": sources,
            "feedback": feedback,
            "previous_plan": previous_plan,
            "creative_direction": CREATIVE_DIRECTION,
            "forbidden_phrases": BANNED_COPY,
            "copy_limits": {
                "headline_words": 6,
                "narration_words_per_second": 2.6,
                "preferred_total_words": [30, 44],
                "total_seconds": 20,
                "max_words_by_seconds": {
                    "2": 4,
                    "3": 7,
                    "4": 10,
                    "5": 13,
                    "6": 15,
                    "7": 18,
                },
            },
        }
        plan, review = self.write_reviewed(payload, CampaignPlan, WRITER_SYSTEM, brief)
        approved_copy = dict(
            plan.model_dump(),
            quality_review=dict(review, scope="Campaign scripts only"),
            prompt_version=PROMPT_VERSION,
        )
        self.note(
            f"Campaign copy passed AI review: {review['score']}/10; awaiting human approval"
        )
        return (approved_copy, self.model + " multimodal writer + script review")

    def revise_ad(self, state, index, feedback):
        payload = {
            "task": "Revise the selected ad using the user feedback.",
            "brief": state["brief"],
            "feedback": feedback,
            "selected_ad": state["plan"]["ads"][index],
            "other_ads": [
                ad for i, ad in enumerate(state["plan"]["ads"]) if i != index
            ],
            "creative_direction": CREATIVE_DIRECTION,
            "forbidden_phrases": BANNED_COPY,
            "copy_limits": {
                "headline_words": 6,
                "words_per_second": 2.6,
                "total_seconds": 20,
            },
        }
        revised, review = self.write_reviewed(
            payload, AdPlan, REVISION_SYSTEM, state["brief"]
        )
        plan = json.loads(json.dumps(state["plan"]))
        plan["ads"][index] = revised.model_dump()
        issues = plan_copy_issues(CampaignPlan.model_validate(plan), state["brief"])
        if issues:
            raise RuntimeError("Revision needs review: " + "; ".join(issues[:2]))
        plan["quality_review"] = dict(review, scope=f"Ad {index + 1} revision")
        plan["prompt_version"] = PROMPT_VERSION
        return plan
