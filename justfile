set shell := ["bash", "-cu"]
set positional-arguments

default:
	@just --list

setup:
	cd frontend && bun install
	cd e2e && bun install && bunx --bun playwright install chromium
	cd backend && uv sync
	just up
	set -a && source .env.dev && set +a && cd backend && uv run alembic upgrade head && uv run python -m scripts.admin.create_auth_admin_user && uv run python -m scripts.admin.bootstrap_local_admin_state

up:
	docker compose -f docker-compose.dev.yml up --build -d

down:
	docker compose -f docker-compose.dev.yml down -v

logs service='':
	if [ -n "{{service}}" ]; then docker compose -f docker-compose.dev.yml logs -f {{service}}; else docker compose -f docker-compose.dev.yml logs -f; fi

ps:
	docker compose -f docker-compose.dev.yml ps

dev-backend:
	set -a && source .env && set +a && cd backend && uv sync && scripts/lifecycle/prestart.sh && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

dev-frontend:
	set -a && source .env && set +a && cd frontend && bun install && bun run dev -- --host 0.0.0.0 --port 5173

dev-app:
	set -a && source .env && set +a && \
		for port in 8000 5173; do \
			pid=$(lsof -t -nP -iTCP:$port -sTCP:LISTEN 2>/dev/null | head -n 1); \
			if [ -n "$pid" ]; then \
				echo "Port $port is already in use by PID $pid:"; \
				ps -p "$pid" -o pid=,ppid=,cmd=; \
				echo "Stop the existing process or use the running app directly."; \
				exit 1; \
			fi; \
		done; \
		(cd backend && uv sync && scripts/lifecycle/prestart.sh && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload) & \
		backend_pid=$!; \
		(cd frontend && bun install && bun run dev -- --host 0.0.0.0 --port 5173) & \
		frontend_pid=$!; \
		trap 'kill $backend_pid $frontend_pid 2>/dev/null || true' EXIT INT TERM; \
		wait -n $backend_pid $frontend_pid; \
		exit_code=$?; \
		kill $backend_pid $frontend_pid 2>/dev/null || true; \
		wait $backend_pid $frontend_pid 2>/dev/null || true; \
		exit $exit_code

dev:
	just up
	just dev-app

env:
	bash scripts/pull-bitwarden-env.sh

init:
	docker compose --profile init run --rm auth-db-init

migrate:
	docker compose run --rm --no-deps backend uv run alembic upgrade head

deploy:
	docker compose pull backend
	just migrate
	docker compose up -d --no-build backend auth redis

test-backend *args:
	set -a && source .env.dev && set +a && cd backend && scripts/lifecycle/tests-start.sh "$@"

format-backend:
	uv run --project backend ruff format backend
	uv run --project backend ruff check --fix backend

test-frontend:
	set -a && source .env.dev && set +a && cd frontend && bun run test

build-frontend:
	set -a && source .env.dev && set +a && cd frontend && bun run build

format-frontend:
	cd frontend && bun run format

# Read-only frontend checks: Biome lint/format check and Svelte/TypeScript diagnostics.
lint-frontend:
	cd frontend && bun run lint

check-frontend:
	cd frontend && bun run check

check-all:
	just format-backend
	just format-frontend
	just test-backend
	just test-frontend

test-e2e:
	set -a && source .env.dev && set +a && cd e2e && bun run test

test-guards:
	set -a && source .env.dev && set +a && cd e2e && bun run test:guards

generate-client:
	./scripts/generate-client.sh

load-clickhouse-local:
	set -a && source .env.dev && set +a && cd backend && uv run scripts/data/insert/load_local_clickhouse.py && uv run python -m scripts.clickhouse.build_fact_tables

build-clickhouse-facts:
	set -a && source .env.dev && set +a && cd backend && uv run python -m scripts.clickhouse.build_fact_tables

holocron +args='':
	@cd holocron && just {{args}}

# Read-only checks and explicitly isolated integration suites.
lint-backend:
	uv run --project backend ruff check backend
	uv run --project backend ruff format --check backend

test-backend-postgres *args:
	set -a && source .env.dev && set +a && cd backend && uv run pytest tests/integration/postgres "$@"

test-backend-clickhouse *args:
	set -a && source .env.dev && set +a && cd backend && uv run pytest tests/integration/clickhouse "$@"

test-backend-infrastructure *args:
	set -a && source .env.dev && set +a && cd backend && uv run pytest tests/integration/infrastructure "$@"

# Read-only AI capability discovery; does not call model providers or upload data.
ai-inventory:
	cd backend && uv run python -m scripts.evaluation inventory

ai-benchmark:
	cd backend && uv run python -m scripts.evaluation dataset

test-evaluation *args:
	just test-backend tests/evaluation {{args}}

# Explicit live model execution, private evidence, local authenticated application.
ai-evaluate *args:
	set -a && source .env.dev && set +a && cd backend && uv run python -m scripts.evaluation run {{args}}

# Local trace assessment and comparison; never call models or export evidence.
ai-assess *args:
	cd backend && uv run python -m scripts.evaluation assess {{args}}

ai-compare *args:
	cd backend && uv run python -m scripts.evaluation compare {{args}}

# Preview by default. --upload explicitly sends only allowlisted metadata to LangSmith.
ai-export *args:
	set -a && source .env.dev && set +a && cd backend && uv run python -m scripts.evaluation export {{args}}
