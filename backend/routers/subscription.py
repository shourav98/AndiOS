from fastapi import APIRouter, Depends, HTTPException
from database.supabase_client import get_supabase
from middleware.auth_middleware import verify_token
from utils.response import api_success
from pydantic import BaseModel
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/subscription", tags=["Subscription & Billing"])

class UpgradeRequest(BaseModel):
    plan_name: str

@router.get("/my-plan")
async def get_my_plan(current_user: dict = Depends(verify_token)):
    """Get the current agency's subscription plan details."""
    sb = get_supabase()
    agency_id = current_user.get("agency_id")
    role = current_user.get("role")
    
    if not agency_id:
        raise HTTPException(status_code=400, detail="User not associated with an agency")
        
    if role not in ["owner", "manager", "super_admin"]:
        raise HTTPException(status_code=403, detail="Forbidden: Only owners and managers can view subscription details")

    # Fetch agency details
    result = sb.table("agencies").select("name, subscription_status, created_at").eq("id", agency_id).single().execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Agency not found")
        
    agency = result.data
    status = agency.get("subscription_status", "trialing")
    
    # Mock plan details based on status
    plan_details = {
        "plan_name": "Starter Plan" if status == "trialing" else "Growth Plan",
        "status": status,
        "price": "$0 / month" if status == "trialing" else "$199 / month",
        "renewal_date": (datetime.utcnow() + timedelta(days=14)).strftime("%Y-%m-%d"),
        "features": [
            "Up to 5 agents",
            "WhatsApp Lead Qualification",
            "Google Calendar Integration",
            "Basic Reporting"
        ] if status == "trialing" else [
            "Up to 15 agents",
            "Everything in Starter",
            "AI Voice Calling Agent (Sami)",
            "Tenancy Contract Generation",
            "Custom Workflows"
        ],
        "usage": {
            "agents_used": 2,
            "agents_limit": 5 if status == "trialing" else 15,
            "ai_minutes_used": 120,
            "ai_minutes_limit": 500
        }
    }
    
    return api_success(data=plan_details, message="Subscription details fetched successfully")


@router.get("/invoices")
async def get_invoices(current_user: dict = Depends(verify_token)):
    """Get billing history and invoices (Mocked)."""
    agency_id = current_user.get("agency_id")
    role = current_user.get("role")
    
    if not agency_id:
        raise HTTPException(status_code=400, detail="User not associated with an agency")
        
    if role not in ["owner", "manager", "super_admin"]:
        raise HTTPException(status_code=403, detail="Forbidden: Only owners and managers can view invoices")

    # Mock invoices
    invoices = [
        {
            "id": "INV-1001",
            "date": (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d"),
            "amount": "$199.00",
            "status": "Paid",
            "download_url": "#"
        },
        {
            "id": "INV-1002",
            "date": datetime.utcnow().strftime("%Y-%m-%d"),
            "amount": "$199.00",
            "status": "Pending",
            "download_url": "#"
        }
    ]
    
    return api_success(data=invoices, message="Invoices fetched successfully")


@router.post("/upgrade")
async def upgrade_plan(request: UpgradeRequest, current_user: dict = Depends(verify_token)):
    """Upgrade or change the subscription plan."""
    sb = get_supabase()
    agency_id = current_user.get("agency_id")
    role = current_user.get("role")
    
    if not agency_id:
        raise HTTPException(status_code=400, detail="User not associated with an agency")
        
    if role != "owner":
        raise HTTPException(status_code=403, detail="Forbidden: Only owners can upgrade the plan")

    plan_name = request.plan_name.lower()
    if plan_name not in ["starter", "growth", "pro"]:
        raise HTTPException(status_code=400, detail="Invalid plan selected")
        
    new_status = "active" if plan_name != "starter" else "trialing"
    
    result = sb.table("agencies").update({"subscription_status": new_status}).eq("id", agency_id).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to upgrade plan")
        
    return api_success(data={"plan": plan_name, "status": new_status}, message=f"Successfully upgraded to {plan_name.title()} Plan")
