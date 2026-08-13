from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from database.supabase_client import get_supabase
from middleware.auth_middleware import verify_token
from utils.response import api_success
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["Super Admin"])


def require_super_admin(current_user: dict = Depends(verify_token)):
    """Middleware to ensure the user is a super admin."""
    role = current_user.get("role")
    if role != "super_admin":
        raise HTTPException(status_code=403, detail="Forbidden: Super admin access required")
    return current_user


class AgencyCreate(BaseModel):
    name: str
    admin_email: str
    subscription_status: str = "trialing"
    subscription_plan: str = "starter"


class PlanUpdateRequest(BaseModel):
    plan: str  # starter, growth, pro


# ─── Dashboard Stats ──────────────────────────────────────────────────────────

@router.get("/stats")
async def get_platform_stats(current_user: dict = Depends(require_super_admin)):
    """Get platform-wide statistics for super admin dashboard."""
    sb = get_supabase()
    try:
        agencies = sb.table("agencies").select("id, subscription_status, subscription_plan, created_at").execute()
        agents_total = sb.table("agents").select("id", count="exact").eq("is_active", True).execute()
        leads_total = sb.table("leads").select("id", count="exact").execute()

        # Count by status
        status_counts = {}
        plan_counts = {}
        for a in agencies.data:
            s = a.get("subscription_status", "trialing")
            p = a.get("subscription_plan", "starter")
            status_counts[s] = status_counts.get(s, 0) + 1
            plan_counts[p] = plan_counts.get(p, 0) + 1

        total_agencies = len(agencies.data)
        agents_count = agents_total.count if hasattr(agents_total, "count") and agents_total.count is not None else len(agents_total.data)
        leads_count = leads_total.count if hasattr(leads_total, "count") and leads_total.count is not None else len(leads_total.data)

        return api_success(
            data={
                "total_agencies": total_agencies,
                "total_agents": agents_count,
                "total_leads": leads_count,
                "agencies_by_status": status_counts,
                "agencies_by_plan": plan_counts,
            },
            message="Platform stats retrieved",
        )
    except Exception as e:
        logger.error(f"Error fetching platform stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch stats")


# ─── Agency Management ────────────────────────────────────────────────────────

@router.get("/agencies")
async def list_agencies(
    status: Optional[str] = Query(None),
    plan: Optional[str] = Query(None),
    current_user: dict = Depends(require_super_admin),
):
    """List all agencies with optional filters."""
    sb = get_supabase()
    try:
        query = sb.table("agencies").select("*").order("created_at", desc=True)
        if status:
            query = query.eq("subscription_status", status)
        if plan:
            query = query.eq("subscription_plan", plan)
        result = query.execute()

        # Enrich with agent counts
        enriched = []
        for agency in result.data:
            agents_count = sb.table("agents").select("id", count="exact").eq("agency_id", agency["id"]).eq("is_active", True).execute()
            count = agents_count.count if hasattr(agents_count, "count") and agents_count.count is not None else len(agents_count.data)
            enriched.append({**agency, "agents_count": count})

        return api_success(data=enriched, message="Agencies retrieved successfully")
    except Exception as e:
        logger.error(f"Error fetching agencies: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch agencies")


@router.post("/agencies", status_code=201)
async def create_agency(agency: AgencyCreate, current_user: dict = Depends(require_super_admin)):
    """Create a new agency tenant."""
    sb = get_supabase()
    try:
        slug = agency.name.lower().replace(" ", "-").replace("_", "-")
        insert_data = {
            "name": agency.name,
            "slug": slug,
            "email": agency.admin_email,
            "subscription_status": agency.subscription_status,
            "subscription_plan": agency.subscription_plan,
            "is_active": True,
        }
        result = sb.table("agencies").insert(insert_data).execute()
        if not result.data:
            raise HTTPException(status_code=400, detail="Failed to create agency")
        return api_success(data=result.data[0], message="Agency created successfully", status_code=201)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating agency: {e}")
        raise HTTPException(status_code=500, detail="Failed to create agency")


@router.patch("/agencies/{agency_id}/status")
async def update_agency_status(
    agency_id: str,
    status: str = Query(...),
    current_user: dict = Depends(require_super_admin),
):
    """Update subscription status of an agency (active, suspended, trialing, cancelled)."""
    sb = get_supabase()
    try:
        result = sb.table("agencies").update({"subscription_status": status}).eq("id", agency_id).execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="Agency not found")
        return api_success(data=result.data[0], message=f"Agency status updated to {status}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating agency {agency_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update agency status")


@router.patch("/agencies/{agency_id}/plan")
async def update_agency_plan(
    agency_id: str,
    body: PlanUpdateRequest,
    current_user: dict = Depends(require_super_admin),
):
    """Upgrade or downgrade an agency's subscription plan."""
    sb = get_supabase()
    valid_plans = ["starter", "growth", "pro"]
    if body.plan.lower() not in valid_plans:
        raise HTTPException(status_code=400, detail=f"Invalid plan. Must be one of: {valid_plans}")

    try:
        result = sb.table("agencies").update({
            "subscription_plan": body.plan.lower(),
        }).eq("id", agency_id).execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="Agency not found")
        return api_success(data=result.data[0], message=f"Agency plan updated to {body.plan}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating agency plan {agency_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update plan")


@router.get("/agencies/{agency_id}")
async def get_agency_detail(agency_id: str, current_user: dict = Depends(require_super_admin)):
    """Get detailed info about a specific agency."""
    sb = get_supabase()
    try:
        agency = sb.table("agencies").select("*").eq("id", agency_id).single().execute()
        if not agency.data:
            raise HTTPException(status_code=404, detail="Agency not found")

        agents = sb.table("agents").select("id, name, email, role, is_active").eq("agency_id", agency_id).execute()
        leads_count = sb.table("leads").select("id", count="exact").eq("agency_id", agency_id).execute()
        lc = leads_count.count if hasattr(leads_count, "count") and leads_count.count is not None else len(leads_count.data)

        return api_success(
            data={
                **agency.data,
                "agents": agents.data,
                "total_leads": lc,
            },
            message="Agency details retrieved",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching agency {agency_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch agency")
