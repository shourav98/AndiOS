"""
Leads Router
GET    /leads                  — list leads with filters
GET    /leads/stats            — overview stats
GET    /leads/{id}             — single lead + conversation
PATCH  /leads/{id}             — update lead
POST   /leads/{id}/handover    — trigger AI→agent handover
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from datetime import datetime, timedelta
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


@router.get("", response_model=ApiResponse[dict])
async def list_leads(
    status: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    agent_id: Optional[str] = Query(None),
    agent: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    branch: Optional[str] = Query(None),
    timeframe: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    is_ai_handling: Optional[bool] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    _: dict = Depends(verify_token),
):
    """List all leads with multi-level filters (status, platform, branch, agent, timeframe, search)."""
    sb = get_supabase()
    current_user = _
    agency_id = require_agency_id(current_user)

    # 1. Normalize Status filter
    target_status = None
    if status and isinstance(status, str) and status.strip().lower() not in ("all", "all status", "select status", ""):
        s_clean = status.strip().lower().replace(" ", "_")
        if s_clean in ("closed_won", "closed won", "closed"):
            target_status = "closed"
        elif s_clean in ("viewing_booked", "viewing booked"):
            target_status = "viewing_booked"
        else:
            target_status = s_clean

    # 2. Normalize Platform/Source filter
    effective_source = source if isinstance(source, str) else (platform if isinstance(platform, str) else None)
    target_source = None
    if effective_source and effective_source.strip().lower() not in ("all", "all platform", "select platform", ""):
        src_clean = effective_source.strip().lower().replace(" ", "_")
        target_source = src_clean

    # 3. Resolve Branch filter (branch_id or branch name)
    effective_branch = branch_id if isinstance(branch_id, str) else (branch if isinstance(branch, str) else None)
    branch_agent_ids = None
    if effective_branch and effective_branch.strip() not in ("All branches", "All", "all", ""):
        target_branch_val = effective_branch.strip()
        agency_res = sb.table("agencies").select("settings").eq("id", agency_id).maybe_single().execute()
        stored_branches = ((agency_res.data or {}).get("settings") or {}).get("branches") or []
        for b in stored_branches:
            if b.get("id") == target_branch_val:
                target_branch_val = b.get("name", target_branch_val)
                break

        branch_rows = (
            sb.table("agents")
            .select("id")
            .eq("agency_id", agency_id)
            .or_(f"branch.eq.{target_branch_val},branch.eq.{effective_branch.strip()}")
            .execute()
            .data or []
        )
        branch_agent_ids = [a["id"] for a in branch_rows]

    # 4. Resolve Agent filter (agent_id or agent name)
    raw_agent = agent_id if isinstance(agent_id, str) else (agent if isinstance(agent, str) else None)
    target_agent_id = None
    if raw_agent and raw_agent.strip() not in ("All agents", "All", "all", ""):
        clean_agent = raw_agent.strip()
        if len(clean_agent) == 36 and "-" in clean_agent:
            target_agent_id = clean_agent
        else:
            ag_res = sb.table("agents").select("id").eq("agency_id", agency_id).ilike("name", f"%{clean_agent}%").execute()
            if ag_res.data:
                target_agent_id = ag_res.data[0]["id"]

    # 5. Resolve Timeframe & Date Range
    effective_start_date = start_date if isinstance(start_date, str) else (from_date if isinstance(from_date, str) else None)
    effective_end_date = end_date if isinstance(end_date, str) else (to_date if isinstance(to_date, str) else None)
    now = datetime.utcnow()
    if timeframe and isinstance(timeframe, str) and timeframe.strip().lower() not in ("all", "all time", ""):
        tf = timeframe.strip().lower().replace(" ", "_")
        if tf == "today":
            effective_start_date = now.strftime("%Y-%m-%d")
            effective_end_date = now.strftime("%Y-%m-%d")
        elif tf == "last_7_days":
            effective_start_date = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        elif tf == "this_month":
            effective_start_date = now.replace(day=1).strftime("%Y-%m-%d")
        elif tf == "last_30_days":
            effective_start_date = (now - timedelta(days=30)).strftime("%Y-%m-%d")
        elif tf == "this_quarter":
            quarter_month = ((now.month - 1) // 3) * 3 + 1
            effective_start_date = now.replace(month=quarter_month, day=1).strftime("%Y-%m-%d")

    # Searchable fields
    SEARCHABLE_FIELDS = (
        "name", "phone", "email", "external_lead_id",
        "property_ref", "property_address", "location_pref",
    )

    def _apply_filters(q):
        q = apply_lead_scope(q, current_user)          # agency + agent scoping
        if target_status:
            if target_status == "closed":
                q = q.or_("status.eq.closed,status.eq.closed_won")
            else:
                q = q.eq("status", target_status)
        if target_source:
            q = q.eq("source", target_source)
        if target_agent_id:
            q = q.eq("assigned_agent_id", target_agent_id)
        elif branch_agent_ids is not None:
            NULL_UUID = "00000000-0000-0000-0000-000000000000"
            q = q.in_("assigned_agent_id", branch_agent_ids or [NULL_UUID])
        if effective_start_date:
            q = q.gte("created_at", f"{effective_start_date}T00:00:00")
        if effective_end_date:
            q = q.lte("created_at", f"{effective_end_date}T23:59:59")
        if is_ai_handling is not None and isinstance(is_ai_handling, bool):
            q = q.eq("is_ai_handling", is_ai_handling)
        if search and isinstance(search, str):
            clean = search.replace(",", "").replace("(", "").replace(")", "")
            clean = clean.replace("%", "").replace("\\", "").strip()
            if clean:
                conds = ",".join(f"{f}.ilike.%{clean}%" for f in SEARCHABLE_FIELDS)
                q = q.or_(conds)
        return q

    lim = limit if isinstance(limit, int) else 50
    off = offset if isinstance(offset, int) else 0

    result = (
        _apply_filters(sb.table("leads").select("*, agents(name)"))
        .order("created_at", desc=True)
        .range(off, off + lim - 1)
        .execute()
    )
    total = getattr(result, "count", None)

    # Exact total for pagination — same filters as the data query
    if total is None:
        try:
            total = _apply_filters(sb.table("leads").select("id", count="exact")).execute().count
        except Exception as e:
            logger.warning(f"Lead count query failed: {e}")
            total = len(result.data)
    
    # Source display name mapping (lowercase DB value → UI display)
    SOURCE_LABELS = {
        "property_finder": "Property Finder",
        "whatsapp": "WhatsApp",
        "bayut": "Bayut",
        "dubizzle": "Dubizzle",
        "instagram": "Instagram",
        "referral": "Referral",
    }

    # Map to frontend expected schema
    formatted_leads = []
    for row in result.data:
        agent_data = row.get("agents") or {}
        agent_name = agent_data.get("name") if isinstance(agent_data, dict) else None

        # Property details: "{bedrooms}BR - {area}" e.g. "2BR - Dubai Marina"
        bedrooms = row.get("bedrooms")
        # Use location_pref (area name only) to avoid duplicate bedroom prefix in property_address
        location = row.get("location_pref") or row.get("property_address") or "Dubai"
        if bedrooms:
            property_details = f"{bedrooms}BR - {location}"
        else:
            property_details = location

        # Deal value formatting: e.g. "AED 3.2M" for sale, "AED 110k/yr" for rent
        budget = row.get("budget_max") or row.get("budget_min") or 0
        purpose = (row.get("purpose") or "rent").lower()
        if budget >= 1_000_000:
            amount_str = f"AED {budget / 1_000_000:.1f}M"
        elif budget >= 1_000:
            amount_str = f"AED {int(budget / 1_000)}k"
        else:
            amount_str = f"AED {int(budget):,}" if budget else "—"
        if purpose == "rent" and budget:
            amount_str += "/yr"

        # Source display label
        raw_source = (row.get("source") or "").lower()
        source_label = SOURCE_LABELS.get(raw_source, raw_source.replace("_", " ").title())

        # Listing link text: "{Source} listing" e.g. "Property Finder listing"
        listing_label = f"{source_label} listing" if source_label else "Listing"

        formatted_leads.append({
            "id": row["id"],
            "clientName": row["name"],
            "stage": row["status"],
            "propertyDetails": property_details,
            "dealValue": amount_str,
            "source": source_label,
            "listingLink": listing_label,
            "listingRef": row.get("property_ref", ""),
            "agent": {
                "name": agent_name,
            } if agent_name else None,
            # Extra fields for filtering support
            "sourceRaw": raw_source,
            "purpose": purpose,
            "created_at": row.get("created_at"),
        })

    return api_success(
        data={
            "leads": formatted_leads,
            "total": total if total is not None else len(formatted_leads),
            "limit": limit,
            "offset": offset,
        },
        message="Leads retrieved successfully",
    )


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

    # ── Smart AI Restore: if re-enabling AI handling, reset status from 'handover' ──
    lead = result.data[0]
    if update_data.get("is_ai_handling") is True and lead.get("status") == "handover":
        restore_result = sb.table("leads").update({
            "status": "qualifying",
            "handover_reason": None,
        }).eq("id", str(lead_id)).execute()
        if restore_result.data:
            lead = restore_result.data[0]
        logger.info(f"Lead {lead_id}: AI handling restored, status reset to 'qualifying'")

    return api_success(data=lead, message="Lead updated successfully")


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


@router.post("", status_code=201)
async def create_lead(lead: LeadCreate, current_user: dict = Depends(verify_token)):
    """Manually create a new lead."""
    sb = get_supabase()
    agency_id = require_agency_id(current_user)

    # mode="json": serialize UUID/datetime/enum fields to JSON-safe values
    # (raw UUID objects are not JSON-serializable for the PostgREST payload)
    lead_data = lead.model_dump(mode="json", exclude_unset=True)
    lead_data["agency_id"] = agency_id

    # Assignment target must belong to THIS agency (no cross-tenant assignment)
    if lead_data.get("assigned_agent_id"):
        agent_check = (
            sb.table("agents")
            .select("id")
            .eq("id", lead_data["assigned_agent_id"])
            .eq("agency_id", agency_id)
            .execute()
        )
        if not agent_check.data:
            raise HTTPException(
                status_code=400,
                detail="assigned_agent_id does not belong to your agency",
            )

    # Handle the status parameter if passed in the payload for testing, otherwise default to new
    if "status" not in lead_data:
        lead_data["status"] = "new"

    result = sb.table("leads").insert(lead_data).execute()
    if not result.data:
        raise HTTPException(status_code=400, detail="Failed to create lead")

    return api_success(data=result.data[0], message="Lead created successfully", status_code=201)
