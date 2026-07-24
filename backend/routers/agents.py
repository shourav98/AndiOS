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
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agents", tags=["Agents"])


@router.get("", response_model=list[AgentResponse])
async def list_agents(_: dict = Depends(verify_token)):
    sb = get_supabase()
    result = sb.table("agents").select("*").eq("is_active", True).order("name").execute()
    return result.data


@router.post("", response_model=AgentResponse)
async def create_agent(body: AgentCreate, _: dict = Depends(verify_token)):
    sb = get_supabase()
    try:
        result = sb.table("agents").insert(body.model_dump(exclude_none=True)).execute()
        return result.data[0]
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
    return {**agent.data, "stats": stats}


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(agent_id: UUID, body: AgentUpdate, _: dict = Depends(verify_token)):
    sb = get_supabase()
    update_data = body.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
    result = sb.table("agents").update(update_data).eq("id", str(agent_id)).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Agent not found")
    return result.data[0]


@router.delete("/{agent_id}")
async def deactivate_agent(agent_id: UUID, _: dict = Depends(verify_token)):
    """Soft delete — sets is_active=False."""
    sb = get_supabase()
    result = sb.table("agents").update({"is_active": False}).eq("id", str(agent_id)).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"status": "deactivated", "agent_id": str(agent_id)}
