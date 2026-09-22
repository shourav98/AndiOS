"""
Agents Router — Team management page
GET    /agents          — list all agents
POST   /agents          — add new agent (checks plan limit)
POST   /agents/invite   — invite agent via email
GET    /agents/{id}     — single agent with stats
PATCH  /agents/{id}     — update agent
DELETE /agents/{id}     — deactivate agent
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from uuid import UUID
from typing import Optional
from pydantic import BaseModel, EmailStr
from database.supabase_client import get_supabase
from models.agent import AgentCreate, AgentUpdate, AgentResponse
from middleware.auth_middleware import verify_token
from utils.response import api_success, ApiResponse
from utils.tenant import require_agency_id, is_management_role
from utils.plan_limits import check_agent_limit, get_plan_limits
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agents", tags=["Agents"])


class InviteAgentRequest(BaseModel):
    email: EmailStr
    name: str
    role: str = "agent"  # 'agent' or 'manager'
    branch: Optional[str] = None


def validate_and_normalize_branch(sb, agency_id: str, branch_val: Optional[str]) -> Optional[str]:
    """
    Validates that branch_val belongs to one of the agency's created branches (by id or name).
    Returns the canonical branch name.
    Raises HTTPException(400) if branch_val is invalid.
    """
    if not branch_val or not str(branch_val).strip() or str(branch_val).strip() in ("All branches", "All", "none", "None", ""):
        return None

    clean_val = str(branch_val).strip()

    # 1. Fetch agency's valid branches from settings
    agency_res = sb.table("agencies").select("settings").eq("id", agency_id).maybe_single().execute()
    stored_branches = ((agency_res.data or {}).get("settings") or {}).get("branches") or []

    # Check against stored branches
    for b in stored_branches:
        if b.get("id") == clean_val or b.get("name", "").strip().lower() == clean_val.lower():
            return b.get("name", clean_val).strip()

    # 2. Also check against distinct branches already assigned to active agents in DB
    agents_res = sb.table("agents").select("branch").eq("agency_id", agency_id).eq("is_active", True).execute()
    existing_agent_branches = {row["branch"].strip() for row in (agents_res.data or []) if row.get("branch") and str(row.get("branch")).strip()}
    for eb in existing_agent_branches:
        import uuid
        auto_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{agency_id}-{eb}"))
        if auto_id == clean_val or eb.lower() == clean_val.lower():
            return eb

    # If not found in any valid branch
    valid_branch_names = sorted(list({b.get("name") for b in stored_branches if b.get("name")} | existing_agent_branches))
    valid_str = ", ".join(f"'{name}'" for name in valid_branch_names) if valid_branch_names else "None (Please create a branch first)"
    raise HTTPException(
        status_code=400,
        detail=f"Invalid branch '{clean_val}'. Allowed branches for your agency: {valid_str}"
    )


def _populate_agent_branch_info(sb, agency_id: str, agent_rows: list[dict]) -> list[dict]:
    """
    Ensures that for every agent:
    - 'branch' is the human-readable branch name
    - 'branch_id' is the unique branch UUID
    """
    if not agent_rows:
        return []

    agency_res = sb.table("agencies").select("settings").eq("id", agency_id).maybe_single().execute()
    stored_branches = ((agency_res.data or {}).get("settings") or {}).get("branches") or []

    id_to_name = {}
    name_to_id = {}
    for b in stored_branches:
        b_id = b.get("id")
        b_name = (b.get("name") or "").strip()
        if b_id and b_name:
            id_to_name[b_id] = b_name
            name_to_id[b_name.lower()] = b_id

    for row in agent_rows:
        raw_b = (row.get("branch") or "").strip()
        if not raw_b:
            row["branch"] = None
            row["branch_id"] = None
            continue

        if raw_b in id_to_name:
            row["branch"] = id_to_name[raw_b]
            row["branch_id"] = raw_b
        elif raw_b.lower() in name_to_id:
            row["branch"] = raw_b
            row["branch_id"] = name_to_id[raw_b.lower()]
        else:
            import uuid
            row["branch"] = raw_b
            row["branch_id"] = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{agency_id}-{raw_b}"))

    return agent_rows


@router.get("", response_model=ApiResponse[list[AgentResponse]])
async def list_agents(
    search: Optional[str] = Query(None),
    branch: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    limit: int = Query(50, le=100),
    offset: int = Query(0),
    current_user: dict = Depends(verify_token),
):
    sb = get_supabase()
    agency_id = require_agency_id(current_user)

    query = sb.table("agents").select("*").eq("agency_id", agency_id).eq("is_active", True)

    if search and isinstance(search, str):
        clean = search.replace(",", "").replace("(", "").replace(")", "").replace("%", "").strip()
        if clean:
            query = query.or_(f"name.ilike.%{clean}%,email.ilike.%{clean}%")
    if branch and isinstance(branch, str) and branch not in ("All branches", "All", "all", ""):
        # Match both branch name and branch id
        try:
            norm_b = validate_and_normalize_branch(sb, agency_id, branch) or branch
            query = query.or_(f"branch.eq.{norm_b},branch.eq.{branch}")
        except Exception:
            query = query.eq("branch", branch)
    if role and isinstance(role, str) and role not in ("All agents", "All", "all", ""):
        query = query.eq("role", role)

    lim = limit if isinstance(limit, int) else 50
    off = offset if isinstance(offset, int) else 0
    result = query.order("name").range(off, off + lim - 1).execute()
    formatted_agents = _populate_agent_branch_info(sb, agency_id, result.data or [])
    return api_success(data=formatted_agents, message="Agents retrieved successfully")


@router.get("/branches")
async def list_branches_endpoint(current_user: dict = Depends(verify_token)):
    """List all distinct branches configured for the current agency's team with unique IDs."""
    from routers.branches import list_branches as get_all_branches
    return await get_all_branches(current_user=current_user)


