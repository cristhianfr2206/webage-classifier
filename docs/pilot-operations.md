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

Pausing prevents new persisted members from being dispatched. Maintenance
reconciliation continues to record terminal outcomes for work already in flight;
resuming starts with the next undispatched membership and never recalculates the set.

Static failures retain normalized reason codes. Transient DNS failures and timeouts
use bounded resolver retries; permanent no-address results are not repeatedly
retried. Browser recovery is limited to potentially user-facing HTTP failures such
as blocking, bounded redirect failures, fetch failures, or insufficient evidence.
Unsafe destinations, DNS failures, oversized or non-HTML responses, and domains that
appear to be infrastructure, CDN, or DNS endpoints are not sent to Playwright.

Browser workers intentionally default to one concurrent context. Pilot capacity
limits total classification work but does not override the separately bounded
browser-worker concurrency.
