# WebAge Classifier — Milestones 1–6

A local-first website age-classification system with static inspection, secure browser
fallback, optional provider-independent AI fallback, and evaluation and pilot
operations. AI is disabled by default and database age policies remain authoritative.

## Evaluation and controlled pilots

Milestone 6 adds immutable versioned CSV/JSONL evaluation datasets, reviewer
agreement and adjudication, batch evaluation, calibration, separate category and
policy metrics, audited ruleset versioning, manual review, and controlled Tranco
pilots. Pilot sizes are restricted to 100, 1,000, and 10,000; dry-runs dispatch no
work and generate reproducible capacity, runtime, cost, and storage estimates.

See [evaluation datasets](docs/evaluation-guide.md),
[classifier versioning](docs/classifier-versioning.md),
[manual review](docs/manual-review-operations.md), and
[pilot operations](docs/pilot-operations.md).

## Prerequisites

- Windows 11 with WSL2 enabled
- Ubuntu running under WSL2
- Docker Desktop using the WSL2 engine, with integration enabled for the Ubuntu distribution
- Git and Codex CLI in WSL2

Python, Node.js, PostgreSQL, and Redis are not required on the host. Run all project commands from the WSL2 repository directory, not a Windows-mounted `/mnt/c` path, for faster and more reliable container file I/O.

## First run

```bash
cp .env.example .env
```

Replace all `CHANGE_ME` values. Generate secrets without a host Python installation:

