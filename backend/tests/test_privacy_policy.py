"""
Tests for Privacy Policy endpoints (HTML and JSON).
"""
import pytest
from starlette.testclient import TestClient
from main import app

client = TestClient(app)


def test_privacy_policy_html_endpoint():
    """Verify /privacy returns 200 HTML content with required Meta compliance elements."""
    response = client.get("/privacy")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "Privacy Policy" in response.text
    assert "AndiOS" in response.text
    assert "WhatsApp" in response.text
    assert "September 18, 2026" in response.text
    assert "privacy@andios.com" in response.text


def test_privacy_policy_alias_html_endpoint():
    """Verify /privacy-policy returns 200 HTML content."""
    response = client.get("/privacy-policy")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "Privacy Policy" in response.text


def test_privacy_policy_json_endpoint():
    """Verify /api/privacy returns structured 200 JSON content."""
    response = client.get("/api/privacy")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["status"] == 200
    assert "data" in data
    policy_data = data["data"]
    assert policy_data["title"] == "Privacy Policy"
    assert policy_data["effective_date"] == "September 18, 2026"
    assert len(policy_data["sections"]) >= 19


def test_privacy_policy_alias_json_endpoint():
    """Verify /api/privacy-policy returns structured 200 JSON content."""
    response = client.get("/api/privacy-policy")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["status"] == 200
    assert data["data"]["title"] == "Privacy Policy"
