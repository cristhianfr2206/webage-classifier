# Dimension-aware manual review

Milestone 8 Phase 1A adds an additive, human-operated review foundation. It
does not change classification routing, create review cases automatically, or
dispatch any task.

## Evidence

Every new review case is created by an explicit routing decision and receives
one immutable `manual_review_evidence_snapshots` record. The snapshot is a
bounded, canonical JSON payload of already-stored plain-text evidence. It does
not fetch a website and rejects secrets, credentials, cookies, internal URLs,
filesystem paths, and raw HTML-like content. Its SHA-256 checksum and source
run/assessment/browser/feed provenance make later review reproducible.

Legacy review cases remain valid with nullable Phase 1A fields. They are not
backfilled and no evidence is fabricated for them.

## Human decisions

`manual_review_label_decisions` records a reviewer decision against one label
in the case's immutable taxonomy version. Primary decisions must be accepted,
active `content` labels. Scope and security labels cannot be primary. The
database and service both enforce one unsuperseded accepted primary decision.

The reviewer never supplies an arbitrary age value or block decision. For the
three legacy content labels, the service can derive the existing authoritative
category and age policy. Other content labels remain policy-unset until a
future policy rollout defines an authoritative mapping. Unresolved and
non-consumer dispositions intentionally have no age-policy requirement.

## Lifecycle and concurrency

New cases use these dispositions:

- `pending-review`, `in-review`, `resolved`, `unresolved`
- `non-consumer-infrastructure`, `unreachable`, `safety-blocked`, `cancelled`

They are distinct from the legacy display status and permit future routing to
auto-resolution, abstention, infrastructure, or manual review without forcing
every low-confidence result to a person.

Every service mutation requires the caller's expected revision. A stale
revision fails without mutation. Claims are single-owner and idempotent for
the same reviewer. Terminal cases may be locked; locked cases reject claims,
decisions, and ordinary updates. Reopening is a privileged, reason-required,
audited operation.

## Audit

The service creates bounded audit entries for case creation, snapshot capture,
claim, release, decision acceptance/rejection/override, resolution, lock, and
reopen. Entries contain actor, case, old/new revisions, taxonomy version, and
relevant decision identifiers. They never contain raw evidence or secrets.

AI remains disabled in Phase 1A. No AI recommendation table, provider call,
budgeting change, or automated promotion is introduced here.
