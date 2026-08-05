"""
Lead Routing Service — resolves agency_id and assigned_agent_id for inbound webhooks.
"""
from database.supabase_client import get_supabase
from config import settings
import logging

logger = logging.getLogger(__name__)


def _normalize_phone(phone: str) -> str:
    return phone.replace("+", "").replace(" ", "").replace("-", "")


async def resolve_agency_and_agent(
    source: str,
    property_ref: str | None = None,
    agent_phone: str | None = None,
    agent_email: str | None = None,
) -> tuple[str | None, str | None]:
    """
    Determine which agency and agent should receive an inbound lead.
    Returns (agency_id, assigned_agent_id).
    """
    sb = get_supabase()
    agency_id = None
    agent_id = None

    # 1. Explicit default agency from env (production webhook routing)
    if settings.DEFAULT_AGENCY_ID:
        agency_id = settings.DEFAULT_AGENCY_ID

    # 2. Match agent by phone or email (portal often includes listing agent contact)
    if agent_phone:
        clean = _normalize_phone(agent_phone)
        agent_result = (
            sb.table("agents")
            .select("id, agency_id")
            .eq("is_active", True)
            .or_(f"phone.ilike.%{clean[-9:]},whatsapp_number.ilike.%{clean[-9:]}")
            .limit(1)
            .execute()
        )
        if agent_result.data:
            agent = agent_result.data[0]
            agent_id = agent["id"]
            agency_id = agent["agency_id"]

    if not agent_id and agent_email:
        agent_result = (
            sb.table("agents")
            .select("id, agency_id")
            .eq("email", agent_email)
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
        if agent_result.data:
            agent = agent_result.data[0]
            agent_id = agent["id"]
            agency_id = agent["agency_id"]

    # 3. Fallback: first active agency (single-tenant dev setups)
    if not agency_id:
        agency_result = (
            sb.table("agencies")
            .select("id")
            .eq("is_active", True)
            .order("created_at")
            .limit(1)
            .execute()
        )
        if agency_result.data:
            agency_id = agency_result.data[0]["id"]
            logger.warning(f"Lead routing fallback: using first agency {agency_id} for source={source}")

    # 4. If agency known but no agent, pick first active agent in agency
    if agency_id and not agent_id:
        fallback_agent = (
            sb.table("agents")
            .select("id")
            .eq("agency_id", agency_id)
            .eq("is_active", True)
            .order("created_at")
            .limit(1)
            .execute()
        )
        if fallback_agent.data:
            agent_id = fallback_agent.data[0]["id"]

    return agency_id, agent_id
