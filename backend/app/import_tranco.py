import argparse
import asyncio
import csv
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SessionLocal, engine
from app.models import TrancoImport, Website
from app.normalization import NormalizationError, normalize_domain, registrable_domain


async def import_rows(
    db: AsyncSession,
    rows: Iterable[list[str]],
    source_name: str,
    limit: int | None = None,
    batch_size: int = 2000,
) -> TrancoImport:
    record = TrancoImport(source_name=source_name, requested_limit=limit)
    db.add(record)
    await db.flush()
    batch: dict[str, dict[str, object]] = {}
    accepted = 0
    skipped = 0

    async def flush() -> None:
        nonlocal batch
        if not batch:
            return
        statement = insert(Website).values(list(batch.values()))
        statement = statement.on_conflict_do_update(
            index_elements=[Website.domain],
            set_={
                "tranco_rank": statement.excluded.tranco_rank,
                "updated_at": datetime.now(UTC),
            },
        )
        await db.execute(statement)
        await db.commit()
        batch = {}

    for row in rows:
        if limit is not None and accepted >= limit:
            break
        if len(row) < 2:
            skipped += 1
            continue
        try:
            rank = int(row[0])
            domain = normalize_domain(row[1])
        except (ValueError, NormalizationError):
            skipped += 1
            continue
        batch[domain] = {
            "domain": domain,
            "registrable_domain": registrable_domain(domain),
            "canonical_url": f"https://{domain}/",
            "tranco_rank": rank,
        }
        accepted += 1
        if len(batch) >= batch_size:
            await flush()
    await flush()
    record.imported_count = accepted
    record.skipped_count = skipped
    record.completed_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(record)
    return record


async def run(path: Path, limit: int | None) -> None:
    with path.open("r", encoding="utf-8", newline="") as source:
        async with SessionLocal() as db:
            result = await import_rows(db, csv.reader(source), path.name, limit)
            print(
                f"import_id={result.id} imported={result.imported_count} "
                f"skipped={result.skipped_count}"
            )
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Stream a Tranco CSV into PostgreSQL")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    asyncio.run(run(args.csv_path, args.limit))


if __name__ == "__main__":
    main()
