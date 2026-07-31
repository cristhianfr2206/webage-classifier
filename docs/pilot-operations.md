# Controlled pilot operations

Pilots accept exactly 100, 1,000, or 10,000 Tranco-ranked domains. They never start
automatically, and one million is not an allowed pilot size.

`rank_start` is the minimum original Tranco rank to search, not the beginning of a
contiguous rank window. At creation time the backend selects the first requested
number of pilot-eligible websites, ordered by original rank and stable website ID.
Only one website is selected for a duplicated rank. Rejected Public Suffix List
entries and records not imported through the trusted Tranco path remain ineligible.
Creation fails without writing a partial pilot when fewer than the requested number
are available.

The exact set is persisted in `pilot_items` with immutable selection order and
original rank. Dry-run estimates, execution batches, pause/resume, progress, and
exports read that membership rather than repeating the selection query. Original
Tranco ranks are never renumbered and missing ranks never receive placeholders.

Dry-run mode dispatches no jobs. It reproducibly estimates pending/static work,
browser fallbacks, AI calls, queue impact, runtime, AI cost, and storage growth, then
hashes the canonical estimate for export and comparison.

Live orchestration is persistent, version-pinned, capacity-bounded, pausable,
resumable, and cancellable. Existing duplicate prevention, locks, SSRF protections,
browser isolation, AI limits, and prior-classification preservation remain active.
