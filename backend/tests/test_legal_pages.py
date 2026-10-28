"""
Tests for Legal Compliance endpoints:
- Privacy Policy (/privacy, /api/privacy)
- Terms of Service (/terms, /api/terms)
- User Data Deletion Instructions & Callbacks (/data-deletion, /api/data-deletion)
"""
import pytest
from starlette.testclient import TestClient
from main import app

client = TestClient(app)


# ─── Privacy Policy Tests ──────────────────────────────────────────────────────

def test_privacy_policy_html_endpoint():
    """Verify /privacy returns 200 HTML content with required Meta compliance elements."""
    response = client.get("/privacy")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "Privacy Policy" in response.text
    assert "AndiOS" in response.text
    assert "WhatsApp" in response.text
    assert "hello@funkydiandi.com" in response.text
    assert "/terms" in response.text
    assert "/data-deletion" in response.text


def test_privacy_policy_json_endpoint():
    """Verify /api/privacy returns structured 200 JSON content."""
    response = client.get("/api/privacy")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["status"] == 200
    assert data["data"]["title"] == "AndiOS Privacy Policy"


# ─── Terms of Service Tests ────────────────────────────────────────────────────

def test_terms_of_service_html_endpoint():
    """Verify /terms and aliases return 200 HTML content matching official PDF styling."""
    for path in ["/terms", "/terms-of-service", "/tos"]:
        response = client.get(path)
        assert response.status_code == 200, f"Failed on path {path}"
        assert "text/html" in response.headers.get("content-type", "")
        assert "AndiOS Terms of Service" in response.text
        assert "Funky Di Andi for Online Selling" in response.text
        assert "1582068" in response.text
        assert "Data Processor" in response.text
        assert "WhatsApp" in response.text
        assert "/privacy" in response.text
        assert "/data-deletion" in response.text


def test_terms_of_service_json_endpoint():
    """Verify /api/terms returns structured 200 JSON content."""
    for path in ["/api/terms", "/api/terms-of-service"]:
        response = client.get(path)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["status"] == 200
        assert data["data"]["title"] == "AndiOS Terms of Service"
        assert len(data["data"]["sections"]) >= 12


# ─── Data Deletion Instructions Tests ──────────────────────────────────────────

def test_data_deletion_html_endpoint():
    """Verify /data-deletion and aliases return 200 HTML content with instructions and form."""
    for path in ["/data-deletion", "/data-deletion-instructions", "/user-data-deletion"]:
        response = client.get(path)
        assert response.status_code == 200, f"Failed on path {path}"
        assert "text/html" in response.headers.get("content-type", "")
        assert "User Data Deletion Instructions" in response.text
        assert "Option 1: WhatsApp In-Chat Deletion" in response.text
        assert "Option 2: Online Deletion Request Form" in response.text
        assert "Option 3: Direct Email Request" in response.text
        assert "Option 4: Facebook App Settings" in response.text
        assert "30" in response.text  # 30-day deletion timeline
        assert "hello@funkydiandi.com" in response.text
        assert "/privacy" in response.text
        assert "/terms" in response.text


def test_data_deletion_json_endpoint():
    """Verify /api/data-deletion returns structured 200 JSON content."""
    response = client.get("/api/data-deletion")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["title"] == "AndiOS User Data Deletion Instructions"
    assert data["data"]["timeline_days"] == 30


def test_data_deletion_submission_and_status():
    """Verify submitting a data deletion request generates a tracking code and can be checked."""
    # 1. Submit request
    payload = {
        "name": "Ahmed Al-Mansoor",
        "email": "ahmed.mansoor@example.ae",
        "phone": "+971 50 987 6543",
        "role": "lead",
        "reason": "Test deletion request for Meta App Review verification"
    }
    submit_res = client.post("/api/data-deletion/request", json=payload)
    assert submit_res.status_code == 200
    submit_data = submit_res.json()
    assert submit_data["success"] is True
    tracking_code = submit_data["data"]["confirmation_code"]
    assert tracking_code.startswith("DEL-")
    assert submit_data["data"]["estimated_days"] == 30

    # 2. Check status via API
    status_res = client.get(f"/api/data-deletion/status?tracking_id={tracking_code}")
    assert status_res.status_code == 200
    status_data = status_res.json()
    assert status_data["success"] is True
    assert status_data["data"]["confirmation_code"] == tracking_code
    assert "In Progress" in status_data["data"]["status"]
    assert status_data["data"]["email"] == "ahmed.mansoor@example.ae"


def test_meta_data_deletion_callback_post():
    """Verify POST /data-deletion complies with Meta Callback requirements."""
    # When Facebook calls the callback, it sends form data with signed_request
    response = client.post("/data-deletion", data={"signed_request": "mock_signed_request_from_facebook"})
    assert response.status_code == 200
    data = response.json()
    # Meta strictly expects "url" and "confirmation_code"
    assert "url" in data
    assert "confirmation_code" in data
    assert data["url"].startswith("https://api.andios.ae/data-deletion?id=")
    assert data["confirmation_code"].startswith("DEL-")