@router.post("/branches", status_code=201)
async def create_branch_endpoint(body: dict, current_user: dict = Depends(verify_token)):
    """Create a new branch from Team page."""
    from routers.branches import create_branch as add_new_branch, BranchCreate
    branch_create = BranchCreate(**body)
    return await add_new_branch(body=branch_create, current_user=current_user)


@router.post("", response_model=ApiResponse[AgentResponse], status_code=201)
async def create_agent(body: AgentCreate, current_user: dict = Depends(verify_token)):
    """Add a new agent — checks plan limits and validates branch before creating."""
    sb = get_supabase()
    agency_id = require_agency_id(current_user)

    # Only owners and managers can add agents
    if not is_management_role(current_user.get("role")):
        raise HTTPException(status_code=403, detail="Only owners and managers can add agents")

    # Check plan limits
    check_agent_limit(agency_id)

    try:
        agent_data = body.model_dump(exclude_none=True)
        agent_data["agency_id"] = agency_id
        if "branch" in agent_data and agent_data["branch"]:
            agent_data["branch"] = validate_and_normalize_branch(sb, agency_id, agent_data["branch"])

        result = sb.table("agents").insert(agent_data).execute()
        created_agent = _populate_agent_branch_info(sb, agency_id, result.data)[0]
        return api_success(data=created_agent, message="Agent created successfully", status_code=201)
    except HTTPException:
        raise
    except Exception as e:
        if "unique" in str(e).lower():
            raise HTTPException(status_code=409, detail="Agent with this email already exists")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/invite", status_code=201)
