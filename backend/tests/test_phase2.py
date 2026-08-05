import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock

MOCK_USER = {
    "sub": "test-user",
    "agency_id": "test-agency-id",
    "role": "owner",
    "agent_id": "test-agent-id",
}


@pytest.fixture
def client():
    with patch("database.supabase_client.get_supabase") as mock_sb:
        mock_sb.return_value = MagicMock()
        from main import app
        from middleware.auth_middleware import verify_token
        app.dependency_overrides[verify_token] = lambda: MOCK_USER.copy()
        with TestClient(app) as c:
            yield c, mock_sb.return_value
        app.dependency_overrides.clear()


# ─── Documents ───────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_create_document_triggers_extraction(client):
    c, mock_sb = client

    mock_sb.table.return_value.insert.return_value.execute.return_value.data = [{
        "id": "11111111-1111-1111-1111-111111111111",
        "lead_id": "00000000-0000-0000-0000-000000000001",
        "document_type": "passport",
        "file_url": "https://example.com/passport.jpg",
        "status": "pending",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
    }]

    with patch("services.document_service.client") as mock_openai:
        mock_response = MagicMock()
        mock_response.choices[0].message.content = '{"full_name": "John Doe", "document_number": "P1234567"}'
        mock_openai.chat.completions.create = AsyncMock(return_value=mock_response)

        mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [{
            "id": "11111111-1111-1111-1111-111111111111",
            "lead_id": "00000000-0000-0000-0000-000000000001",
            "document_type": "passport",
            "file_url": "https://example.com/passport.jpg",
        }]

        with patch("routers.documents.verify_lead_access", new_callable=AsyncMock) as mock_access:
            mock_access.return_value = {"id": "00000000-0000-0000-0000-000000000001", "agency_id": "test-agency-id"}
            resp = c.post("/documents/", json={
                "lead_id": "00000000-0000-0000-0000-000000000001",
                "document_type": "passport",
                "file_url": "https://example.com/passport.jpg"
            })

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == "11111111-1111-1111-1111-111111111111"
        assert data["extracted_data"]["full_name"] == "John Doe"
        assert data["status"] == "extracted"


# ─── Contracts ───────────────────────────────────────────────────────────────
def test_create_contract(client):
    c, mock_sb = client

    mock_sb.table.return_value.insert.return_value.execute.return_value.data = [{
        "id": "22222222-2222-2222-2222-222222222222",
        "lead_id": "00000000-0000-0000-0000-000000000001",
        "type": "tenancy_agreement",
        "property_address": "Marina 123",
        "rent_amount": 100000,
        "start_date": "2024-01-01",
        "end_date": "2024-12-31",
        "status": "draft",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
    }]

    resp = c.post("/contracts/", json={
        "lead_id": "00000000-0000-0000-0000-000000000001",
        "type": "tenancy_agreement",
        "property_address": "Marina 123",
        "rent_amount": 100000,
        "start_date": "2024-01-01",
        "end_date": "2024-12-31"
    })

    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "draft"


# ─── Cheques ─────────────────────────────────────────────────────────────────
def test_create_cheque(client):
    c, mock_sb = client

    mock_sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
        "id": "22222222-2222-2222-2222-222222222222",
    }
    mock_sb.table.return_value.insert.return_value.execute.return_value.data = [{
        "id": "33333333-3333-3333-3333-333333333333",
        "contract_id": "22222222-2222-2222-2222-222222222222",
        "cheque_number": "CHK001",
        "bank_name": "Emirates NBD",
        "amount": 25000,
        "due_date": "2024-01-01",
        "status": "pending",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
    }]

    resp = c.post("/cheques/", json={
        "contract_id": "22222222-2222-2222-2222-222222222222",
        "cheque_number": "CHK001",
        "bank_name": "Emirates NBD",
        "amount": 25000,
        "due_date": "2024-01-01"
    })

    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "pending"
