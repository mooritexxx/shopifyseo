"""Tests for POST /api/opportunities/create-idea endpoint.

Regression test for bug where save_article_ideas returns list[int] but
the router incorrectly called .get("id") on the integer.
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from backend.app.main import app

client = TestClient(app)


def test_create_idea_from_opportunity_returns_integer_id():
    """Regression: save_article_ideas returns list[int], not list[dict].
    
    The endpoint must return the integer id directly, not call .get("id") on it.
    """
    with patch("shopifyseo.dashboard_article_ideas.save_article_ideas") as mock_save:
        mock_save.return_value = [123]
        
        response = client.post(
            "/api/opportunities/create-idea",
            json={
                "query": "best disposable vapes canada",
                "object_type": "collection",
                "object_handle": "disposable-vapes",
            },
        )
        
        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is True
        assert payload["data"]["idea_id"] == 123
        assert payload["data"]["message"] == "Article idea created from opportunity"
        
        mock_save.assert_called_once()
        call_args = mock_save.call_args
        idea_data = call_args[0][1][0]
        assert idea_data["primary_keyword"] == "best disposable vapes canada"
        assert idea_data["source_type"] == "gsc_opportunity"


def test_create_idea_from_opportunity_with_content_type():
    """Test that content_type parameter is accepted (but not stored in current implementation)."""
    with patch("shopifyseo.dashboard_article_ideas.save_article_ideas") as mock_save:
        mock_save.return_value = [456]
        
        response = client.post(
            "/api/opportunities/create-idea",
            json={
                "query": "how to vape",
                "object_type": "product",
                "object_handle": "elfbar-5000",
                "content_type": "Blog / Guide",
            },
        )
        
        assert response.status_code == 200
        payload = response.json()
        assert payload["data"]["idea_id"] == 456


def test_create_idea_from_opportunity_returns_500_when_save_fails():
    """Test that empty return from save_article_ideas raises 500."""
    with patch("shopifyseo.dashboard_article_ideas.save_article_ideas") as mock_save:
        mock_save.return_value = []
        
        response = client.post(
            "/api/opportunities/create-idea",
            json={
                "query": "failing query",
                "object_type": "page",
                "object_handle": "about-us",
            },
        )
        
        assert response.status_code == 500
