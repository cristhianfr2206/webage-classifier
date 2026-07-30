import uuid
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ClassifierVersion, RulesetVersion


async def active_classifier_version_id(db: AsyncSession) -> uuid.UUID | None:
    return cast(
        uuid.UUID | None,
        await db.scalar(
            select(ClassifierVersion.id).where(ClassifierVersion.is_active.is_(True)).limit(1)
        ),
    )


async def rules_for_classifier_version(
    db: AsyncSession, version_id: uuid.UUID | None
) -> dict[str, dict[str, int]] | None:
    if version_id is None:
        return None
    version = await db.get(ClassifierVersion, version_id)
    ruleset = await db.get(RulesetVersion, version.ruleset_version_id) if version else None
    if ruleset is None:
        return None
    parsed: dict[str, dict[str, int]] = {}
    for category, raw_rules in ruleset.weights.items():
        if not isinstance(raw_rules, dict):
            continue
        parsed[str(category)] = {
            str(term): weight
            for term, weight in raw_rules.items()
            if isinstance(weight, int) and 0 <= weight <= 100
        }
    return parsed or None
