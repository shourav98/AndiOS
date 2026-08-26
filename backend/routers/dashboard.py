"""
Dashboard Router — Role-based overview API for Agents and Owners
GET /dashboard/overview
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from database.supabase_client import get_supabase
from middleware.auth_middleware import verify_token
from utils.response import api_success
from datetime import datetime, timedelta
from typing import Optional
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

@router.get("/overview")
async def get_dashboard_overview(
    branch_id: Optional[str] = Query(None),
    agent_id: Optional[str] = Query(None),
    timeframe: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    current_user: dict = Depends(verify_token)
):
    """
    Returns role-based dashboard metrics with filtering support.
    """
    sb = get_supabase()
    email = current_user.get("email")

    if not email:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    # 1. Fetch current agent profile to determine role and agency_id
    agent_res = sb.table("agents").select("*").eq("email", email).maybe_single().execute()
    agent = agent_res.data if (agent_res and hasattr(agent_res, "data")) else None
    if not agent:
        # Authenticated but no agent profile (e.g. partial registration):
        # a 4xx client error, never a 500.
        raise HTTPException(status_code=400, detail="No agent profile found for this user")

    agent = agent_res.data
    agency_id = agent.get("agency_id")
    current_agent_id = agent.get("id")
    role = agent.get("role")
    is_owner = role == "owner"

    # ── B-5: Branch filter ────────────────────────────────────────────────────
    # branch_id → agents in THIS agency's branch → their leads/viewings/contracts.
    # Scoped to the caller's agency: a foreign branch_id resolves to zero agents.
    branch_agent_ids = None
    if branch_id:
        branch_rows = (
            sb.table("agents")
            .select("id")
            .eq("agency_id", agency_id)
            .eq("branch", branch_id)
            .execute()
            .data or []
        )
        branch_agent_ids = [a["id"] for a in branch_rows]

    effective_agent_id = agent_id
    if branch_agent_ids is not None:
        if effective_agent_id:
            if effective_agent_id not in branch_agent_ids:
                # Agent exists but sits outside the requested branch → nothing matches
                branch_agent_ids = []
                effective_agent_id = None   # fall through to emptied in_() sentinel
        else:
            effective_agent_id = None       # branch list governs

    NULL_UUID = "00000000-0000-0000-0000-000000000000"

    def _scoped(q, column: str):
        """Apply agent/branch narrowing to a query builder (owner-side)."""
        if not is_owner:
            return q.eq(column, current_agent_id)
        if effective_agent_id:
            return q.eq(column, effective_agent_id)
        if branch_agent_ids is not None:
            return q.in_(column, branch_agent_ids or [NULL_UUID])
        return q

    now = datetime.utcnow()

    # Date calculations based on timeframe
    prev_start_date = None
    prev_end_date = None

    if timeframe:
        if timeframe == "today":
            start_date = now.strftime("%Y-%m-%d")
            prev_start_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
            prev_end_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        elif timeframe == "this_month":
            start_date = now.replace(day=1).strftime("%Y-%m-%d")
            prev_month_end = now.replace(day=1) - timedelta(days=1)
            prev_start_date = prev_month_end.replace(day=1).strftime("%Y-%m-%d")
            prev_end_date = prev_month_end.strftime("%Y-%m-%d")
        elif timeframe == "last_7_days":
            start_date = (now - timedelta(days=7)).strftime("%Y-%m-%d")
            prev_start_date = (now - timedelta(days=14)).strftime("%Y-%m-%d")
            prev_end_date = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        elif timeframe == "last_30_days":
            start_date = (now - timedelta(days=30)).strftime("%Y-%m-%d")
            prev_start_date = (now - timedelta(days=60)).strftime("%Y-%m-%d")
            prev_end_date = (now - timedelta(days=30)).strftime("%Y-%m-%d")
        elif timeframe == "this_quarter":
            quarter_month = (now.month - 1) // 3 * 3 + 1
            start_date = now.replace(month=quarter_month, day=1).strftime("%Y-%m-%d")
            prev_quarter_end = now.replace(month=quarter_month, day=1) - timedelta(days=1)
            prev_quarter_month = (prev_quarter_end.month - 1) // 3 * 3 + 1
            prev_start_date = prev_quarter_end.replace(month=prev_quarter_month, day=1).strftime("%Y-%m-%d")
            prev_end_date = prev_quarter_end.strftime("%Y-%m-%d")
    elif start_date and end_date:
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d")
            end = datetime.strptime(end_date, "%Y-%m-%d")
            diff = (end - start).days + 1
            prev_end_date = (start - timedelta(days=1)).strftime("%Y-%m-%d")
            prev_start_date = (start - timedelta(days=diff)).strftime("%Y-%m-%d")
        except:
            pass

    # 2. Query Leads (Current Period)
    leads_query = _scoped(
        sb.table("leads").select("*").eq("agency_id", agency_id),
        "assigned_agent_id",
    )
    if platform:
        leads_query = leads_query.ilike("source", f"%{platform}%")
    if start_date:
        leads_query = leads_query.gte("created_at", start_date)
    if end_date:
        leads_query = leads_query.lte("created_at", end_date)

    leads = leads_query.execute().data

    # 3. Query Viewings (Current Period)
    viewings_query = _scoped(
        sb.table("viewings").select("*, leads(name, source)").eq("agency_id", agency_id),
        "agent_id",
    )
    if start_date:
        viewings_query = viewings_query.gte("viewing_datetime", start_date)
    if end_date:
        viewings_query = viewings_query.lte("viewing_datetime", end_date)

    viewings = viewings_query.execute().data

    # 4. Query Contracts (Current Period)
    contracts_query = _scoped(
        sb.table("contracts").select("*").eq("agency_id", agency_id),
        "agent_id",
    )
    if start_date:
        contracts_query = contracts_query.gte("created_at", start_date)
    if end_date:
        contracts_query = contracts_query.lte("created_at", end_date)

    contracts = contracts_query.execute().data

    # 5. Query Previous Period
    if prev_start_date and prev_end_date:
        prev_leads_q = _scoped(
            sb.table("leads").select("*").eq("agency_id", agency_id),
            "assigned_agent_id",
        )
        if platform: prev_leads_q = prev_leads_q.ilike("source", f"%{platform}%")
        prev_leads_q = prev_leads_q.gte("created_at", prev_start_date).lte("created_at", prev_end_date)
        prev_leads = prev_leads_q.execute().data

        prev_viewings_q = _scoped(
            sb.table("viewings").select("*").eq("agency_id", agency_id),
            "agent_id",
        )
        prev_viewings_q = prev_viewings_q.gte("viewing_datetime", prev_start_date).lte("viewing_datetime", prev_end_date)
        prev_viewings = prev_viewings_q.execute().data
        
        p_total_leads = len(prev_leads)
        p_leads_with_viewings = sum(1 for l in prev_leads if l.get("status") in ("viewing_booked", "viewing_done", "closed"))
        p_viewing_completed = sum(1 for v in prev_viewings if v.get("status") == "completed")
        p_closed_leads = sum(1 for l in prev_leads if l.get("status") == "closed")
        
        prev_lead_to_viewing_pct = round((p_leads_with_viewings / p_total_leads * 100), 1) if p_total_leads > 0 else 0
        prev_viewing_to_close_pct = round((p_closed_leads / p_viewing_completed * 100), 1) if p_viewing_completed > 0 else 0
        prev_close_rate = round((p_closed_leads / p_total_leads * 100), 1) if p_total_leads > 0 else 0
    else:
        prev_lead_to_viewing_pct = 0
        prev_viewing_to_close_pct = 0
        prev_close_rate = 0

    # Calculate KPIs
    total_leads = len(leads)
    
    leads_with_viewings = sum(1 for l in leads if l.get("status") in ("viewing_booked", "viewing_done", "closed"))
    viewing_completed = sum(1 for v in viewings if v.get("status") == "completed")
    closed_leads = sum(1 for l in leads if l.get("status") == "closed")
    
    # B-1: "closed" = fee cheque attached (contract lifecycle final state).
    # Include it alongside signed/active so post-close deals are counted.
    closed_contracts = [c for c in contracts if c.get("status") in ("signed", "active", "closed")]
    closed_deals_count = len(closed_contracts)
    total_revenue = sum(float(c.get("rent_amount") or 0) for c in closed_contracts)
    agency_fees_earned = total_revenue * 0.05 # Assuming 5% agency fee

    lead_to_viewing_pct = round((leads_with_viewings / total_leads * 100), 1) if total_leads > 0 else 0
    viewing_to_close_pct = round((closed_leads / viewing_completed * 100), 1) if viewing_completed > 0 else 0
    close_rate = round((closed_leads / total_leads * 100), 1) if total_leads > 0 else 0
    
    def calc_trend(current, prev):
        if not prev_start_date: return {"trend": "up", "trend_value": "0pts"}
        diff = round(current - prev, 1)
        return {
            "trend": "up" if diff >= 0 else "down",
            "trend_value": f"{abs(diff)}pts"
        }
        
    ltv_trend = calc_trend(lead_to_viewing_pct, prev_lead_to_viewing_pct)
    vtc_trend = calc_trend(viewing_to_close_pct, prev_viewing_to_close_pct)
    cr_trend = calc_trend(close_rate, prev_close_rate)

    # Calculate AI handling percentage
    ai_handled_leads = sum(1 for l in leads if l.get("is_ai_handling") is True)
    ai_handled_pct = round((ai_handled_leads / total_leads * 100), 1) if total_leads > 0 else 0
    ai_handled_subtext = f"AI handles {int(ai_handled_pct)}%"

    # Fetch all agents for name mapping (if owner)
    all_agents_map = {}
    if is_owner:
        all_agents_query = sb.table("agents").select("id, name").eq("agency_id", agency_id).execute().data
        for a in all_agents_query:
            all_agents_map[a["id"]] = a["name"]
    else:
        all_agents_map[current_agent_id] = agent.get("name")

    # Group closed deals by agent
    agent_deals_dict = {}
    for c in closed_contracts:
        c_agent_id = c.get("agent_id")
        if c_agent_id not in agent_deals_dict:
            c_agent_name = all_agents_map.get(c_agent_id, "Unknown Agent")
            
            # Create initials
            name_parts = c_agent_name.split()
            initials = "".join([p[0].upper() for p in name_parts[:2]]) if name_parts else "UA"
            
            agent_deals_dict[c_agent_id] = {
                "id": c_agent_id,
                "firstName": name_parts[0] if name_parts else "Unknown",
                "lastName": " ".join(name_parts[1:]) if len(name_parts) > 1 else "",
                "initials": initials,
                "deals_count": 0,
                "revenue": 0
            }
        
        agent_deals_dict[c_agent_id]["deals_count"] += 1
        rent_amt = float(c.get("rent_amount") or 0)
        # Assuming fee is 5% of rent amount per deal for the agent stats
        agent_deals_dict[c_agent_id]["revenue"] += rent_amt * 0.05

    deals_by_agent = list(agent_deals_dict.values())

    # Live Leads (Most recent 5 active leads)
    active_leads = [l for l in leads if l.get("status") not in ("closed", "lost")]
    active_leads.sort(key=lambda x: x.get("updated_at") or x.get("created_at"), reverse=True)
    top_live_leads = active_leads[:5]

    for ll in top_live_leads:
        ll["agent_name"] = all_agents_map.get(ll.get("assigned_agent_id"), "Unknown")

    # Today's Viewings
    today_str = now.strftime("%Y-%m-%d")
    todays_viewings = [v for v in viewings if v.get("viewing_datetime") and v.get("viewing_datetime").startswith(today_str)]
    todays_viewings.sort(key=lambda x: x.get("viewing_datetime"))

    for v in todays_viewings:
        v["agent_name"] = all_agents_map.get(v.get("agent_id"), "Unknown")
        # B-2: expose the associated lead's name + platform source
        # (joined via viewings.lead_id → leads; agency-scoped by the viewing row)
        lead_info = v.get("leads") or {}
        v["lead_name"] = lead_info.get("name")
        v["lead_source"] = lead_info.get("source")

    # Funnel and AI Stats — computed from real call records (no fabricated data)
    funnel_data = [
        { "name": 'Leads', "count": total_leads, "percentage": 100 },
        { "name": 'Viewings', "count": leads_with_viewings, "percentage": lead_to_viewing_pct },
        { "name": 'Closings', "count": closed_leads, "percentage": close_rate }
    ]

    try:
        calls_q = sb.table("calls").select("status_value").eq("agency_id", agency_id)
        if start_date:
            calls_q = calls_q.gte("call_time", start_date)
        if end_date:
            calls_q = calls_q.lte("call_time", f"{end_date}T23:59:59")
        call_rows = calls_q.execute().data or []
    except Exception as e:
        logger.warning(f"Calls stats query failed: {e}")
        call_rows = []

    outbound_dials = len(call_rows)
    ANSWERED_VALUES = ("listing-won", "callback-booked", "interested", "not-interested")
    answered_dials = sum(1 for c in call_rows if c.get("status_value") in ANSWERED_VALUES)
    listings_won = sum(1 for c in call_rows if c.get("status_value") == "listing-won")
    answer_rate_pct = round((answered_dials / outbound_dials * 100), 1) if outbound_dials else 0
    calls_to_listings_pct = round((listings_won / answered_dials * 100), 1) if answered_dials else 0

    ai_agent_stats = {
        "outbound_dials": outbound_dials,
        "answer_rate": f"{answer_rate_pct}%",
        "calls_to_listings": f"{calls_to_listings_pct}%",
        "conversations": answered_dials,
        "new_listings_won": listings_won
    }

    return api_success(
        message="Dashboard metrics fetched successfully",
        data={
            "role": role,
            "metrics": {
                "avg_response_time": {
                    # Not yet measurable (requires message-timestamp analytics) — null, not fabricated
                    "value": None,
                    "subtext": ai_handled_subtext,
                    "trend": None,
                    "trend_value": None
                },
                "lead_to_viewing": {
                    "value": lead_to_viewing_pct,
                    "subtext": f"{leads_with_viewings} of {total_leads}",
                    "trend": ltv_trend["trend"],
                    "trend_value": ltv_trend["trend_value"]
                },
                "viewing_to_close": {
                    "value": viewing_to_close_pct,
                    "subtext": f"{closed_leads} of {viewing_completed}",
                    "trend": vtc_trend["trend"],
                    "trend_value": vtc_trend["trend_value"]
                },
                "close_rate": {
                    "value": close_rate,
                    "subtext": "overall, this qtr" if timeframe == "this_quarter" else (timeframe.replace("_", " ") if timeframe else "overall"),
                    "trend": cr_trend["trend"],
                    "trend_value": cr_trend["trend_value"]
                }
            },
            "closed_deals": {
                "count": closed_deals_count,
                "total_rent_value": total_revenue,
                "agency_fees_earned": agency_fees_earned,
                "deals_by_agent": deals_by_agent
            },
            "live_leads": top_live_leads,
            "todays_viewings": todays_viewings,
            "funnel": funnel_data,
            "ai_agent_stats": ai_agent_stats
        }
    )

@router.get("/calling-performance")
async def get_calling_performance(current_user: dict = Depends(verify_token)):
    """
    Returns metrics for the Calling Agent Dashboard — computed from real call
    records for the last 7 days. No mock data.
    """
    sb = get_supabase()
    agency_id = current_user.get("agency_id")

    if not agency_id:
        raise HTTPException(status_code=400, detail="User not associated with an agency")

    week_start = (datetime.utcnow() - timedelta(days=7)).isoformat()

    try:
        rows = (
            sb.table("calls")
            .select("id, owner_name, property_location, status, status_value, duration_seconds, audio_url, call_time")
            .eq("agency_id", agency_id)
            .gte("call_time", week_start)
            .order("call_time", desc=True)
            .limit(500)
            .execute()
            .data or []
        )
    except Exception as e:
        logger.error(f"Calling performance query failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to load calling performance")

    total_calls = len(rows)
    ANSWERED_VALUES = ("listing-won", "callback-booked", "interested", "not-interested")
    answered_rows = [r for r in rows if r.get("status_value") in ANSWERED_VALUES]
    answered = len(answered_rows)
    listings_won = sum(1 for r in rows if r.get("status_value") == "listing-won")
    callbacks_booked = sum(1 for r in rows if r.get("status_value") == "callback-booked")

    def pct(part: int, whole: int) -> str:
        return f"{round((part / whole * 100), 1)}%" if whole else "0%"

    def fmt_duration(seconds) -> str:
        try:
            s = int(seconds or 0)
            return f"{s // 60}:{s % 60:02d}"
        except (TypeError, ValueError):
            return "0:00"

    recent_calls = [
        {
            "id": r.get("id"),
            "time": (r.get("call_time") or "")[11:16],
            "hasAudio": bool(r.get("audio_url")),
            "name": r.get("owner_name") or "Unknown",
            "role": "Owner",
            "location": r.get("property_location") or "",
            "status": r.get("status") or "No answer",
            "status_value": r.get("status_value") or "no-answer",
            "duration": fmt_duration(r.get("duration_seconds")),
        }
        for r in rows[:5]
    ]

    data = {
        "period_start": week_start,
        "callsThisWeek": str(total_calls),
        "answerRate": pct(answered, total_calls),
        "callsToListings": pct(listings_won, answered),
        "callsToViewings": pct(callbacks_booked, total_calls),
        "recentCalls": recent_calls,
        "funnel": [
            { "name": 'Total Calls', "value": total_calls },
            { "name": 'Answered', "value": answered },
            { "name": 'Interested', "value": sum(1 for r in rows if r.get("status_value") in ("interested", "listing-won", "callback-booked")) },
            { "name": 'Listings Won', "value": listings_won },
        ]
    }

    return api_success(message="Calling performance fetched successfully", data=data)
