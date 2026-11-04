"""Small provider requests with credential-safe errors and diagnostic output."""

from datetime import datetime, timezone
import re
import requests

PROVIDERS = {
    "Gemini": "GEMINI_API_KEY",
    "Tavily": "TAVILY_API_KEY",
    "Coverr": "COVERR_API_KEY",
    "ElevenLabs": "ELEVENLABS_API_KEY",
}


def redact(value, keys):
    private = {
        "api_key",
        "apikey",
        "authorization",
        "xi-api-key",
        "token",
        "access_token",
        "refresh_token",
        "client_secret",
        "email",
    }
    if isinstance(value, dict):
        return {
            k: "[hidden]" if k.lower() in private else redact(v, keys)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v, keys) for v in value]
    if isinstance(value, str):
        for key, secret in keys.items():
            if "API_KEY" in key and secret:
                value = value.replace(secret, "[API key hidden]")
        value = re.sub(r"eyJ[\w-]+\.[\w-]+\.[\w-]+", "[signed token hidden]", value)
        value = re.sub(
            r"([?&](?:token|api_key|key|signature)=)[^&\s]+",
            r"\1[hidden]",
            value,
            flags=re.I,
        )
    return value


def api_json(service, method, url, **kwargs):
    try:
        response = requests.request(
            method, url, timeout=(8, 60), allow_redirects=False, **kwargs
        )
    except requests.RequestException as error:
        raise RuntimeError(
            f"{service}: connection failed ({type(error).__name__})."
        ) from None
    if not response.ok:
        # Do not echo request URLs, headers, or credential-bearing response fields.
        hints = {
            401: "Check the API key and its endpoint permissions.",
            403: "This key or account cannot access this operation.",
            429: "Quota or rate limit reached. Retry later.",
            503: "The model is busy. Retry later or edit the saved draft.",
        }
        raise RuntimeError(
            f"{service}: HTTP {response.status_code}. "
            + hints.get(response.status_code, "Request rejected.")
        )
    try:
        return response.json()
    except ValueError:
        raise RuntimeError(f"{service}: response was not valid JSON.") from None


def check_provider(service, keys, model="gemini-2.5-flash"):
    """No TTS or video generation; one small text/search/list request per click."""
    name = PROVIDERS[service]
    if not keys.get(name):
        return dict(
            service=service,
            status=None,
            ok=False,
            body={"message": name + " is missing."},
        )
    common = dict(timeout=(8, 60), allow_redirects=False)
    try:
        if service == "Gemini":
            response = requests.post(
                "https://generativelanguage.googleapis.com/v1beta/interactions",
                headers={"x-goog-api-key": keys[name], "Api-Revision": "2026-05-20"},
                json={
                    "model": model,
                    "store": False,
                    "input": "Reply with exactly: API check successful.",
                },
                **common,
            )
        elif service == "Tavily":
            response = requests.post(
                "https://api.tavily.com/search",
                headers={"Authorization": "Bearer " + keys[name]},
                json={
                    "query": "product marketing photography",
                    "search_depth": "basic",
                    "max_results": 1,
                    "include_usage": True,
                },
                **common,
            )
        elif service == "Coverr":
            response = requests.get(
                "https://api.coverr.co/videos",
                headers={"Authorization": "Bearer " + keys[name]},
                params={"query": "nature", "page_size": 1, "urls": "false"},
                **common,
            )
        else:
            response = requests.get(
                "https://api.elevenlabs.io/v2/voices",
                headers={"xi-api-key": keys[name]},
                params={"page_size": 1},
                **common,
            )
        try:
            body = response.json()
        except ValueError:
            body = response.text[:5000]
        result = dict(
            service=service,
            status=response.status_code,
            ok=response.ok,
            body=redact(body, keys),
        )
    except requests.RequestException as error:
        result = dict(
            service=service,
            status=None,
            ok=False,
            body={"message": type(error).__name__},
        )
    return dict(
        result, checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