async def invite_agent(body: InviteAgentRequest, current_user: dict = Depends(verify_token)):
    """
    Invite a new agent via email.
    1. Checks plan limits
    2. Validates branch is an allowed agency branch
    3. Creates agent row in agents table (is_active=True)
    4. Sends Supabase invite email — agent sets password on first login
    5. Syncs app_metadata so the invited user gets correct agency_id/role
    """
    sb = get_supabase()
    agency_id = require_agency_id(current_user)

    if not is_management_role(current_user.get("role")):
        raise HTTPException(status_code=403, detail="Only owners and managers can invite agents")

    # Check plan limits
    check_agent_limit(agency_id)

    # Validate branch if provided
    norm_branch = None
    if body.branch:
        norm_branch = validate_and_normalize_branch(sb, agency_id, body.branch)

    # Check if agent with this email already exists
    existing = sb.table("agents").select("id").eq("email", body.email).eq("agency_id", agency_id).execute()
    if existing.data:
        raise HTTPException(status_code=409, detail="Agent with this email already exists in your agency")

    try:
        # Step 1: Create agent row
        insert_payload = {
            "name": body.name,
            "email": body.email,
            "role": body.role,
            "agency_id": agency_id,
            "is_active": True,
        }
        if norm_branch:
            insert_payload["branch"] = norm_branch

        agent_result = sb.table("agents").insert(insert_payload).execute()

        if not agent_result.data:
            raise HTTPException(status_code=500, detail="Failed to create agent record")

        agent_formatted = _populate_agent_branch_info(sb, agency_id, [agent])[0]

        # Step 2: Send Supabase invite email
        try:
            invite_response = sb.auth.admin.invite_user_by_email(
                body.email,
                options={
                    "data": {
                        "agency_id": agency_id,
                        "role": body.role,
                        "agent_id": agent["id"],
                    },
                    "redirect_to": f"{__import__('config').settings.FRONTEND_URL}/auth/accept-invite",
                },
            )
            logger.info(f"Invite email sent to {body.email}")
        except Exception as invite_err:
            logger.warning(f"Supabase invite email failed (agent row created): {invite_err}")
            # Agent row is already created — they can still register manually

        return api_success(
            data=agent_formatted,
            message=f"Invitation sent to {body.email}",
            status_code=201,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Invite error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/plan-usage")
async def get_plan_usage(current_user: dict = Depends(verify_token)):
    """Get current plan limits and usage for the agency."""
    agency_id = require_agency_id(current_user)
    sb = get_supabase()

    limits = get_plan_limits(agency_id)

    # Get current agent count
    agents_result = sb.table("agents").select("id", count="exact").eq("agency_id", agency_id).eq("is_active", True).execute()
    agents_count = agents_result.count if hasattr(agents_result, "count") and agents_result.count is not None else len(agents_result.data)

    return api_success(
        data={
            "plan": limits["plan"],
            "agents": {"used": agents_count, "limit": limits["max_agents"]},
            "campaigns_per_month": {"limit": limits["max_campaigns_per_month"]},
            "ai_minutes": {"limit": limits["ai_minutes"]},
            "contracts": {"limit": limits["contracts"]},
        },
        message="Plan usage retrieved",
    )


@router.get("/{agent_id}")
async def get_agent(agent_id: UUID, current_user: dict = Depends(verify_token)):
    """Get agent profile with lead and viewing stats."""
    sb = get_supabase()
    agency_id = require_agency_id(current_user)

    agent = sb.table("agents").select("*").eq("id", str(agent_id)).eq("agency_id", agency_id).single().execute()
    if not agent.data:
        raise HTTPException(status_code=404, detail="Agent not found")

    agent_formatted = _populate_agent_branch_info(sb, agency_id, [agent.data])[0]

    # Agent stats
    leads = sb.table("leads").select("status").eq("assigned_agent_id", str(agent_id)).execute().data
    viewings = sb.table("viewings").select("status").eq("agent_id", str(agent_id)).execute().data

    stats = {
        "total_leads": len(leads),
        "closed_deals": sum(1 for l in leads if l["status"] == "closed"),
        "total_viewings": len(viewings),
        "viewings_completed": sum(1 for v in viewings if v["status"] == "completed"),
    }
    return api_success(data={**agent_formatted, "stats": stats}, message="Agent profile retrieved successfully")


@router.patch("/{agent_id}", response_model=ApiResponse[AgentResponse])
async def update_agent(agent_id: UUID, body: AgentUpdate, current_user: dict = Depends(verify_token)):
    sb = get_supabase()
    agency_id = require_agency_id(current_user)
    update_data = body.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    management = is_management_role(current_user.get("role"))
    privileged_fields = {"role", "is_active"}

    # Only owners and managers can change roles or activation status
    if not management and privileged_fields & set(update_data.keys()):
        raise HTTPException(
            status_code=403,
            detail="Only owners and managers can change agent roles or activation status",
        )

    # Regular agents can only edit their own profile
    if not management and str(agent_id) != str(current_user.get("agent_id")):
        raise HTTPException(status_code=403, detail="You can only edit your own profile")

    if "branch" in update_data and update_data["branch"]:
        update_data["branch"] = validate_and_normalize_branch(sb, agency_id, update_data["branch"])

    result = sb.table("agents").update(update_data).eq("id", str(agent_id)).eq("agency_id", agency_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Agent not found")
    updated_agent = _populate_agent_branch_info(sb, agency_id, result.data)[0]
    return api_success(data=updated_agent, message="Agent updated successfully")


@router.delete("/{agent_id}")
async def deactivate_agent(agent_id: UUID, current_user: dict = Depends(verify_token)):
    """Soft delete — sets is_active=False."""
    sb = get_supabase()
    agency_id = require_agency_id(current_user)

    if not is_management_role(current_user.get("role")):
        raise HTTPException(status_code=403, detail="Only owners and managers can remove agents")

    result = sb.table("agents").update({"is_active": False}).eq("id", str(agent_id)).eq("agency_id", agency_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Agent not found")
    return api_success(data={"agent_id": str(agent_id)}, message="Agent deactivated successfully")
