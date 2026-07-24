"""
Leads Router
GET    /leads                  — list leads with filters
GET    /leads/stats            — overview stats
GET    /leads/{id}             — single lead + conversation
PATCH  /leads/{id}             — update lead
POST   /leads/{id}/handover    — trigger AI→agent handover
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional
from uuid import UUID
from database.supabase_client import get_supabase
from models.lead import LeadCreate, LeadUpdate, LeadResponse, LeadStats, HandoverRequest
from middleware.auth_middleware import verify_token
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/leads", tags=["Leads"])


@router.get("", response_model=list[LeadResponse])
async def list_leads(
    status: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    agent_id: Optional[UUID] = Query(None),
    is_ai_handling: Optional[bool] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    _: dict = Depends(verify_token),
):
    """List all leads with optional filters. Used by the Leads dashboard page."""
    sb = get_supabase()
    query = sb.table("leads").select("*").order("created_at", desc=True)

    if status:
        query = query.eq("status", status)
    if source:
        query = query.eq("source", source)
    if agent_id:
        query = query.eq("assigned_agent_id", str(agent_id))
    if is_ai_handling is not None:
        query = query.eq("is_ai_handling", is_ai_handling)
    if search:
        query = query.or_(f"name.ilike.%{search}%,phone.ilike.%{search}%,email.ilike.%{search}%")

    result = query.range(offset, offset + limit - 1).execute()
    return result.data


@router.get("/stats", response_model=LeadStats)
async def get_lead_stats(_: dict = Depends(verify_token)):
    """Aggregate stats for the Overview dashboard cards."""
    sb = get_supabase()
    leads = sb.table("leads").select("status, created_at").execute().data

    stats = {s: 0 for s in ["new", "qualifying", "viewing_booked", "viewing_done", "closed", "lost", "handover"]}
    for lead in leads:
        s = lead.get("status", "new")
        if s in stats:
            stats[s] += 1

    total = len(leads)
    viewings = sb.table("viewings").select("status").execute().data
    viewings_done = sum(1 for v in viewings if v["status"] == "completed")
    viewings_booked = sum(1 for v in viewings if v["status"] in ("scheduled", "confirmed", "completed"))

    lead_to_viewing = round((viewings_booked / total * 100), 1) if total > 0 else 0
    viewing_to_close = round((stats["closed"] / viewings_done * 100), 1) if viewings_done > 0 else 0

    return {
        "total": total,
        **stats,
        "avg_response_time_seconds": None,  # computed from conversations if needed
        "lead_to_viewing_pct": lead_to_viewing,
        "viewing_to_close_pct": viewing_to_close,
    }


@router.get("/{lead_id}")
async def get_lead(lead_id: UUID, _: dict = Depends(verify_token)):
    """Get a single lead with full conversation history and viewings."""
    sb = get_supabase()
    lead = sb.table("leads").select("*").eq("id", str(lead_id)).single().execute()
    if not lead.data:
        raise HTTPException(status_code=404, detail="Lead not found")

    conversations = (
        sb.table("conversations")
        .select("*")
        .eq("lead_id", str(lead_id))
        .order("timestamp", desc=False)
        .execute()
    ).data

    viewings = (
        sb.table("viewings")
        .select("*")
        .eq("lead_id", str(lead_id))
        .order("viewing_datetime", desc=False)
        .execute()
    ).data

    return {
        **lead.data,
        "conversations": conversations,
        "viewings": viewings,
    }


@router.patch("/{lead_id}", response_model=LeadResponse)
async def update_lead(lead_id: UUID, body: LeadUpdate, _: dict = Depends(verify_token)):
    """Update lead status, assigned agent, or qualification data."""
    sb = get_supabase()
    update_data = body.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Convert UUIDs to strings for Supabase
    if "assigned_agent_id" in update_data:
        update_data["assigned_agent_id"] = str(update_data["assigned_agent_id"])

    result = sb.table("leads").update(update_data).eq("id", str(lead_id)).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Lead not found")
    return result.data[0]


@router.post("/{lead_id}/handover")
async def trigger_handover(lead_id: UUID, body: HandoverRequest, _: dict = Depends(verify_token)):
    """Manually trigger AI-to-agent handover for a lead."""
    sb = get_supabase()

    update = {
        "is_ai_handling": False,
        "status": "handover",
        "handover_reason": body.reason,
    }
    if body.agent_id:
        update["assigned_agent_id"] = str(body.agent_id)

    result = sb.table("leads").update(update).eq("id", str(lead_id)).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Lead not found")

    logger.info(f"Manual handover triggered for lead {lead_id}: {body.reason}")
    return {"status": "success", "lead_id": str(lead_id), "handover_reason": body.reason}


@router.post("/{lead_id}/restore-ai")
async def restore_ai_handling(lead_id: UUID, _: dict = Depends(verify_token)):
    """Re-enable AI handling for a lead (undo handover)."""
    sb = get_supabase()
    result = sb.table("leads").update({
        "is_ai_handling": True,
        "status": "qualifying",
        "handover_reason": None,
    }).eq("id", str(lead_id)).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"status": "success", "message": "AI handling restored"}
