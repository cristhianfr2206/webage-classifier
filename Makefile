.PHONY: build up down logs migrate seed format lint typecheck test check compose-config import-tranco worker-status queue-status browser-worker-status browser-security-test ai-worker-status ai-security-test evaluation-worker-status evaluation-test

build:
	docker compose build
up:
	docker compose up -d --build
down:
	docker compose down
logs:
	docker compose logs -f
migrate:
	docker compose run --rm backend alembic upgrade head
seed:
	docker compose run --rm backend python -m app.seed
format:
	docker compose run --rm backend-check ruff format .
	docker compose run --rm frontend-check npm run format
lint:
	docker compose run --rm backend-check ruff check .
	docker compose run --rm frontend-check npm run lint
typecheck:
	docker compose run --rm backend-check mypy app
	docker compose run --rm frontend-check npm run typecheck
test:
	docker compose run --rm backend-check pytest
	docker compose run --rm frontend-check npm test
check: lint typecheck test
compose-config:
	docker compose config --quiet

import-tranco:
	@test -n "$(CSV)" || (echo "Usage: make import-tranco CSV=/absolute/path.csv [LIMIT=10000]" && exit 2)
	docker compose run --rm -v "$(CSV):/imports/tranco.csv:ro" backend \
		python -m app.import_tranco /imports/tranco.csv $(if $(LIMIT),--limit $(LIMIT),)

worker-status:
	docker compose exec backend celery -A app.celery_app:celery_app inspect ping

queue-status:
	docker compose exec redis redis-cli --scan --pattern 'celery*'

browser-worker-status:
	docker compose exec backend celery -A app.browser_celery_app:browser_celery_app inspect ping

browser-security-test:
	docker compose run --rm browser-security-test

ai-worker-status:
	docker compose exec backend celery -A app.ai_celery_app:ai_celery_app inspect ping

ai-security-test:
	docker compose run --rm backend-check pytest tests/test_ai_security.py tests/test_ai_jobs.py tests/test_ai_state_integration.py

evaluation-worker-status:
	docker compose exec backend celery -A app.evaluation_celery_app:evaluation_celery_app inspect ping

evaluation-test:
	docker compose run --rm backend-check pytest tests/test_evaluation.py tests/test_evaluation_api.py
