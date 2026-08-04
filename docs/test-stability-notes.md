# Test stability notes

## Pilot dry-run UUID sentinel observation

During Phase 4 validation, one initial full-suite invocation failed in
`test_dry_run_dispatches_no_jobs_and_is_reproducible` while committing the
100-row `PilotItem` batch to SQLite. SQLAlchemy's insert-many-values sentinel
path attempted to coerce `float('inf')` through the UUID result processor,
raising `AttributeError: 'float' object has no attribute 'replace'`.

The same test then passed in isolation and the complete suite passed in a clean
subsequent run. No deterministic application-level cause has been established:
the pilot candidate IDs were UUIDs, the test did not assign a float to a UUID
field, and no production PostgreSQL path was involved. This appears limited to
the SQLite test dialect's bulk-insert/result-ordering machinery or prior test
state, but that is not proven.

No pilot code was changed. A correction is deferred until the failure can be
reproduced deterministically with a narrow regression test. The test remains in
the complete suite and should be rerun in isolation plus a clean full-suite run
when this symptom appears.
