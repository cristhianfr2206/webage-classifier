.PHONY: build up down logs migrate seed format lint typecheck test check compose-config

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
