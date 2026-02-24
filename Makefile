.PHONY: lint typecheck test check fix format coverage coverage-html docs \
       docker-redis docker-test docker-clean \
       docker-server docker-server-clean docker-test-e2e \
       docker-tls-certs docker-redis-tls docker-test-tls \
       docker-timescale docker-test-timescale docker-clean-timescale

lint:
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

typecheck:
	uv run --all-extras mypy

test:
	uv run --all-extras pytest --tb=short -q

check: lint typecheck test

fix:
	uv run ruff check --fix src/ tests/
	uv run ruff format src/ tests/

format:
	uv run ruff format src/ tests/

coverage:
	uv run --all-extras pytest --cov --cov-report=term-missing

coverage-html:
	uv run --all-extras pytest --cov --cov-report=html

docs:
	uv run --group docs sphinx-build -W -b html docs docs/_build/html

# ---------------------------------------------------------------------------
# Docker / Server
# ---------------------------------------------------------------------------

docker-server:
	$(COMPOSE) up -d --build redis server

docker-server-clean:
	$(COMPOSE) down

docker-test-e2e: docker-server
	$(COMPOSE) up -d --wait server
	HS_PY_DOCKER_E2E=1 uv run pytest tests/test_e2e_docker.py --tb=short -q

# ---------------------------------------------------------------------------
# Docker / Redis integration tests
# ---------------------------------------------------------------------------

COMPOSE := docker compose -f docker/docker-compose.yml

docker-redis:
	$(COMPOSE) up -d redis

docker-test: docker-redis
	$(COMPOSE) up -d --wait redis
	uv run pytest tests/test_redis_ops.py --tb=short -q

docker-clean:
	$(COMPOSE) down -v --remove-orphans

# ---------------------------------------------------------------------------
# Docker / Redis TLS integration tests
# ---------------------------------------------------------------------------

docker-tls-certs:
	uv run python docker/gen_tls_certs.py

docker-redis-tls: docker-tls-certs
	$(COMPOSE) up -d --wait redis-tls

docker-test-tls: docker-redis-tls
	uv run pytest tests/test_redis_tls.py --tb=short -q

# ---------------------------------------------------------------------------
# Docker / TimescaleDB integration tests
# ---------------------------------------------------------------------------

docker-timescale:
	$(COMPOSE) up -d --wait timescaledb

docker-test-timescale: docker-timescale
	uv run pytest tests/test_storage_timescale.py --tb=short -q

docker-clean-timescale:
	$(COMPOSE) down -v --remove-orphans timescaledb
