"""Multimodal Gemini planner and Tavily research, independent of the UI."""

import base64
import json
import time
from io import BytesIO
from PIL import Image
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from .models import *
from .prompts import *
from .validation import *
from .storage import read_json, save_json, digest
from .connections import api_json
from .control import DEFAULT_LIMITS


class Planner:
    def __init__(self, folder, keys, assets, note, model="gemini-2.5-flash"):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.cache_dir = self.folder / "research_cache"
        self.cache_dir.mkdir(exist_ok=True)
        self.keys, self.assets, self.note, self.model = keys, assets, note, model
        self.control, self.cid = None, self.folder.name

    def gemini_request(self, payload):
        key = self.keys.get("GEMINI_API_KEY") or ""
        if not key:
            raise RuntimeError("Gemini API key missing")
        return api_json(
            "Gemini",
            "POST",
            "https://generativelanguage.googleapis.com/v1beta/interactions",
            headers={"x-goog-api-key": key, "Api-Revision": "2026-05-20"},
            json=payload,
        )

    def planning_input(self, prompt, schema):
        if schema not in (CampaignPlan, AdPlan):
            return prompt
        parts = [{"type": "text", "text": prompt}]
        manifest = self.folder / "image-analysis.json"
        hashes = {key: asset["sha256"] for key, asset in self.assets.items()}
        if (
            manifest.exists()
            and read_json(manifest).get("hashes") == hashes
            and read_json(manifest).get("confirmed")
        ):
            parts.append({"type": "text", "text": json.dumps(read_json(manifest))})
            return parts
        descriptions = []
        for asset in self.assets.values():
            path = Path(asset["path"])
            mime = {".png": "image/png", ".webp": "image/webp"}.get(
                path.suffix.lower(), "image/jpeg"
            )
            label = f"Asset {asset['asset_id']}: {asset['use']}; {asset['title']}"
            parts.append({"type": "text", "text": label})
            with Image.open(path) as photo:
                photo.thumbnail((768, 768))
                buffer = BytesIO()
                photo.convert("RGB").save(buffer, format="JPEG", quality=80)
                descriptions.append(
                    dict(
                        asset_id=asset["asset_id"],
                        use=asset["use"],
                        title=asset["title"],
                        size=asset.get("size"),
                    )
                )
            parts.append(
                {
                    "type": "image",
                    "mime_type": "image/jpeg",
                    "data": base64.b64encode(buffer.getvalue()).decode("ascii"),
                }
            )
        self.note(
            f"Writer receives {len(self.assets)} campaign images, labelled as identity or style references."
        )
        # This stores visual context metadata, not a claim that pixels were semantically verified.
        save_json(
            manifest,
            dict(
                hashes=hashes,
                assets=descriptions,
                confirmed=False,
                note="Original images sent on initial planning only; revisions use this context and the saved plan.",
            ),
        )
        return parts

    def model_json(self, prompt, schema, system=None):
        data = json.loads(prompt)
        brief = data.get("brief", {})
        self.model = brief.get("planning_model") or self.model
        cache = (
            self.folder
            / "model_cache"
            / (digest([self.model, prompt, schema.__name__, system]) + ".json")
        )
        if cache.exists():
            return schema.model_validate(read_json(cache)["parsed"])
        payload_input = self.planning_input(prompt, schema)
        if isinstance(payload_input, list):
            for analysis in brief.get("input_analysis", []):
                for frame in analysis.get("frames", [])[:4]:
                    if Path(frame).is_file():
                        payload_input.append(
                            {
                                "type": "image",
                                "mime_type": "image/jpeg",
                                "data": base64.b64encode(
                                    Path(frame).read_bytes()
                                ).decode(),
                            }
                        )
        expected = (
            len(prompt) // 3
            + 8192
            + 2048
            * (
                sum(part.get("type") == "image" for part in payload_input)
                if isinstance(payload_input, list)
                else 0
            )
        )
        key = "model:" + digest([self.model, prompt, schema.__name__, system])
        if self.control:
            self.control.task_start(self.cid, key, "planning", max_attempts=2)
            attempt = self.control.task(self.cid, key)["attempts"]
            allowance_key = key + ":" + str(attempt)
            from .usage import charge

            try:
                self.control.reserve(
                    self.cid,
                    allowance_key,
                    charge({"model_tokens": expected}, brief.get("usage_rates")),
                    brief.get("limits", DEFAULT_LIMITS),
                    "planning",
                )
            except ValueError as error:
                self.control.task_update(self.cid, key, "paused", error=str(error))
                raise
            self.control.task_update(self.cid, key, "submitting")
        try:
            result = self.gemini_request(
                {
                    "model": self.model,
                    "input": payload_input,
                    "system_instruction": system or BASE_SYSTEM,
                    "store": False,
                    "generation_config": {"max_output_tokens": 8192},
                    "response_format": {
                        "type": "text",
                        "mime_type": "application/json",
                        "schema": schema.model_json_schema(),
                    },
                }
            )
        except Exception as error:
            if self.control:
                self.control.task_update(self.cid, key, "unknown", error=str(error))
            raise
        text = " ".join(
            (
                b.get("text", "")
                for s in result.get("steps", [])
                if s.get("type") == "model_output"
                for b in s.get("content", [])
                if b.get("type") == "text"
            )
        )
        parsed = schema.model_validate_json(text)
        manifest = self.folder / "image-analysis.json"
        if manifest.exists() and schema in [CampaignPlan, AdPlan]:
            analysis = read_json(manifest)
            analysis["confirmed"] = True
            analysis["visual_context"] = [
                {
                    "headline": scene.headline,
                    "description": scene.visual_direction,
                    "assets": scene.asset_ids,
                }
                for ad in (parsed.ads if isinstance(parsed, CampaignPlan) else [parsed])
                for scene in ad.scenes
            ]
            save_json(manifest, analysis)
        usage = result.get("usage", result.get("usage_metadata", {}))
        total = usage.get("total_tokens") or usage.get("totalTokens")
        if total is None:
            total = (
                usage.get("total_input_tokens", 0) + usage.get("total_output_tokens", 0)
            ) or expected
        if self.control:
            self.control.settle(
                self.cid,
                allowance_key,
                charge({"model_tokens": int(total)}, brief.get("usage_rates")),
            )
            self.control.task_update(
                self.cid, key, "complete", output=dict(cache=str(cache), usage=usage)
            )
        save_json(
            cache,
            dict(
                parsed=parsed.model_dump(), usage=usage, estimated_usage=not bool(usage)
            ),
        )
        if total > expected:
            raise ValueError(
                "Actual token usage exceeded the reservation; review the campaign allowance before continuing."
            )
        return parsed

    def check_model_access(self, model):
        import re

        if not re.fullmatch(r"gemini-[A-Za-z0-9._-]+", model):
            raise ValueError("Use a Gemini model ID, not a URL.")
        path = self.cache_dir / (
            "model-access-"
            + digest([model, self.keys.get("GEMINI_API_KEY", "")])
            + ".json"
        )
        if path.exists() and time.time() - path.stat().st_mtime < 3600:
            return read_json(path)
        result = api_json(
            "Gemini model access",
            "GET",
            "https://generativelanguage.googleapis.com/v1beta/models/" + model,
            headers={"x-goog-api-key": self.keys.get("GEMINI_API_KEY", "")},
        )
        save_json(path, result)
        return result

    def search_competitor(self, name):
        return api_json(
            "Tavily",
            "POST",
            "https://api.tavily.com/search",
            headers={
                "Authorization": "Bearer " + (self.keys.get("TAVILY_API_KEY") or "")
            },
            json={
                "query": name
                + " official product positioning creative advertising hooks social video call to action",
                "search_depth": "basic",
                "max_results": 4,
                "include_answer": False,
            },
        )

    def research_competitors(self, names, refresh=False, ttl_seconds=86400):
        sources = []
        seen = set()
        for name in names[:3]:
            cache = self.cache_dir / f"research-{digest(name)}.json"
            try:
                fresh = (
                    cache.exists()
                    and time.time() - cache.stat().st_mtime < ttl_seconds
                    and not refresh
                )
                if fresh:
                    data = read_json(cache)
                else:
                    data = self.search_competitor(name)
                    save_json(cache, data)
                for hit in data.get("results", [])[:4]:
                    url = hit.get("url", "").split("#")[0].rstrip("/")
                    if not url or url in seen:
                        continue
                    seen.add(url)
                    kind = (
                        "organic_post"
                        if any(
                            domain in url
                            for domain in [
                                "instagram.com/",
                                "tiktok.com/",
                                "youtube.com/",
                                "facebook.com/",
                            ]
                        )
                        else "product_page"
                        if any(word in url for word in ["/product", "/shop", "/item"])
                        else "public_page"
                    )
                    excerpt = hit.get("content", "")
                    import re

                    ctas = re.findall(
                        r"\b(?:shop now|learn more|explore[^.!?\n]{0,60}|discover[^.!?\n]{0,60}|buy now)\b",
                        excerpt,
                        re.I,
                    )
                    sources.append(
                        {
                            "competitor": name,
                            "title": hit.get("title", ""),
                            "url": url,
                            "reference_type": kind,
                            "positioning_excerpt": excerpt[:300],
                            "creative_hook_observation": hit.get("title", "")
                            + " — "
                            + excerpt[:160],
                            "observed_format": kind,
                            "calls_to_action_observed": ctas[:3],
                            "publication_date": hit.get("published_date"),
                            "active_ad_status": "unverified",
                            "performance": "not available",
                            "observation": "Accessible source excerpt; positioning/hooks/formats/CTA require review. "
                            + hit.get("content", "")[:800],
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
            if any("Fact review required" in issue for issue in review.issues):
                if isinstance(candidate, CampaignPlan):
                    plan = candidate.model_dump()
                else:
                    plan = json.loads(json.dumps(payload["previous_plan"]))
                    plan["ads"][payload["revision_index"]] = candidate.model_dump()
                save_json(
                    self.folder / "pending_facts.json",
                    dict(
                        plan=plan,
                        brief_hash=digest(brief),
                        previous_plan_hash=digest(payload.get("previous_plan")),
                        issues=plan_copy_issues(
                            CampaignPlan.model_validate(plan), brief
                        ),
                    ),
                )
                raise ValueError(
                    "Fact review required. Review the proposed statements in the plan screen before approving media."
                )
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
        self.check_model_access(brief.get("planning_model") or self.model)
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
            "previous_plan": state["plan"],
            "revision_index": index,
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
        from .timeline import ad_key

        if ad_key(plan["ads"][index], state["brief"]) == ad_key(
            state["plan"]["ads"][index], state["brief"]
        ):
            raise ValueError(
                "The proposed revision makes no executable media change. Use a supported timeline edit or clarify the script change."
            )
        issues = plan_copy_issues(CampaignPlan.model_validate(plan), state["brief"])
        if issues:
            raise RuntimeError("Revision needs review: " + "; ".join(issues[:2]))
        plan["quality_review"] = dict(review, scope=f"Ad {index + 1} revision")
        plan["prompt_version"] = PROMPT_VERSION
        return plan
