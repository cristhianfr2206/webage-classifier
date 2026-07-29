# WebAge Classifier — Milestones 1–2

A local-only system for administering website age-classification categories and policies. Milestone 1 provides the secure application foundation. Milestone 2 adds normalized websites, streaming Tranco imports, safe HTTP inspection, weighted rule classification, age-policy application, and classification history. Browser automation, queues, screenshots, and AI classification remain intentionally out of scope.

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

`POST /api/websites/check` normalizes a hostile URL, creates or reuses its website record, and returns a deduplicated `pending` classification run. This is the production-facing queue boundary: it does not make outbound network requests.

For local testing only, an administrator can call:

```text
POST /api/websites/{website_id}/runs/{run_id}/classify-now
```

That explicit path performs one asynchronous inspection directly. It validates DNS and every redirect destination, accepts only globally routable IPv4/IPv6 answers, disables environment proxies and cookies, follows redirects manually, applies strict time and byte limits, accepts only HTML, never executes JavaScript, and discards raw HTML after bounded extraction. Public errors remain generic while logs record only a stable failure code and run ID.

History is available at `GET /api/websites/{website_id}/history`. Administrators can create an audited manual result at `POST /api/websites/{website_id}/manual-override`; manual results coexist with rule history instead of destructively replacing it.

DNS is validated immediately before each request and redirect. The client deliberately sends no authorization headers, cookies, or internal credentials. As with a normal hostname-based TLS client, a narrow DNS-change window remains between validation and the transport's own resolution; deployments needing stronger pinning should add a resolver-aware transport in a later worker milestone.

## Streaming Tranco import

Place a Tranco CSV in a deliberate local path and mount it into the one-shot backend container. The importer reads the CSV iterator incrementally, validates and IDNA-normalizes each domain, retains only one bounded batch, and uses PostgreSQL `INSERT ... ON CONFLICT DO UPDATE`.

For a quick top-N test:

```bash
docker compose run --rm \
  -v "$PWD/data:/imports:ro" \
  backend python -m app.import_tranco /imports/top-1m.csv --limit 10000
```

Omit `--limit` for the full file. The database enforces unique domains and a partial unique index prevents duplicate pending/running classification work. Import progress is recorded in `tranco_imports`.

## Milestone 2 limits

Defaults are configurable in `.env`:

- 5-second connection timeout
- 10-second response/read timeout
- 1,000,000 response bytes
- 50,000 extracted characters
- 5 redirects

Keep these bounded. The inspector does not bypass authentication, CAPTCHAs, paywalls, or access controls and does not retain full source HTML.

## Troubleshooting WSL2

- If `docker` is unavailable in Ubuntu, enable Docker Desktop → Settings → Resources → WSL Integration for that distribution, then restart WSL with `wsl --shutdown` from PowerShell.
- If ports are occupied, stop the conflicting process; do not expose PostgreSQL as a workaround.
- Inspect service logs with `docker compose logs backend frontend db`.
- Validate environment interpolation with `docker compose config` (it renders secrets, so do not paste its output into issues).
