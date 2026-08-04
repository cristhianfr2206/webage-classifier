"""Read-only evaluation of versioned UT1 mappings against completed pilots.

Only existing database domains and local UT1 extracted lists are read.  The
evaluator does not request websites, enqueue tasks, or persist assessments,
manual-review cases, classifications, or feed results.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SessionLocal
from app.infrastructure_detection import detect_infrastructure
from app.models import PilotItem, PilotRun, PilotStatus, Website
from app.normalization import NormalizationError, normalize_domain, registrable_domain
from app.taxonomy_feeds import FeedSignal, evaluate_feed_signals

UT1_EVALUATION_CATEGORIES = (
    "education",
    "social_networks",
    "audio-video",
    "shopping",
    "games",
    "adult",
    "gambling",
)


def _domain_file(root: Path, category: str) -> Path:
    return root / category / category / "domains"


def local_ut1_signals(
    domains: tuple[str, ...], root: Path
) -> tuple[dict[str, tuple[FeedSignal, ...]], tuple[str, ...]]:
    """Stream local UT1 lists and retain only normalized pilot-domain matches."""

    normalized_domains = {normalize_domain(domain) for domain in domains}
    by_registrable: dict[str, set[str]] = defaultdict(set)
    for domain in normalized_domains:
        by_registrable[registrable_domain(domain)].add(domain)
    signals: dict[str, list[FeedSignal]] = defaultdict(list)
    unavailable: list[str] = []
    for category in UT1_EVALUATION_CATEGORIES:
        domain_file = _domain_file(root, category)
        if not domain_file.is_file():
            unavailable.append(category)
            continue
        timestamp = datetime.fromtimestamp(domain_file.stat().st_mtime, UTC)
        with domain_file.open(encoding="utf-8", errors="replace") as handle:
            for raw_line in handle:
                raw_domain = raw_line.strip()
                if not raw_domain:
                    continue
                try:
                    feed_domain = normalize_domain(raw_domain)
                except NormalizationError:
                    continue
                if feed_domain in normalized_domains:
                    signals[feed_domain].append(FeedSignal("ut1", category, "exact", timestamp))
                for domain in by_registrable.get(feed_domain, ()):
                    if domain != feed_domain:
                        signals[domain].append(
                            FeedSignal("ut1", category, "parent-domain", timestamp)
                        )
    return {domain: tuple(value) for domain, value in signals.items()}, tuple(unavailable)


def evaluate_domains(domains: tuple[str, ...], root: Path) -> dict[str, object]:
    signals_by_domain, unavailable = local_ut1_signals(domains, root)
    by_source_category: Counter[str] = Counter()
    by_label: Counter[str] = Counter()
    by_handling_mode: Counter[str] = Counter()
    conflicts: list[str] = []
    manual_review: list[str] = []
    final_candidates: list[str] = []
    multi_signal: list[str] = []
    infrastructure_suppressed: list[str] = []
    matched_domains: set[str] = set()
    for domain in domains:
        signals = signals_by_domain.get(domain, ())
        if not signals:
            continue
        matched_domains.add(domain)
        infrastructure = detect_infrastructure(domain)
        excluded = infrastructure is not None and infrastructure.confidence_tier == "safe_exclude"
        evaluation = evaluate_feed_signals(signals, infrastructure_excluded=excluded)
        if excluded:
            infrastructure_suppressed.append(domain)
        if len(evaluation.mappings) > 1:
            multi_signal.append(domain)
        if evaluation.conflict:
            conflicts.append(domain)
        if evaluation.requires_manual_review:
            manual_review.append(domain)
        if evaluation.potential_final_candidate is not None:
            final_candidates.append(domain)
        for mapping in evaluation.mappings:
            by_source_category[mapping.original_source_category] += 1
            by_handling_mode[mapping.handling_mode.value] += 1
            if mapping.target_label_slug is not None:
                by_label[mapping.target_label_slug] += 1
    return {
        "domain_count": len(domains),
        "matched_domain_count": len(matched_domains),
        "eligible_matched_domain_count": len(matched_domains) - len(infrastructure_suppressed),
        "unmatched_domain_count": len(domains) - len(matched_domains),
        "matches_by_source_category": dict(sorted(by_source_category.items())),
        "mapped_taxonomy_labels": dict(sorted(by_label.items())),
        "handling_modes": dict(sorted(by_handling_mode.items())),
        "conflicts": sorted(conflicts),
        "unsupported_mappings": [],
        "unavailable_local_categories": list(unavailable),
        "domains_with_multiple_feed_signals": sorted(multi_signal),
        "potential_final_candidates": sorted(final_candidates),
        "potential_manual_review_cases": sorted(manual_review),
        "infrastructure_suppressed": sorted(infrastructure_suppressed),
        "estimated_coverage_without_enforcement": len(matched_domains)
        - len(infrastructure_suppressed),
        "read_only": True,
    }


async def completed_pilot_domains(session: AsyncSession) -> dict[str, tuple[str, ...]]:
    rows = (
        await session.execute(
            select(PilotRun.id, Website.domain)
            .join(PilotItem, PilotItem.pilot_id == PilotRun.id)
            .join(Website, Website.id == PilotItem.website_id)
            .where(PilotRun.status == PilotStatus.COMPLETED)
            .order_by(PilotRun.created_at, PilotItem.selection_order)
        )
    ).all()
    grouped: dict[str, list[str]] = defaultdict(list)
    for pilot_id, domain in rows:
        grouped[str(pilot_id)].append(domain)
    return {pilot_id: tuple(domains) for pilot_id, domains in grouped.items()}


async def _main_async(root: Path, pilot_id: uuid.UUID | None) -> None:
    async with SessionLocal() as session:
        pilot_domains = await completed_pilot_domains(session)
    if pilot_id is not None:
        selected = {str(pilot_id): pilot_domains.get(str(pilot_id), ())}
    else:
        selected = pilot_domains
    report = {
        "source": "ut1",
        "root": str(root),
        "pilots": {pilot: evaluate_domains(domains, root) for pilot, domains in selected.items()},
        "read_only": True,
    }
    print(json.dumps(report, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ut1-root", type=Path, required=True)
    parser.add_argument("--pilot-id", type=uuid.UUID)
    args = parser.parse_args()
    asyncio.run(_main_async(args.ut1_root, args.pilot_id))


if __name__ == "__main__":
    main()
