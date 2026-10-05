.PHONY: help install install-dev build-frontend run-backend test test-api test-postgres lint-frontend

help:
	@echo "Targets: install, install-dev, build-frontend, run-backend, test, test-api, test-postgres, lint-frontend"

install:
	pip install -e .
	pip install -r backend/requirements.txt
	cd frontend && npm ci

install-dev:
	pip install -e ".[dev]"
	pip install -r backend/requirements.txt
	cd frontend && npm ci

build-frontend:
	cd frontend && npm run build

run-backend:
	PYTHONPATH=. uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000

test:
	PYTHONPATH=. python -m pytest tests/ -q

test-api:
	PYTHONPATH=. python -m pytest tests/test_api.py -q

# Requires TEST_DATABASE_URL=postgresql://… (local :5433 or CI service). See tests/README.md.
test-postgres:
	@test -n "$$TEST_DATABASE_URL" || (echo "Set TEST_DATABASE_URL to a postgresql:// URL (db shopifyseo_test, port 5433 on the box)"; exit 1)
	PYTHONPATH=. python -m pytest tests/test_db_layer.py tests/test_db_fixture.py -q

lint-frontend:
	cd frontend && npx tsc --noEmit
