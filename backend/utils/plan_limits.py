"""
Plan Limits — enforce subscription plan constraints.
Used by routers that create billable resources (agents, campaigns, etc.).
"""
from fastapi import HTTPException
from database.supabase_client import get_supabase
import logging

logger = logging.getLogger(__name__)

# Plan definitions
PLAN_LIMITS = {
    "starter": {
        "max_agents": 5,
        "max_campaigns_per_month": 3,
        "ai_minutes": 500,
        "whatsapp_messages": 1000,
        "contracts": 10,
    },
    "growth": {
        "max_agents": 15,
        "max_campaigns_per_month": 10,
        "ai_minutes": 2000,
        "whatsapp_messages": 5000,
        "contracts": 50,
    },
    "pro": {
        "max_agents": 999,  # effectively unlimited
        "max_campaigns_per_month": 999,
        "ai_minutes": 10000,
        "whatsapp_messages": 50000,
        "contracts": 999,
    },
}


def _get_agency_plan(agency_id: str) -> str:
    """Get the subscription plan name for an agency."""
    sb = get_supabase()
    result = sb.table("agencies").select("subscription_plan, subscription_status").eq("id", agency_id).single().execute()
    if not result.data:
        return "starter"
    
    status = result.data.get("subscription_status", "trialing")
    if status in ("suspended", "cancelled"):
        raise HTTPException(
            status_code=403,
            detail="Your subscription is inactive. Please contact support or upgrade your plan.",
        )
    
    return (result.data.get("subscription_plan") or "starter").lower()


def get_plan_limits(agency_id: str) -> dict:
    """Get the plan limits for an agency."""
    plan = _get_agency_plan(agency_id)
    return {"plan": plan, **PLAN_LIMITS.get(plan, PLAN_LIMITS["starter"])}


def check_agent_limit(agency_id: str):
    """
    Check if the agency can add another agent.
    Raises 403 if limit reached.
    """
    sb = get_supabase()
    plan = _get_agency_plan(agency_id)
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["starter"])
    
    # Count current active agents
    agents_result = (
        sb.table("agents")
        .select("id", count="exact")
        .eq("agency_id", agency_id)
        .eq("is_active", True)
        .execute()
    )
    current_count = agents_result.count if hasattr(agents_result, "count") and agents_result.count is not None else len(agents_result.data)
    
    max_agents = limits["max_agents"]
    if current_count >= max_agents:
        raise HTTPException(
            status_code=403,
            detail=f"Agent limit reached ({current_count}/{max_agents}). "
                   f"Your {plan.title()} plan allows up to {max_agents} agents. "
                   f"Please upgrade your plan to add more agents.",
        )
    
    return {"current": current_count, "max": max_agents, "plan": plan}


def check_campaign_limit(agency_id: str):
    """Check if the agency can create another campaign this month."""
    sb = get_supabase()
    plan = _get_agency_plan(agency_id)
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["starter"])
    
    from datetime import datetime
    first_of_month = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0).isoformat()
    
    campaigns_result = (
        sb.table("call_campaigns")
        .select("id", count="exact")
        .eq("agency_id", agency_id)
        .gte("created_at", first_of_month)
        .execute()
    )
    current_count = campaigns_result.count if hasattr(campaigns_result, "count") and campaigns_result.count is not None else len(campaigns_result.data)
    
    max_campaigns = limits["max_campaigns_per_month"]
    if current_count >= max_campaigns:
        raise HTTPException(
            status_code=403,
            detail=f"Campaign limit reached ({current_count}/{max_campaigns} this month). "
                   f"Upgrade your plan for more campaigns.",
        )
    
    return {"current": current_count, "max": max_campaigns}
