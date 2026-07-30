# Controlled pilot operations

Pilots accept exactly 100, 1,000, or 10,000 Tranco-ranked domains. They never start
automatically, and one million is not an allowed pilot size.

Dry-run mode dispatches no jobs. It reproducibly estimates pending/static work,
browser fallbacks, AI calls, queue impact, runtime, AI cost, and storage growth, then
hashes the canonical estimate for export and comparison.

Live orchestration is persistent, version-pinned, capacity-bounded, pausable,
resumable, and cancellable. Existing duplicate prevention, locks, SSRF protections,
browser isolation, AI limits, and prior-classification preservation remain active.
