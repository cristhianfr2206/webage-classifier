# Multidimensional taxonomy model

Phases 1 through 5 introduce storage and historical projections only. They do not alter classification, feeds,
infrastructure handling, browser handling, AI, APIs, pilots, seeded categories,
or age-policy behavior.

## Dimensions

- **Content:** one optional primary label and zero or more secondary labels.
- **Security:** independent labels such as phishing or malware. They cannot be a
  primary content label.
- **Scope:** independent service-role labels such as CDN or cloud hosting. They
  cannot be a primary content label.
- **Policy decision:** an optional, derived snapshot. It is absent for unresolved,
  non-consumer, unreachable, failed, and cancelled outcomes.
- **Terminal disposition:** records the assessment result independently from any
  content label.

## Versioning and immutability

`taxonomy_versions` has a draft/published/retired lifecycle. A published version
and its labels are immutable at the database layer. Corrections must create a new
draft version with `parent_version_id` pointing to the corrected version, then
publish that successor after review.

No taxonomy labels are seeded in Phase 1, and no legacy classification rows are
backfilled. Existing `categories`, `age_policies`, and `website_classifications`
continue to be authoritative for current production behavior.

Phase 2 publishes `initial-legacy-v1` with only `education-reference`,
`entertainment-streaming`, and `social-networking`. It projects completed legacy
classification runs into assessments without changing or deleting legacy rows.

Phase 3 publishes `initial-scope-v2`, a successor to the legacy snapshot. It
preserves those content labels and adds only scope labels already emitted by
completed safe infrastructure exclusions. Infrastructure assessments have the
`non-consumer-infrastructure` disposition, no primary content label, and no
age-policy decision.

Phase 4 publishes `initial-content-v3`, a successor to the scope snapshot. It
preserves every Phase 3 label and adds `news-media`, `shopping-ecommerce`,
`gaming`, and `technology-software`. The accompanying deterministic rules are
internal/offline only: they require independent evidence families and are not
called by `classification_service`, browser processing, or an API endpoint.
Their future-facing policy defaults are general for news, shopping, and
technology/software, and teen (minimum age 13) for gaming. They do not write a
policy decision, change a legacy category, or recommend blocking in Phase 4.

UT1 `shopping` and `games` are mapped only to low-confidence supporting labels
for offline evaluation. They cannot short-circuit HTTP/browser processing or
produce a final taxonomy assessment. Existing UT1 `social_networks` and
`audio-video` behavior remains unchanged. Infrastructure exclusions always take
precedence over these prospective content candidates.

Phase 5 publishes `initial-feeds-v4`, preserving all v3 labels and adding the
inactive, review-gated content-risk labels `adult-content` and `gambling`.
`feed_label_mappings` is version-scoped, immutable once its taxonomy version is
published, and stores source/category, target label, handling mode, confidence,
review requirement, mapping version, enabled state, and creation time. It has
no age-policy, security, or enforcement fields.

The initial `ut1-initial-v1` mapping set is evaluation-only: education is a
potential final candidate; social, audio-video, shopping, and games are
supporting evidence; adult and gambling are high-risk evidence requiring
review. No mapping is wired into the live UT1 lookup. Exact infrastructure
exclusions suppress all prospective feed results; conflicts and high-risk
signals require review. Missing or unsupported mappings remain unknown.

## Assessment invariants

- A classification run has at most one normalized assessment.
- A taxonomy version/slug pair is unique.
- An assessment may have at most one primary label.
- A primary label must be a content label from the assessment taxonomy version.
- Scope and security labels may only be secondary/evidence labels.
- An assessment label must use the same taxonomy version as its assessment.
- Evidence and provenance are bounded text fields; Phase 1 does not store raw
  HTML, provider prompts, or screenshots in taxonomy tables.

PostgreSQL triggers enforce persistence invariants. Matching SQLAlchemy checks
make the same constraints available in the SQLite-backed unit-test suite.
