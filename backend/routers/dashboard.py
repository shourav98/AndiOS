"""
Dashboard Router — Role-based overview API for Agents and Owners
GET /dashboard/overview
"""
from fastapi import APIRouter, Depends, HTTPException
from database.supabase_client import get_supabase
from middleware.auth_middleware import verify_token
from utils.response import api_success
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

@router.get("/overview")
async def get_dashboard_overview(current_user: dict = Depends(verify_token)):
    """
    Returns role-based dashboard metrics.
    If the user is an owner, returns stats for the entire agency.
    If the user is an agent, returns stats only for that specific agent.
    """
    sb = get_supabase()
    email = current_user.get("email")

    if not email:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    # 1. Fetch current agent profile to determine role and agency_id
    agent_res = sb.table("agents").select("*").eq("email", email).single().execute()
    if not agent_res.data:
        raise HTTPException(status_code=404, detail="Agent profile not found")

    agent = agent_res.data
    agency_id = agent.get("agency_id")
    agent_id = agent.get("id")
    role = agent.get("role")  # agent, senior_agent, manager, owner
    is_owner = role == "owner"

    # Define common filters
    now = datetime.utcnow()

    # 2. Query Leads
    leads_query = sb.table("leads").select("*").eq("agency_id", agency_id)
    if not is_owner:
        leads_query = leads_query.eq("assigned_agent_id", agent_id)
    leads = leads_query.execute().data

    # 3. Query Viewings
    viewings_query = sb.table("viewings").select("*").eq("agency_id", agency_id)
    if not is_owner:
        viewings_query = viewings_query.eq("agent_id", agent_id)
    viewings = viewings_query.execute().data

    # 4. Query Contracts (for Closed Deals & Revenue)
    contracts_query = sb.table("contracts").select("*").eq("agency_id", agency_id)
    if not is_owner:
        contracts_query = contracts_query.eq("agent_id", agent_id)
    contracts = contracts_query.execute().data

    # Calculate KPIs
    total_leads = len(leads)
    
    # Viewings booked / done / closed implies they had a viewing
    leads_with_viewings = sum(1 for l in leads if l.get("status") in ("viewing_booked", "viewing_done", "closed"))
    viewing_completed = sum(1 for v in viewings if v.get("status") == "completed")
    
    closed_leads = sum(1 for l in leads if l.get("status") == "closed")
    
    # Closed Deals from contracts (where status is signed/active)
    closed_contracts = [c for c in contracts if c.get("status") in ("signed", "active")]
    closed_deals_count = len(closed_contracts)
    
    # Total Revenue (sum of rent_amount from closed contracts)
    # Using rent_amount as a proxy for revenue/deal size for now
    total_revenue = sum(float(c.get("rent_amount") or 0) for c in closed_contracts)

    # Conversion Rates
    lead_to_viewing_pct = round((leads_with_viewings / total_leads * 100), 1) if total_leads > 0 else 0
    viewing_to_close_pct = round((closed_leads / viewing_completed * 100), 1) if viewing_completed > 0 else 0
    close_rate = round((closed_leads / total_leads * 100), 1) if total_leads > 0 else 0

    # Live Leads (Most recent 5 active leads)
    # Sort locally since we already fetched all leads
    active_leads = [l for l in leads if l.get("status") not in ("closed", "lost")]
    active_leads.sort(key=lambda x: x.get("updated_at") or x.get("created_at"), reverse=True)
    top_live_leads = active_leads[:5]

    # Map assigned agent names for Live Leads
    if is_owner:
        all_agents = sb.table("agents").select("id, name").eq("agency_id", agency_id).execute().data
        agent_map = {a["id"]: a["name"] for a in all_agents}
        for ll in top_live_leads:
            ll["agent_name"] = agent_map.get(ll.get("assigned_agent_id"), "Unknown")
    else:
        for ll in top_live_leads:
            ll["agent_name"] = agent.get("name")

    # Today's Viewings
    today_str = now.strftime("%Y-%m-%d")
    todays_viewings = [v for v in viewings if v.get("viewing_datetime") and v.get("viewing_datetime").startswith(today_str)]
    todays_viewings.sort(key=lambda x: x.get("viewing_datetime"))

    # Map agent names for Today's Viewings
    if is_owner:
        for v in todays_viewings:
            v["agent_name"] = agent_map.get(v.get("agent_id"), "Unknown")
    else:
        for v in todays_viewings:
            v["agent_name"] = agent.get("name")

    return api_success(
        message="Dashboard metrics fetched successfully",
        data={
            "role": role,
            "metrics": {
                "avg_response_time": "1m 12s", # Placeholder
                "lead_to_viewing_pct": lead_to_viewing_pct,
                "viewing_to_close_pct": viewing_to_close_pct,
                "close_rate": close_rate,
                "closed_deals_count": closed_deals_count,
                "total_revenue": total_revenue
            },
            "live_leads": top_live_leads,
            "todays_viewings": todays_viewings
        }
    )
