"""
Reports Router — Owner Reports page
GET  /reports/owner                 — list all generated reports
POST /reports/owner/generate        — generate a new AI owner report
GET  /reports/owner/{id}            — get specific report
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from uuid import UUID
from datetime import datetime, timedelta
from database.supabase_client import get_supabase
from services.ai_service import generate_owner_report
from middleware.auth_middleware import verify_token, get_current_user_id
from utils.response import api_success
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/reports", tags=["Reports"])


@router.get("/owner")
async def list_reports(_: dict = Depends(verify_token)):
    """List all AI-generated owner reports."""
    sb = get_supabase()
    result = (
        sb.table("owner_reports")
        .select("id, generated_at, period_start, period_end, total_leads, closed_deals, total_revenue_aed")
        .order("generated_at", desc=True)
        .limit(50)
        .execute()
    )
    return api_success(data=result.data, message="Reports retrieved successfully")


@router.post("/owner/generate")
async def generate_report(
    period_days: int = Query(30, description="Number of days to cover in the report"),
    user_id: str = Depends(get_current_user_id),
):
    """
    Generate an AI-powered owner report.
    Pulls all metrics from Supabase and runs them through GPT-4o.
    """
    sb = get_supabase()
    now = datetime.utcnow()
    period_start = now - timedelta(days=period_days)

    # ── Gather all metrics ──
    leads = sb.table("leads").select("*").gte("created_at", period_start.isoformat()).execute().data
    viewings = sb.table("viewings").select("*").gte("created_at", period_start.isoformat()).execute().data
    agents = sb.table("agents").select("id, name").eq("is_active", True).execute().data

    total_leads = len(leads)
    new_leads = sum(1 for l in leads if l["status"] == "new")
    qualified = sum(1 for l in leads if l["status"] not in ("new", "lost"))
    viewings_booked = sum(1 for l in leads if l["status"] in ("viewing_booked", "viewing_done", "closed"))
    viewings_completed = sum(1 for v in viewings if v["status"] == "completed")
    closed = sum(1 for l in leads if l["status"] == "closed")
    lost = sum(1 for l in leads if l["status"] == "lost")

    # Agent breakdown
    agent_map = {a["id"]: a["name"] for a in agents}
    agent_stats = {}
    for lead in leads:
        aid = lead.get("assigned_agent_id")
        if aid:
            name = agent_map.get(aid, "Unknown")
            if name not in agent_stats:
                agent_stats[name] = {"leads": 0, "closed": 0, "viewings": 0}
            agent_stats[name]["leads"] += 1
            if lead["status"] == "closed":
                agent_stats[name]["closed"] += 1

    for viewing in viewings:
        aid = viewing.get("agent_id")
        if aid:
            name = agent_map.get(aid, "Unknown")
            if name in agent_stats:
                agent_stats[name]["viewings"] += 1

    lead_to_viewing = round((viewings_booked / total_leads * 100), 1) if total_leads > 0 else 0
    viewing_to_close = round((closed / viewings_completed * 100), 1) if viewings_completed > 0 else 0

    report_data = {
        "period_start": period_start.isoformat(),
        "period_end": now.isoformat(),
        "period_days": period_days,
        "total_leads": total_leads,
        "new_leads": new_leads,
        "qualified_leads": qualified,
        "viewings_booked": viewings_booked,
        "viewings_completed": viewings_completed,
        "closed_deals": closed,
        "lost_leads": lost,
        "lead_to_viewing_pct": lead_to_viewing,
        "viewing_to_close_pct": viewing_to_close,
        "agent_performance": agent_stats,
        "sources": {
            source: sum(1 for l in leads if l["source"] == source)
            for source in ["property_finder", "bayut", "dubizzle", "direct", "referral"]
        },
    }

    # ── Generate AI narrative ──
    ai_narrative = await generate_owner_report(report_data)

    # ── Store report ──
    stored = sb.table("owner_reports").insert({
        "period_start": period_start.isoformat(),
        "period_end": now.isoformat(),
        "total_leads": total_leads,
        "new_leads": new_leads,
        "qualified_leads": qualified,
        "viewings_booked": viewings_booked,
        "viewings_completed": viewings_completed,
        "closed_deals": closed,
        "lost_leads": lost,
        "lead_to_viewing_pct": lead_to_viewing,
        "viewing_to_close_pct": viewing_to_close,
        "ai_narrative": ai_narrative,
        "report_json": report_data,
        "generated_by": user_id,
    }).execute()

    logger.info(f"Owner report generated for {period_days}-day period")
    return api_success(data={**stored.data[0], "ai_narrative": ai_narrative}, message="Report generated successfully")


@router.get("/owner/{report_id}")
async def get_report(report_id: UUID, _: dict = Depends(verify_token)):
    """Get a specific owner report by ID."""
    sb = get_supabase()
    result = sb.table("owner_reports").select("*").eq("id", str(report_id)).single().execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Report not found")
    return api_success(data=result.data, message="Report retrieved successfully")
