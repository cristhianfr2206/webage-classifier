"""Offline, read-only UT1-style domain category lookup proof of concept.

This module is deliberately not imported by the production classification
pipeline.  It provides a small CSV-backed lookup that can be evaluated before
HTTP inspection in a future integration.
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from app.normalization import NormalizationError, normalize_domain, registrable_domain


@dataclass(frozen=True)
class LookupResult:
    matched: bool
    source_category: str | None
    normalized_domain: str
    match_type: str


def load_ut1_lookup(path: Path) -> dict[str, str]:
    """Load normalized, valid domain/category rows from a tiny UT1 CSV fixture.

    Invalid rows are ignored because unknown is safer than accepting an
    unnormalized or non-registrable domain.  The production normalizer is used
    for both fixture rows and query domains.
    """

    lookup: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            raw_domain = (row.get("domain") or "").strip()
            category = (row.get("category") or "").strip()
            if not raw_domain or not category:
                continue
            try:
                domain = normalize_domain(raw_domain)
            except NormalizationError:
                continue
            lookup[domain] = category
    return lookup


def lookup_domain(domain: str, lookup: dict[str, str]) -> LookupResult:
    try:
        normalized = normalize_domain(domain)
    except NormalizationError:
        return LookupResult(False, None, "", "unknown")
    if normalized in lookup:
        return LookupResult(True, lookup[normalized], normalized, "exact")

    registrable = registrable_domain(normalized)
    if registrable in lookup:
        return LookupResult(True, lookup[registrable], normalized, "parent-domain")
    return LookupResult(False, None, normalized, "unknown")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("domain")
    parser.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = lookup_domain(args.domain, load_ut1_lookup(args.fixture))
    except NormalizationError:
        result = LookupResult(False, None, "", "unknown")
    print(json.dumps(asdict(result), sort_keys=True))


if __name__ == "__main__":
    main()
