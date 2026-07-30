import json
import re

from app.ai_provider import detect_prompt_injection, payload_hash
from app.config import Settings


def _clean(value: str, limit: int) -> str:
    return re.sub(r"\s+", " ", value).strip()[:limit]


def build_ai_payload(
    settings: Settings,
    *,
    domain: str,
    title: str = "",
    description: str = "",
    headings: list[str] | None = None,
    buttons: list[str] | None = None,
    links: list[str] | None = None,
    static_text: str = "",
    rendered_text: str = "",
    features: list[str] | None = None,
    rule_scores: dict[str, int] | None = None,
    language: str = "",
) -> tuple[dict[str, object], str, bool]:
    values = [title, description, static_text, rendered_text, *(headings or []), *(buttons or [])]
    payload: dict[str, object] = {
        "domain": _clean(domain, 253),
        "title": _clean(title, 500),
        "description": _clean(description, 1000),
        "headings": [_clean(item, 300) for item in (headings or [])[:50]],
        "buttons": [_clean(item, 200) for item in (buttons or [])[:50]],
        "links": [_clean(item, 200) for item in (links or [])[:100]],
        "static_text": _clean(static_text, 6000),
        "rendered_text": _clean(rendered_text, 8000),
        "features": [_clean(item, 100) for item in (features or [])[:100]],
        "rule_scores": dict(list((rule_scores or {}).items())[:50]),
        "language": _clean(language, 20),
    }
    while len(json.dumps(payload, separators=(",", ":"))) > settings.ai_max_input_characters:
        if payload["rendered_text"]:
            payload["rendered_text"] = str(payload["rendered_text"])[:-500]
        elif payload["static_text"]:
            payload["static_text"] = str(payload["static_text"])[:-500]
        elif payload["headings"]:
            assert isinstance(payload["headings"], list)
            payload["headings"].pop()
        elif payload["links"]:
            assert isinstance(payload["links"], list)
            payload["links"].pop()
        elif payload["buttons"]:
            assert isinstance(payload["buttons"], list)
            payload["buttons"].pop()
        elif payload["features"]:
            assert isinstance(payload["features"], list)
            payload["features"].pop()
        else:
            break
    return payload, payload_hash(payload), detect_prompt_injection(values)