```bash
docker run --rm python:3.12-alpine python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Use distinct output for `SECRET_KEY`, `POSTGRES_PASSWORD`, and the initial admin password. Then:

```bash
docker compose config --quiet
docker compose build
docker compose up -d db
docker compose run --rm backend alembic upgrade head
docker compose run --rm backend python -m app.seed
docker compose up -d
```

Open <http://localhost:3000>. The API is bound only to <http://127.0.0.1:8000>; PostgreSQL has no host port at all. For local HTTP, `COOKIE_SECURE=false` is necessary. Set it to `true` behind HTTPS.

## Operations and checks

```bash
make format
make lint
make typecheck
make test
docker compose run --rm browser-security-test
make compose-config
docker compose ps
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/ready
```

`make down` stops services while retaining PostgreSQL data. `docker compose down --volumes` destroys local database data and should only be used intentionally.

## Security model

- Passwords use Argon2id. Login produces an opaque random token; only its HMAC-SHA256 digest is stored.
- The session cookie is HTTP-only and SameSite Strict. Mutation requests also require a matching CSRF cookie/header token.
- The browser stores no authentication secrets in localStorage.
- CORS origins and trusted hosts are explicit environment allowlists.
- Administrative writes require the backend `admin` role and create an audit entry transactionally.
- Inputs are bounded and validated; persistence uses SQLAlchemy expressions rather than constructed SQL.
- Images run as non-root, drop Linux capabilities, use read-only filesystems where practical, and PostgreSQL is isolated on an internal Docker network.
- Configuration refuses placeholder/short secrets. Logs contain request metadata, not request bodies, cookies, or credentials.

The initial admin is created only by the explicit, idempotent seed command. Change its password value in `.env` before the first seed. Existing credentials are never overwritten by subsequent seeds.

## Structure

```text
backend/   FastAPI, async SQLAlchemy, Alembic, tests
frontend/  Next.js App Router, TypeScript, tests
compose.yaml
Makefile
```

## Milestone 2 website checks

`POST /api/websites/check` normalizes a hostile URL, creates or reuses its website record, and quickly enqueues a deduplicated realtime classification run. Crawling occurs only in a worker.

For local testing only, an administrator can call:

```text
POST /api/websites/{website_id}/runs/{run_id}/classify-now
```

That explicit path performs one asynchronous inspection directly. It validates DNS and every redirect destination, accepts only globally routable IPv4/IPv6 answers, disables environment proxies and cookies, follows redirects manually, applies strict time and byte limits, accepts only HTML, never executes JavaScript, and discards raw HTML after bounded extraction. Public errors remain generic while logs record only a stable failure code and run ID.

History is available at `GET /api/websites/{website_id}/history`. Administrators can create an audited manual result at `POST /api/websites/{website_id}/manual-override`; manual results coexist with rule history instead of destructively replacing it.

DNS is validated immediately before each request and redirect. Every answer must be public, and the worker connects to one validated address while preserving the original Host header and TLS SNI identity. The client sends no authorization headers, cookies, or internal credentials.

## Milestone 3 queues

PostgreSQL is the authoritative job store. Redis contains only bounded JSON Celery messages, rate counters, and expiring domain locks; it has no host port and stores no credentials or crawl content. Task payloads contain only a run UUID.

- `realtime`: priority 9 for active authenticated-user requests.
- `standard`: priority 5 for bounded Tranco rank batches.
- `maintenance`: reserved for fixed-name operational work.

A partial unique PostgreSQL index prevents multiple pending, retrying, or running jobs for one website. A token-owned Redis lock prevents concurrent work for a registrable domain. Task execution locks the database run, reuses an existing result, checks cancellation before persistence, and never changes a completed result to failed.

Transient network, lock, timeout, and worker errors use bounded exponential backoff with jitter. Unsafe destinations and rejected content fail permanently. Defaults provide four attempts, a 45-second soft limit, and a 60-second hard limit. Workers acknowledge late, reject work on process loss, prefetch one task, and receive a 75-second shutdown grace period.

```text
GET  /api/websites/runs/{run_id}/status
POST /api/websites/runs/{run_id}/cancel
POST /api/websites/runs/{run_id}/retry       (admin)
POST /api/websites/bulk-enqueue              (admin)
GET  /api/operations/queues                  (admin)
GET  /api/operations/workers                 (admin)
```

Bulk enqueue loads a bounded Tranco rank range and all existing active runs without N+1 queries. Interactive enqueue uses a per-user Redis rate window; both paths enforce a global PostgreSQL active-job ceiling, database uniqueness, and configured batch limits.

```bash
docker compose logs -f worker-realtime worker-standard worker-maintenance
make worker-status
make queue-status
```

Cancellation is cooperative for an in-flight request: the PostgreSQL flag prevents result persistence, while queued tasks are revoked without abruptly terminating a worker.

## Milestone 4 browser fallback

Static HTTP inspection remains the default. A new browser run is created only for insufficient static content, likely JavaScript rendering, low rule confidence, or an explicit administrator request. Playwright runs exclusively in the `worker-browser-realtime` and `worker-browser` containers; the normal `realtime` and `standard` workers never launch Chromium.

Browser requests are intercepted and validated with the centralized SSRF policy. Initial navigation, redirects, subframes, scripts, styles, images, fonts, XHR/fetch, WebSockets, popups, and worker requests are treated as hostile. DNS and IP validation, re-resolution, request interception, redirect checks, and container isolation provide layered best-effort protection but cannot make DNS-rebinding protection perfect.

```text
POST   /api/browser/websites/{website_id}/reinspect
GET    /api/browser/runs/{inspection_id}
POST   /api/browser/runs/{inspection_id}/cancel
GET    /api/browser/artifacts/{artifact_id}
DELETE /api/browser/artifacts/{artifact_id}
```

Screenshots are disabled by default. When enabled, policy permits them only for explicit requests or configured low-confidence/manual-review/high-risk evidence. Images use opaque IDs in a private Docker volume, expire automatically, and are streamed through authenticated backend authorization. They are never stored in PostgreSQL or served from a public directory.

Browser contexts are fresh and nonpersistent. Downloads, popups, service workers, non-HTTP schemes, permissions, extensions, authentication automation, and persistent profiles are blocked. The system does not bypass CAPTCHAs, logins, paywalls, access controls, bot defenses, or geographic restrictions.

`docker compose run --rm browser-security-test` runs the browser security unit tests and the marked real-Chromium suite against randomized, process-local fixture ports. The fixture container has no Docker network and the test-only exact-origin validator is supplied by dependency injection; production settings cannot enable it. `make browser-security-test` is an optional wrapper when GNU Make is installed.

See [the browser worker guide](docs/browser-worker-operations.md), [security threat model](docs/security-threat-model.md), [screenshot retention guide](docs/screenshot-retention.md), and [WSL2 guidance](docs/wsl2-browser-resources.md).

## Milestone 5 AI fallback

AI classification is disabled by default and runs only after static rules and applicable browser
inspection remain ambiguous, or after an authorized explicit request. Dedicated `ai_realtime`,
`ai`, and `ai_maintenance` workers keep provider latency away from HTTP and browser work.

The provider receives bounded cleaned evidence, never raw HTML, credentials, cookies, environment
variables, or database records. Provider JSON is strictly validated against active database
categories. AI cannot set age, rating, blocked state, review requirements, or policy priority;
those values always come from PostgreSQL. Invalid or low-confidence output preserves the current
classification and requires manual review. Screenshots remain disabled unless both browser and AI
image policies explicitly permit them. No chain-of-thought is stored or exposed.

For local keyless testing set `AI_ENABLED=true`, `AI_PROVIDER=fake`, and `AI_MODEL=fake`, then run
`make ai-security-test`. AI can be wrong and does not establish legal suitability; high-risk and
low-confidence cases require administrator review.

See [AI provider configuration](docs/ai-provider-configuration.md),
[AI threat model](docs/ai-security-threat-model.md),
[prompt-injection defenses](docs/prompt-injection-defense.md),
[AI worker operations](docs/ai-worker-operations.md),
[usage controls](docs/ai-usage-cost-controls.md), and
[manual review](docs/manual-review-workflow.md).

## Streaming Tranco import

Place a Tranco CSV in a deliberate local path and mount it into the one-shot backend container. The importer reads the CSV iterator incrementally, validates and IDNA-normalizes each domain, retains only one bounded batch, and uses PostgreSQL `INSERT ... ON CONFLICT DO UPDATE`.

For a quick top-N test:

```bash
docker compose run --rm \
  -v "$PWD/data:/imports:ro" \
  backend python -m app.import_tranco /imports/top-1m.csv --limit 10000
```

Omit `--limit` for the full file. The database enforces unique domains and a partial unique index prevents duplicate pending/running classification work. Import progress is recorded in `tranco_imports`.

## Inspection and queue limits

Defaults are configurable in `.env`:

- 5-second connection timeout
- 10-second response/read timeout
- 1,000,000 response bytes
- 50,000 extracted characters
- 5 redirects
- 10,000 active jobs
- 1,000 jobs per bulk request
- 20 interactive enqueue requests per user per minute

Keep these bounded. The inspector does not bypass authentication, CAPTCHAs, paywalls, or access controls and does not retain full source HTML.

## Troubleshooting WSL2

- If `docker` is unavailable in Ubuntu, enable Docker Desktop → Settings → Resources → WSL Integration for that distribution, then restart WSL with `wsl --shutdown` from PowerShell.
- If ports are occupied, stop the conflicting process; do not expose PostgreSQL as a workaround.
- Inspect service logs with `docker compose logs backend frontend db redis worker-realtime worker-standard worker-maintenance`.
- Validate environment interpolation with `docker compose config` (it renders secrets, so do not paste its output into issues).
