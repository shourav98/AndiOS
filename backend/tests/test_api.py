"""
AndiOS Backend Tests
Run: pytest tests/ -v
"""
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


# ─── Test Client Setup ────────────────────────────────────────────────────────
@pytest.fixture
def client():
    """FastAPI test client with mocked Supabase."""
    with patch("database.supabase_client.get_supabase") as mock_sb:
        mock_sb.return_value = MagicMock()
        from main import app
        with TestClient(app) as c:
            yield c, mock_sb.return_value


# ─── Health Check ─────────────────────────────────────────────────────────────
def test_root_endpoint(client):
    c, _ = client
    resp = c.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["service"] == "AndiOS API"
    assert data["status"] == "operational"


# ─── Webhook: Property Finder Deduplication ───────────────────────────────────
def test_property_finder_webhook_new_lead(client):
    c, mock_sb = client

    # Mock: no existing lead (not a duplicate)
    mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
    mock_sb.table.return_value.insert.return_value.execute.return_value.data = [{
        "id": "test-lead-uuid",
        "name": "John Doe",
        "phone": "+971501234567",
        "status": "new",
    }]

    with patch("services.whatsapp_service.send_whatsapp_message", new_callable=AsyncMock) as mock_wa:
        mock_wa.return_value = {"status": "sent"}
        with patch("services.dedup_service.is_duplicate", new_callable=AsyncMock) as mock_dedup:
            mock_dedup.return_value = False
            with patch("services.dedup_service.get_existing_lead_by_phone", new_callable=AsyncMock) as mock_phone:
                mock_phone.return_value = None
                resp = c.post("/webhooks/property-finder", json={
                    "lead": {
                        "id": "PF-12345",
                        "name": "John Doe",
                        "phone": "+971501234567",
                        "email": "john@example.com",
                        "property_ref": "MRN-001",
                        "property_title": "2BR Marina",
                        "bedrooms": 2,
                        "budget": 120000,
                        "community": "Dubai Marina",
                    }
                })
    assert resp.status_code == 200
    assert resp.json()["status"] in ("success", "duplicate")


def test_property_finder_webhook_duplicate(client):
    c, _ = client
    with patch("services.dedup_service.is_duplicate", new_callable=AsyncMock) as mock_dedup:
        mock_dedup.return_value = True
        resp = c.post("/webhooks/property-finder", json={
            "lead": {
                "id": "PF-EXISTING",
                "name": "Jane",
                "phone": "+971509999999",
            }
        })
    assert resp.status_code == 200
    assert resp.json()["status"] == "duplicate"


# ─── WhatsApp Webhook Verification ────────────────────────────────────────────
def test_whatsapp_verify_valid_token(client):
    c, _ = client
    resp = c.get("/webhooks/whatsapp", params={
        "hub.mode": "subscribe",
        "hub.challenge": "12345",
        "hub.verify_token": "andios_verify_token",
    })
    assert resp.status_code == 200
    assert resp.json() == 12345


def test_whatsapp_verify_invalid_token(client):
    c, _ = client
    resp = c.get("/webhooks/whatsapp", params={
        "hub.mode": "subscribe",
        "hub.challenge": "12345",
        "hub.verify_token": "wrong_token",
    })
    assert resp.status_code == 403


# ─── Deduplication Service ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_dedup_is_duplicate_true():
    with patch("services.dedup_service.get_supabase") as mock_sb:
        mock_sb.return_value.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"id": "existing-uuid"}
        ]
        from services.dedup_service import is_duplicate
        result = await is_duplicate("PF-12345")
        assert result is True


@pytest.mark.asyncio
async def test_dedup_is_duplicate_false():
    with patch("services.dedup_service.get_supabase") as mock_sb:
        mock_sb.return_value.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
        from services.dedup_service import is_duplicate
        result = await is_duplicate("PF-NEW")
        assert result is False


# ─── AI Service: Handover Detection ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_ai_handover_detection_simple_message():
    """Non-complex messages should not trigger handover."""
    with patch("services.ai_service.client") as mock_client:
        mock_response = MagicMock()
        mock_response.choices[0].message.content = '{"needs_handover": false, "reason": null, "confidence": 0.1}'
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        from services.ai_service import detect_handover
        result = await detect_handover([], "What is the rent for 2BR in Marina?")
        assert result["needs_handover"] is False


@pytest.mark.asyncio
async def test_ai_handover_detection_complex_message():
    """Legal / complex queries should trigger handover."""
    with patch("services.ai_service.client") as mock_client:
        mock_response = MagicMock()
        mock_response.choices[0].message.content = '{"needs_handover": true, "reason": "Legal question about tenancy contract", "confidence": 0.95}'
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        from services.ai_service import detect_handover
        result = await detect_handover([], "I want to dispute the contract clause about maintenance fees")
        assert result["needs_handover"] is True
        assert result["confidence"] > 0.7


# ─── Lead Stats ───────────────────────────────────────────────────────────────
def test_lead_stats_empty_db(client):
    c, mock_sb = client
    # Mock empty leads and viewings
    mock_sb.table.return_value.select.return_value.execute.return_value.data = []
    mock_sb.table.return_value.select.return_value.order.return_value.execute.return_value.data = []

    from main import app
    from middleware.auth_middleware import verify_token
    app.dependency_overrides[verify_token] = lambda: {"sub": "test-user"}
    try:
        resp = c.get("/leads/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
    finally:
        app.dependency_overrides.clear()


# ─── AI Score Lead ────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_lead_scoring_returns_int():
    with patch("services.ai_service.client") as mock_client:
        mock_response = MagicMock()
        mock_response.choices[0].message.content = '{"score": 75, "reason": "Good budget, specific location"}'
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        from services.ai_service import score_lead
        score = await score_lead(
            {"name": "Ali", "source": "property_finder"},
            [{"sender_type": "lead", "message_body": "I want 2BR in Marina, budget 120k AED"}],
        )
        assert isinstance(score, int)
        assert 0 <= score <= 100
