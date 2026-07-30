from dataclasses import dataclass

from app.extraction import ExtractedPage


@dataclass(frozen=True)
class CategoryScore:
    slug: str
    confidence: int
    evidence: list[dict[str, object]]


RULES: dict[str, dict[str, int]] = {
    "education": {
        "course": 4,
        "learn": 3,
        "school": 4,
        "university": 4,
        "tutorial": 3,
        "lesson": 3,
    },
    "entertainment": {
        "game": 3,
        "movie": 3,
        "music": 3,
        "stream": 2,
        "video": 2,
        "casino": 7,
        "betting": 7,
    },
    "social": {
        "community": 3,
        "followers": 4,
        "profile": 2,
        "social": 4,
        "chat": 3,
        "forum": 3,
    },
}

ADULT_TERMS = {"adult", "porn", "casino", "betting", "gambling"}
TEEN_TERMS = {"chat", "social", "forum", "dating"}


def classify_page(
    page: ExtractedPage, ruleset: dict[str, dict[str, int]] | None = None
) -> list[CategoryScore]:
    title = page.title.casefold()
    description = page.description.casefold()
    text = page.visible_text.casefold()
    results: list[CategoryScore] = []
    for slug, rules in (ruleset or RULES).items():
        raw_score = 0
        evidence: list[dict[str, object]] = []
        for term, weight in rules.items():
            occurrences = min(text.count(term), 3)
            title_hit = term in title
            description_hit = term in description
            points = (
                occurrences * weight
                + (weight * 2 if title_hit else 0)
                + (weight if description_hit else 0)
            )
            if points:
                raw_score += points
                evidence.append(
                    {
                        "rule": term,
                        "points": points,
                        "title": title_hit,
                        "description": description_hit,
                        "text_occurrences": occurrences,
                    }
                )
        if raw_score:
            results.append(
                CategoryScore(slug=slug, confidence=min(95, 30 + raw_score * 4), evidence=evidence)
            )
    if not results:
        results.append(
            CategoryScore(
                slug="education",
                confidence=20,
                evidence=[{"rule": "fallback", "points": 0}],
            )
        )
    return sorted(results, key=lambda item: (-item.confidence, item.slug))


def recommended_age_policy_name(page: ExtractedPage, category_slug: str) -> str:
    content = f"{page.title} {page.description} {page.visible_text}".casefold()
    if any(term in content for term in ADULT_TERMS):
        return "Adult"
    if category_slug == "social" or any(term in content for term in TEEN_TERMS):
        return "Teen"
    return "Children"
