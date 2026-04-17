.PHONY: help install install-dev build-frontend run-backend test test-api lint-frontend

help:
	@echo "Targets: install, install-dev, build-frontend, run-backend, test, test-api, lint-frontend"

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
	PYTHONPATH=. python -m pytest tests/test_api.py -q

test-api:
	PYTHONPATH=. python -m pytest tests/test_api.py -q

lint-frontend:
	cd frontend && npx tsc --noEmit
