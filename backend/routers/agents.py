"""
Agents Router — Team management page
GET    /agents          — list all agents
POST   /agents          — add new agent
GET    /agents/{id}     — single agent with stats
PATCH  /agents/{id}     — update agent
DELETE /agents/{id}     — deactivate agent
"""
from fastapi import APIRouter, Depends, HTTPException
from uuid import UUID
from database.supabase_client import get_supabase
from models.agent import AgentCreate, AgentUpdate, AgentResponse
from middleware.auth_middleware import verify_token
from utils.response import api_success, ApiResponse
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agents", tags=["Agents"])


@router.get("", response_model=ApiResponse[list[AgentResponse]])
async def list_agents(_: dict = Depends(verify_token)):
    sb = get_supabase()
    result = sb.table("agents").select("*").eq("is_active", True).order("name").execute()
    return api_success(data=result.data, message="Agents retrieved successfully")


@router.post("", response_model=ApiResponse[AgentResponse], status_code=201)
async def create_agent(body: AgentCreate, _: dict = Depends(verify_token)):
    sb = get_supabase()
    try:
        result = sb.table("agents").insert(body.model_dump(exclude_none=True)).execute()
        return api_success(data=result.data[0], message="Agent created successfully", status_code=201)
    except Exception as e:
        if "unique" in str(e).lower():
            raise HTTPException(status_code=409, detail="Agent with this email already exists")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{agent_id}")
async def get_agent(agent_id: UUID, _: dict = Depends(verify_token)):
    """Get agent profile with lead and viewing stats."""
    sb = get_supabase()
    agent = sb.table("agents").select("*").eq("id", str(agent_id)).single().execute()
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
async def update_agent(agent_id: UUID, body: AgentUpdate, _: dict = Depends(verify_token)):
    sb = get_supabase()
    update_data = body.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
    result = sb.table("agents").update(update_data).eq("id", str(agent_id)).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Agent not found")
    return api_success(data=result.data[0], message="Agent updated successfully")


@router.delete("/{agent_id}")
async def deactivate_agent(agent_id: UUID, _: dict = Depends(verify_token)):
    """Soft delete — sets is_active=False."""
    sb = get_supabase()
    result = sb.table("agents").update({"is_active": False}).eq("id", str(agent_id)).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Agent not found")
    return api_success(data={"agent_id": str(agent_id)}, message="Agent deactivated successfully")
