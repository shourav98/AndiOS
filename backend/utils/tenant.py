"""
Multi-tenant helpers — agency isolation and role-based access control.
Used by all protected API routes (backend uses service_role, so we enforce tenancy here).
"""
from fastapi import HTTPException, Depends
from database.supabase_client import get_supabase
from middleware.auth_middleware import verify_token

MANAGEMENT_ROLES = {"owner", "manager"}


def is_management_role(role: str | None) -> bool:
    return role in MANAGEMENT_ROLES


def require_agency_id(current_user: dict) -> str:
    agency_id = current_user.get("agency_id")
    if not agency_id:
        raise HTTPException(status_code=400, detail="User is not associated with any agency")
    return agency_id


def require_agent_id(current_user: dict) -> str:
    agent_id = current_user.get("agent_id")
    if not agent_id:
        raise HTTPException(status_code=400, detail="Agent profile not found")
    return agent_id


def apply_lead_scope(query, current_user: dict):
    """Filter leads: all agency data for owner/manager, own leads for agents."""
    query = query.eq("agency_id", require_agency_id(current_user))
    if not is_management_role(current_user.get("role")):
        query = query.eq("assigned_agent_id", require_agent_id(current_user))
    return query


def apply_viewing_scope(query, current_user: dict):
    """Filter viewings by agency and optionally by agent."""
    query = query.eq("agency_id", require_agency_id(current_user))
    if not is_management_role(current_user.get("role")):
        query = query.eq("agent_id", require_agent_id(current_user))
    return query


def apply_agency_scope(query, current_user: dict):
    """Filter any table that has agency_id."""
    return query.eq("agency_id", require_agency_id(current_user))


async def verify_lead_access(lead_id: str, current_user: dict) -> dict:
    """Fetch a lead and verify the current user may access it."""
    sb = get_supabase()
    result = (
        sb.table("leads")
        .select("*")
        .eq("id", lead_id)
        .eq("agency_id", require_agency_id(current_user))
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Lead not found")

    lead = result.data[0]
    if not is_management_role(current_user.get("role")):
        if lead.get("assigned_agent_id") != current_user.get("agent_id"):
            raise HTTPException(status_code=403, detail="Access denied")
    return lead


async def verify_viewing_access(viewing_id: str, current_user: dict) -> dict:
    """Fetch a viewing and verify the current user may access it."""
    sb = get_supabase()
    result = (
        sb.table("viewings")
        .select("*")
        .eq("id", viewing_id)
        .eq("agency_id", require_agency_id(current_user))
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Viewing not found")

    viewing = result.data[0]
    if not is_management_role(current_user.get("role")):
        if viewing.get("agent_id") != current_user.get("agent_id"):
            raise HTTPException(status_code=403, detail="Access denied")
    return viewing
