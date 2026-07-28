# WebAge Classifier — Milestone 1

A local-only foundation for administering website age-classification categories and policies. Milestone 1 provides a FastAPI API, PostgreSQL persistence, secure cookie authentication, backend role checks, audit logging, and a Next.js admin dashboard. Crawling, queues, screenshots, and AI classification are intentionally out of scope.

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

## Troubleshooting WSL2

- If `docker` is unavailable in Ubuntu, enable Docker Desktop → Settings → Resources → WSL Integration for that distribution, then restart WSL with `wsl --shutdown` from PowerShell.
- If ports are occupied, stop the conflicting process; do not expose PostgreSQL as a workaround.
- Inspect service logs with `docker compose logs backend frontend db`.
- Validate environment interpolation with `docker compose config` (it renders secrets, so do not paste its output into issues).
