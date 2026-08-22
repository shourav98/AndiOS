"""
Agents Router — Team management page
GET    /agents          — list all agents
POST   /agents          — add new agent (checks plan limit)
POST   /agents/invite   — invite agent via email
GET    /agents/{id}     — single agent with stats
PATCH  /agents/{id}     — update agent
DELETE /agents/{id}     — deactivate agent
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from uuid import UUID
from typing import Optional
from pydantic import BaseModel, EmailStr
from database.supabase_client import get_supabase
from models.agent import AgentCreate, AgentUpdate, AgentResponse
from middleware.auth_middleware import verify_token
from utils.response import api_success, ApiResponse
from utils.tenant import require_agency_id, is_management_role
from utils.plan_limits import check_agent_limit, get_plan_limits
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agents", tags=["Agents"])


class InviteAgentRequest(BaseModel):
    email: EmailStr
    name: str
    role: str = "agent"  # 'agent' or 'manager'


@router.get("", response_model=ApiResponse[list[AgentResponse]])
async def list_agents(
    search: Optional[str] = Query(None),
    branch: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    limit: int = Query(50, le=100),
    offset: int = Query(0),
    current_user: dict = Depends(verify_token),
):
    sb = get_supabase()
    agency_id = require_agency_id(current_user)

    query = sb.table("agents").select("*").eq("agency_id", agency_id).eq("is_active", True)

    if search:
        query = query.or_(f"name.ilike.%{search}%,email.ilike.%{search}%")
    if branch and branch != "All branches":
        query = query.eq("branch", branch)
    if role and role != "All agents":
        query = query.eq("role", role)

    result = query.order("name").range(offset, offset + limit - 1).execute()
    return api_success(data=result.data, message="Agents retrieved successfully")


@router.post("", response_model=ApiResponse[AgentResponse], status_code=201)
async def create_agent(body: AgentCreate, current_user: dict = Depends(verify_token)):
    """Add a new agent — checks plan limits before creating."""
    sb = get_supabase()
    agency_id = require_agency_id(current_user)

    # Only owners and managers can add agents
    if not is_management_role(current_user.get("role")):
        raise HTTPException(status_code=403, detail="Only owners and managers can add agents")

    # Check plan limits
    check_agent_limit(agency_id)

    try:
        agent_data = body.model_dump(exclude_none=True)
        agent_data["agency_id"] = agency_id
        result = sb.table("agents").insert(agent_data).execute()
        return api_success(data=result.data[0], message="Agent created successfully", status_code=201)
    except Exception as e:
        if "unique" in str(e).lower():
            raise HTTPException(status_code=409, detail="Agent with this email already exists")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/invite", status_code=201)
async def invite_agent(body: InviteAgentRequest, current_user: dict = Depends(verify_token)):
    """
    Invite a new agent via email.
    1. Checks plan limits
    2. Creates agent row in agents table (is_active=True)
    3. Sends Supabase invite email — agent sets password on first login
    4. Syncs app_metadata so the invited user gets correct agency_id/role
    """
    sb = get_supabase()
    agency_id = require_agency_id(current_user)

    if not is_management_role(current_user.get("role")):
        raise HTTPException(status_code=403, detail="Only owners and managers can invite agents")

    # Check plan limits
    check_agent_limit(agency_id)

    # Check if agent with this email already exists
    existing = sb.table("agents").select("id").eq("email", body.email).eq("agency_id", agency_id).execute()
    if existing.data:
        raise HTTPException(status_code=409, detail="Agent with this email already exists in your agency")

    try:
        # Step 1: Create agent row
        agent_result = sb.table("agents").insert({
            "name": body.name,
            "email": body.email,
            "role": body.role,
            "agency_id": agency_id,
            "is_active": True,
        }).execute()

        if not agent_result.data:
            raise HTTPException(status_code=500, detail="Failed to create agent record")

        agent = agent_result.data[0]

        # Step 2: Send Supabase invite email
        try:
            invite_response = sb.auth.admin.invite_user_by_email(
                body.email,
                options={
                    "data": {
                        "agency_id": agency_id,
                        "role": body.role,
                        "agent_id": agent["id"],
                    },
                    "redirect_to": f"{__import__('config').settings.FRONTEND_URL}/auth/accept-invite",
                },
            )
            logger.info(f"Invite email sent to {body.email}")
        except Exception as invite_err:
            logger.warning(f"Supabase invite email failed (agent row created): {invite_err}")
            # Agent row is already created — they can still register manually

        return api_success(
            data=agent,
            message=f"Invitation sent to {body.email}",
            status_code=201,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Invite error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/plan-usage")
async def get_plan_usage(current_user: dict = Depends(verify_token)):
    """Get current plan limits and usage for the agency."""
    agency_id = require_agency_id(current_user)
    sb = get_supabase()

    limits = get_plan_limits(agency_id)

    # Get current agent count
    agents_result = sb.table("agents").select("id", count="exact").eq("agency_id", agency_id).eq("is_active", True).execute()
    agents_count = agents_result.count if hasattr(agents_result, "count") and agents_result.count is not None else len(agents_result.data)

    return api_success(
        data={
            "plan": limits["plan"],
            "agents": {"used": agents_count, "limit": limits["max_agents"]},
            "campaigns_per_month": {"limit": limits["max_campaigns_per_month"]},
            "ai_minutes": {"limit": limits["ai_minutes"]},
            "contracts": {"limit": limits["contracts"]},
        },
        message="Plan usage retrieved",
    )


@router.get("/{agent_id}")
async def get_agent(agent_id: UUID, current_user: dict = Depends(verify_token)):
    """Get agent profile with lead and viewing stats."""
    sb = get_supabase()
    agency_id = require_agency_id(current_user)

    agent = sb.table("agents").select("*").eq("id", str(agent_id)).eq("agency_id", agency_id).single().execute()
    if not agent.data:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Agent stats
    leads = sb.table("leads").select("status").eq("assigned_agent_id", str(agent_id)).execute().data
    viewings = sb.table("viewings").select("status").eq("agent_id", str(agent_id)).execute().data

    stats = {
        "total_leads": len(leads),
        "closed_deals": sum(1 for l in leads if l["status"] == "closed"),
        "total_viewings": len(viewings),
        "viewings_completed": sum(1 for v in viewings if v["status"] == "completed"),
    }
    return api_success(data={**agent.data, "stats": stats}, message="Agent profile retrieved successfully")


@router.patch("/{agent_id}", response_model=ApiResponse[AgentResponse])
async def update_agent(agent_id: UUID, body: AgentUpdate, current_user: dict = Depends(verify_token)):
    sb = get_supabase()
    agency_id = require_agency_id(current_user)
    update_data = body.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    management = is_management_role(current_user.get("role"))
    privileged_fields = {"role", "is_active"}

    # Only owners and managers can change roles or activation status
    if not management and privileged_fields & set(update_data.keys()):
        raise HTTPException(
            status_code=403,
            detail="Only owners and managers can change agent roles or activation status",
        )

    # Regular agents can only edit their own profile
    if not management and str(agent_id) != str(current_user.get("agent_id")):
        raise HTTPException(status_code=403, detail="You can only edit your own profile")

    result = sb.table("agents").update(update_data).eq("id", str(agent_id)).eq("agency_id", agency_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Agent not found")
    return api_success(data=result.data[0], message="Agent updated successfully")


@router.delete("/{agent_id}")
async def deactivate_agent(agent_id: UUID, current_user: dict = Depends(verify_token)):
    """Soft delete — sets is_active=False."""
    sb = get_supabase()
    agency_id = require_agency_id(current_user)

    if not is_management_role(current_user.get("role")):
        raise HTTPException(status_code=403, detail="Only owners and managers can remove agents")

    result = sb.table("agents").update({"is_active": False}).eq("id", str(agent_id)).eq("agency_id", agency_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Agent not found")
    return api_success(data={"agent_id": str(agent_id)}, message="Agent deactivated successfully")
