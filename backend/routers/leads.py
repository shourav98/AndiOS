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
from utils.response import api_success, ApiResponse
from utils.tenant import apply_lead_scope, verify_lead_access, require_agency_id, is_management_role, require_agent_id
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/leads", tags=["Leads"])


@router.get("", response_model=ApiResponse[list[dict]])
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
    current_user = _
    query = sb.table("leads").select("*, agents(name)").order("created_at", desc=True)
    query = apply_lead_scope(query, current_user)

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
    
    # Map to frontend expected schema
    formatted_leads = []
    for row in result.data:
        agent_data = row.get("agents") or {}
        agent_name = agent_data.get("name") if isinstance(agent_data, dict) else None
        agent_avatar = None
        
        # Determine property type from address/ref or default
        prop_type = "Apartment"
        if row.get("property_address") and "villa" in row["property_address"].lower():
            prop_type = "Villa"
            
        formatted_leads.append({
            "id": row["id"],
            "clientName": row["name"],
            "stage": row["status"],
            "propertyType": f"{row.get('bedrooms', 1)}BR {prop_type}",
            "location": row.get("property_address") or row.get("location_pref") or "Dubai",
            "value": {
                "amount": f"{row.get('budget_max', 0):,}",
                "type": row.get("purpose", "Rent")
            },
            "source": row["source"],
            "listing": row.get("property_ref", "Off-plan"),
            "agent": {
                "name": agent_name,
                "avatar": agent_avatar
            } if agent_name else None
        })

    return api_success(data=formatted_leads, message="Leads retrieved successfully")


@router.get("/stats", response_model=ApiResponse[LeadStats])
async def get_lead_stats(current_user: dict = Depends(verify_token)):
    """Aggregate stats for the Overview dashboard cards."""
    sb = get_supabase()
    agency_id = require_agency_id(current_user)

    leads_query = sb.table("leads").select("status, created_at").eq("agency_id", agency_id)
    if not is_management_role(current_user.get("role")):
        leads_query = leads_query.eq("assigned_agent_id", require_agent_id(current_user))
    leads = leads_query.execute().data

    stats = {s: 0 for s in ["new", "qualifying", "viewing_booked", "viewing_done", "closed", "lost", "handover"]}
    for lead in leads:
        s = lead.get("status", "new")
        if s in stats:
            stats[s] += 1

    total = len(leads)
    viewings_query = sb.table("viewings").select("status").eq("agency_id", agency_id)
    if not is_management_role(current_user.get("role")):
        viewings_query = viewings_query.eq("agent_id", require_agent_id(current_user))
    viewings = viewings_query.execute().data
    viewings_done = sum(1 for v in viewings if v["status"] == "completed")
    viewings_booked = sum(1 for v in viewings if v["status"] in ("scheduled", "confirmed", "completed"))

    lead_to_viewing = round((viewings_booked / total * 100), 1) if total > 0 else 0
    viewing_to_close = round((stats["closed"] / viewings_done * 100), 1) if viewings_done > 0 else 0

    return api_success(
        data={
            "total": total,
            **stats,
            "avg_response_time_seconds": None,  # computed from conversations if needed
            "lead_to_viewing_pct": lead_to_viewing,
            "viewing_to_close_pct": viewing_to_close,
        },
        message="Lead stats retrieved successfully"
    )


@router.get("/{lead_id}")
async def get_lead(lead_id: UUID, current_user: dict = Depends(verify_token)):
    """Get a single lead with full conversation history and viewings."""
    sb = get_supabase()
    lead_data = await verify_lead_access(str(lead_id), current_user)

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

    return api_success(
        data={
            **lead_data,
            "conversations": conversations,
            "viewings": viewings,
        },
        message="Lead details retrieved successfully"
    )


@router.patch("/{lead_id}", response_model=ApiResponse[LeadResponse])
async def update_lead(lead_id: UUID, body: LeadUpdate, current_user: dict = Depends(verify_token)):
    """Update lead status, assigned agent, or qualification data."""
    sb = get_supabase()
    await verify_lead_access(str(lead_id), current_user)
    update_data = body.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Convert UUIDs to strings for Supabase
    if "assigned_agent_id" in update_data:
        update_data["assigned_agent_id"] = str(update_data["assigned_agent_id"])

    result = sb.table("leads").update(update_data).eq("id", str(lead_id)).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Lead not found")
    return api_success(data=result.data[0], message="Lead updated successfully")


@router.post("/{lead_id}/handover")
async def trigger_handover(lead_id: UUID, body: HandoverRequest, current_user: dict = Depends(verify_token)):
    """Manually trigger AI-to-agent handover for a lead."""
    sb = get_supabase()
    await verify_lead_access(str(lead_id), current_user)

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
    return api_success(data={"lead_id": str(lead_id), "handover_reason": body.reason}, message="Manual handover triggered")


@router.post("/{lead_id}/restore-ai")
async def restore_ai_handling(lead_id: UUID, current_user: dict = Depends(verify_token)):
    """Re-enable AI handling for a lead (undo handover)."""
    sb = get_supabase()
    await verify_lead_access(str(lead_id), current_user)
    result = sb.table("leads").update({
        "is_ai_handling": True,
        "status": "qualifying",
        "handover_reason": None,
    }).eq("id", str(lead_id)).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Lead not found")
    return api_success(message="AI handling restored")
