"""Read-only evaluation of Phase 4 taxonomy rules against stored evidence.

The module never fetches websites and never inserts, updates, or deletes
application records.  It is intentionally separate from the production task
pipeline while the taxonomy rollout remains in evaluation mode.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SessionLocal
from app.models import Category, PilotItem, Website, WebsiteClassification
from app.taxonomy_content import TaxonomyContentEvidence, classify_taxonomy_content


def evaluate_stored_row(
    *, title: str, description: str, text_excerpt: str, existing_category: str | None
) -> dict[str, object]:
    """Evaluate bounded stored fields; no network or persistence is involved."""

    result = classify_taxonomy_content(
        TaxonomyContentEvidence(
            title=title[:500], description=description[:1000], visible_text=text_excerpt[:2000]
        )
    )
    if result.primary is None:
        return {"candidate": None, "secondary": [], "existing_category": existing_category}
    return {
        "candidate": result.primary.label_slug,
        "confidence": result.primary.confidence,
        "evidence": list(result.primary.evidence),
        "secondary": [candidate.label_slug for candidate in result.secondary],
        "existing_category": existing_category,
        "conflicts_existing_label": existing_category is not None,
    }


async def evaluate_pilot_stored_evidence(
    session: AsyncSession, pilot_id: uuid.UUID
) -> dict[str, object]:
    """Return a JSON-serializable offline report for one persisted pilot."""

    rows = (
        await session.execute(
            select(
                Website.domain,
                WebsiteClassification.title,
                WebsiteClassification.description,
                WebsiteClassification.text_excerpt,
                Category.slug,
            )
            .join(PilotItem, PilotItem.website_id == Website.id)
            .join(
                WebsiteClassification,
                WebsiteClassification.run_id == PilotItem.classification_run_id,
            )
            .join(Category, Category.id == WebsiteClassification.category_id)
            .where(PilotItem.pilot_id == pilot_id)
            .order_by(PilotItem.selection_order, WebsiteClassification.id)
        )
    ).all()
    candidates: list[dict[str, object]] = []
    category_counts: Counter[str] = Counter()
    conflicts = 0
    domains: dict[str, tuple[str, str, str, set[str]]] = {}
    for domain, title, description, text_excerpt, existing_category in rows:
        stored = domains.get(domain)
        if stored is None:
            domains[domain] = (title, description, text_excerpt, {existing_category})
        else:
            stored[3].add(existing_category)
    for domain, (title, description, text_excerpt, existing_categories) in domains.items():
        evaluation = evaluate_stored_row(
            title=title,
            description=description,
            text_excerpt=text_excerpt,
            existing_category=None,
        )
        candidate = evaluation["candidate"]
        if not isinstance(candidate, str):
            continue
        category_counts[candidate] += 1
        legacy_conflict = bool(existing_categories)
        conflicts += int(legacy_conflict)
        evaluation.pop("existing_category", None)
        evaluation.pop("conflicts_existing_label", None)
        candidates.append(
            {
                "domain": domain,
                **evaluation,
                "existing_categories": sorted(existing_categories),
                "conflicts_existing_legacy_category": legacy_conflict,
            }
        )
    return {
        "pilot_id": str(pilot_id),
        "stored_classification_rows": len(rows),
        "stored_domain_count": len(domains),
        "candidate_count": len(candidates),
        "category_counts": dict(sorted(category_counts.items())),
        "existing_legacy_category_conflicts": conflicts,
        "candidates": candidates,
        "read_only": True,
    }


async def _main_async(pilot_id: uuid.UUID) -> None:
    async with SessionLocal() as session:
        report = await evaluate_pilot_stored_evidence(session, pilot_id)
    print(json.dumps(report, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot-id", required=True, type=uuid.UUID)
    args = parser.parse_args()
    asyncio.run(_main_async(args.pilot_id))


if __name__ == "__main__":
    main()
