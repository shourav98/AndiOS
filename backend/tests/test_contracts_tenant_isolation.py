"""
Cross-tenant isolation tests for contract generate / close (security fix).

Rules enforced:
- POST /contracts/{id}/generate and /close are scoped to the caller's agency
  via apply_agency_scope before the service layer runs
- foreign or non-existent contracts return 404 "Contract not found"
  (no existence leak), and no mutation is attempted

Tests invoke the route functions directly (no TestClient / main import) so they
cannot interfere with the app-wide supabase mock binding other suites rely on.
Run: pytest tests/test_contracts_tenant_isolation.py -v
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi import HTTPException

from routers.contracts import (
    generate_contract_pdf,
    close_contract,
    CloseContractRequest,
)

CONTRACT_ID = "66666666-6666-6666-6666-666666666666"
CHEQUE_URL = "https://storage.example.com/cheques/fee.jpg"


def _user(agency_id: str = "agency-a-id") -> dict:
    return {
        "sub": "auth-user-1",
        "email": "agent@agencya.com",
        "agency_id": agency_id,
        "role": "agent",
        "agent_id": "agent-1-id",
    }


def _mock_sb(found: bool) -> MagicMock:
    """Supabase mock whose agency-scoped existence query finds / misses the contract."""
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = (
        [{"id": CONTRACT_ID}] if found else []
    )
    return sb


# ─── GENERATE ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_same_agency_generate_allowed():
    sb = _mock_sb(found=True)
    with patch("database.supabase_client.get_supabase", return_value=sb), \
         patch("routers.contracts.generate_tenancy_agreement", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = "https://storage.example.com/contracts/contract.pdf"

        result = await generate_contract_pdf(CONTRACT_ID, _user())

    assert result["success"] is True
    assert result["data"]["url"] == "https://storage.example.com/contracts/contract.pdf"
    mock_gen.assert_awaited_once_with(CONTRACT_ID)


@pytest.mark.asyncio
async def test_foreign_agency_generate_returns_404_and_does_not_mutate():
    sb = _mock_sb(found=False)
    with patch("database.supabase_client.get_supabase", return_value=sb), \
         patch("routers.contracts.generate_tenancy_agreement", new_callable=AsyncMock) as mock_gen:

        with pytest.raises(HTTPException) as exc_info:
            await generate_contract_pdf(CONTRACT_ID, _user(agency_id="agency-b-id"))

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Contract not found"
    mock_gen.assert_not_called()
    sb.table.return_value.update.assert_not_called()


@pytest.mark.asyncio
async def test_nonexistent_contract_generate_returns_404():
    sb = _mock_sb(found=False)
    with patch("database.supabase_client.get_supabase", return_value=sb), \
         patch("routers.contracts.generate_tenancy_agreement", new_callable=AsyncMock) as mock_gen:

        with pytest.raises(HTTPException) as exc_info:
            await generate_contract_pdf(CONTRACT_ID, _user())

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Contract not found"
    mock_gen.assert_not_called()


@pytest.mark.asyncio
async def test_generate_query_is_agency_scoped():
    """Regression guard: the existence check must filter by agency_id."""
    sb = _mock_sb(found=False)
    with patch("database.supabase_client.get_supabase", return_value=sb), \
         patch("routers.contracts.generate_tenancy_agreement", new_callable=AsyncMock):

        with pytest.raises(HTTPException):
            await generate_contract_pdf(CONTRACT_ID, _user(agency_id="agency-b-id"))

    select_chain = sb.table.return_value.select.return_value
    select_chain.eq.assert_called_once_with("id", CONTRACT_ID)
    select_chain.eq.return_value.eq.assert_called_once_with("agency_id", "agency-b-id")


# ─── CLOSE ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_same_agency_close_allowed():
    sb = _mock_sb(found=True)
    with patch("database.supabase_client.get_supabase", return_value=sb), \
         patch("routers.contracts.close_contract_with_cheque", new_callable=AsyncMock) as mock_close:
        mock_close.return_value = {"contract_id": CONTRACT_ID, "status": "closed"}

        result = await close_contract(CONTRACT_ID, CloseContractRequest(cheque_image_url=CHEQUE_URL), _user())

    assert result["success"] is True
    assert result["data"]["status"] == "closed"
    mock_close.assert_awaited_once_with(CONTRACT_ID, CHEQUE_URL)


@pytest.mark.asyncio
async def test_foreign_agency_close_returns_404_and_does_not_mutate():
    sb = _mock_sb(found=False)
    with patch("database.supabase_client.get_supabase", return_value=sb), \
         patch("routers.contracts.close_contract_with_cheque", new_callable=AsyncMock) as mock_close:

        with pytest.raises(HTTPException) as exc_info:
            await close_contract(CONTRACT_ID, CloseContractRequest(cheque_image_url=CHEQUE_URL), _user(agency_id="agency-b-id"))

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Contract not found"
    mock_close.assert_not_called()
    sb.table.return_value.update.assert_not_called()


@pytest.mark.asyncio
async def test_nonexistent_contract_close_returns_404():
    sb = _mock_sb(found=False)
    with patch("database.supabase_client.get_supabase", return_value=sb), \
         patch("routers.contracts.close_contract_with_cheque", new_callable=AsyncMock):

        with pytest.raises(HTTPException) as exc_info:
            await close_contract(CONTRACT_ID, CloseContractRequest(cheque_image_url=CHEQUE_URL), _user())

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Contract not found"
