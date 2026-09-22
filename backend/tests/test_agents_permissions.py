"""
PATCH /agents/{agent_id} permission tests (security fix).

Rules enforced by routers.agents.update_agent:
- role / is_active can only be changed by management (owner | manager)
- regular agents may only edit their own profile (non-privileged fields)

These tests invoke update_agent directly (no TestClient / main import) so they
cannot interfere with the app-wide supabase mock binding other suites rely on.
Run: pytest tests/test_agents_permissions.py -v
"""
import pytest
from uuid import UUID
from unittest.mock import patch, MagicMock
from fastapi import HTTPException

from models.agent import AgentRole, AgentUpdate
from routers.agents import update_agent

AGENT_ID = UUID("44444444-4444-4444-4444-444444444444")
OTHER_AGENT_ID = UUID("55555555-5555-5555-5555-555555555555")

AGENT_ROW = {
    "id": str(AGENT_ID),
    "name": "Test Agent",
    "phone": "+971501234567",
    "email": "agent@testagency.com",
    "role": "agent",
    "calendar_id": None,
    "whatsapp_number": None,
    "branch": "Marina",
    "is_active": True,
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-01T00:00:00Z",
}


def _user(role: str, agent_id) -> dict:
    return {
        "sub": f"auth-{agent_id}",
        "email": "caller@testagency.com",
        "agency_id": "test-agency-id",
        "role": role,
        "agent_id": str(agent_id),
    }


def _mock_sb(updated_row: dict) -> MagicMock:
    sb = MagicMock()
    sb.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value.data = [updated_row]
    return sb


# ─── ALLOWED ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_owner_can_update_another_agent_profile():
    with patch("routers.agents.get_supabase", return_value=_mock_sb({**AGENT_ROW, "name": "Renamed Agent"})):
        result = await update_agent(
            agent_id=OTHER_AGENT_ID,
            body=AgentUpdate(name="Renamed Agent"),
            current_user=_user("owner", AGENT_ID),
        )
    assert result["success"] is True
    assert result["data"]["name"] == "Renamed Agent"


@pytest.mark.asyncio
async def test_manager_can_change_agent_role():
    sb = _mock_sb({**AGENT_ROW, "role": "senior_agent"})
    with patch("routers.agents.get_supabase", return_value=sb):
        result = await update_agent(
            agent_id=OTHER_AGENT_ID,
            body=AgentUpdate(role=AgentRole.senior_agent),
            current_user=_user("manager", AGENT_ID),
        )
    assert result["data"]["role"] == "senior_agent"
    sb.table.return_value.update.assert_called_once_with({"role": AgentRole.senior_agent})


@pytest.mark.asyncio
async def test_owner_can_deactivate_agent():
    sb = _mock_sb({**AGENT_ROW, "is_active": False})
    with patch("routers.agents.get_supabase", return_value=sb):
        result = await update_agent(
            agent_id=OTHER_AGENT_ID,
            body=AgentUpdate(is_active=False),
            current_user=_user("owner", AGENT_ID),
        )
    assert result["data"]["is_active"] is False


@pytest.mark.asyncio
async def test_agent_can_update_own_non_privileged_profile():
    new_phone = "+971509999999"
    sb = _mock_sb({**AGENT_ROW, "phone": new_phone, "whatsapp_number": new_phone})
    with patch("routers.agents.get_supabase", return_value=sb):
        result = await update_agent(
            agent_id=AGENT_ID,
            body=AgentUpdate(phone=new_phone, whatsapp_number=new_phone),
            current_user=_user("agent", AGENT_ID),
        )
    assert result["success"] is True
    assert result["data"]["phone"] == new_phone


# ─── FORBIDDEN ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_cannot_promote_self_to_manager():
    sb = MagicMock()
    with patch("routers.agents.get_supabase", return_value=sb):
        with pytest.raises(HTTPException) as exc_info:
            await update_agent(
                agent_id=AGENT_ID,
                body=AgentUpdate(role=AgentRole.manager),
                current_user=_user("agent", AGENT_ID),
            )
    assert exc_info.value.status_code == 403
    sb.table.return_value.update.assert_not_called()


@pytest.mark.asyncio
async def test_agent_cannot_change_anyone_role():
    sb = MagicMock()
    with patch("routers.agents.get_supabase", return_value=sb):
        with pytest.raises(HTTPException) as exc_info:
            await update_agent(
                agent_id=OTHER_AGENT_ID,
                body=AgentUpdate(role=AgentRole.owner),
                current_user=_user("agent", AGENT_ID),
            )
    assert exc_info.value.status_code == 403
    sb.table.return_value.update.assert_not_called()


@pytest.mark.asyncio
async def test_senior_agent_is_not_management():
    """senior_agent is intentionally outside MANAGEMENT_ROLES."""
    sb = MagicMock()
    with patch("routers.agents.get_supabase", return_value=sb):
        with pytest.raises(HTTPException) as exc_info:
            await update_agent(
                agent_id=OTHER_AGENT_ID,
                body=AgentUpdate(is_active=False),
                current_user=_user("senior_agent", AGENT_ID),
            )
    assert exc_info.value.status_code == 403
    sb.table.return_value.update.assert_not_called()


@pytest.mark.asyncio
async def test_agent_cannot_edit_other_agent_profile():
    sb = MagicMock()
    with patch("routers.agents.get_supabase", return_value=sb):
        with pytest.raises(HTTPException) as exc_info:
            await update_agent(
                agent_id=OTHER_AGENT_ID,
                body=AgentUpdate(name="Hijacked"),
                current_user=_user("agent", AGENT_ID),
            )
    assert exc_info.value.status_code == 403
    sb.table.return_value.update.assert_not_called()


@pytest.mark.asyncio
async def test_agent_mixed_payload_with_role_is_rejected_entirely():
    """A non-privileged field must not smuggle a privileged field through."""
    sb = MagicMock()
    with patch("routers.agents.get_supabase", return_value=sb):
        with pytest.raises(HTTPException) as exc_info:
            await update_agent(
                agent_id=AGENT_ID,
                body=AgentUpdate(name="New Name", role=AgentRole.owner),
                current_user=_user("agent", AGENT_ID),
            )
    assert exc_info.value.status_code == 403
    sb.table.return_value.update.assert_not_called()
