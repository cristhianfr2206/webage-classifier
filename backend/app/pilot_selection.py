import uuid
from dataclasses import dataclass

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ClassificationRun, RunStatus, Website


@dataclass(frozen=True)
class PilotCandidate:
    website_id: uuid.UUID
    domain: str
    original_tranco_rank: int


async def select_pilot_candidates(
    db: AsyncSession, *, rank_start: int, size: int
) -> list[PilotCandidate]:
    """Return the first eligible website per original rank in stable order."""
    ranked = (
        select(
            Website.id.label("website_id"),
            Website.domain,
            Website.tranco_rank.label("original_tranco_rank"),
            func.row_number()
            .over(
                partition_by=Website.tranco_rank,
                order_by=(Website.id.asc(),),
            )
            .label("rank_position"),
        )
        .where(
            Website.pilot_eligible.is_(True),
            Website.tranco_rank.is_not(None),
            Website.tranco_rank >= rank_start,
            ~exists(
                select(ClassificationRun.id).where(
                    ClassificationRun.website_id == Website.id,
                    ClassificationRun.status.in_(
                        (RunStatus.PENDING, RunStatus.RETRYING, RunStatus.RUNNING)
                    ),
                )
            ),
        )
        .subquery()
    )
    rows = (
        await db.execute(
            select(
                ranked.c.website_id,
                ranked.c.domain,
                ranked.c.original_tranco_rank,
            )
            .where(ranked.c.rank_position == 1)
            .order_by(
                ranked.c.original_tranco_rank.asc(),
                ranked.c.website_id.asc(),
            )
            .limit(size)
        )
    ).all()
    return [
        PilotCandidate(
            website_id=row.website_id,
            domain=row.domain,
            original_tranco_rank=int(row.original_tranco_rank),
        )
        for row in rows
    ]
