"""API contract tests for the internal links router."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app


client = TestClient(app)


def test_summary_endpoint_shape():
    res = client.get("/api/internal-links/summary")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    data = body["data"]
    assert set(data) >= {"total_links", "orphan_count", "suggested", "applied", "dismissed"}


def test_suggestions_endpoint_returns_list():
    res = client.get("/api/internal-links/suggestions?status=suggested")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert isinstance(body["data"], list)


def test_orphans_endpoint_returns_list():
    res = client.get("/api/internal-links/orphans")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert isinstance(body["data"], list)


def test_rebuild_endpoint_starts_background():
    res = client.post("/api/internal-links/rebuild")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["data"]["status"] == "started"


def test_dismiss_not_found():
    res = client.post("/api/internal-links/suggestions/999999/dismiss")
    assert res.status_code == 404
