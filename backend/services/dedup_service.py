"""
Deduplication Service — prevents duplicate leads from portals.
Uses external_lead_id as unique key.
"""
import hashlib
from database.supabase_client import get_supabase
import logging

logger = logging.getLogger(__name__)


def generate_lead_id(source: str, external_id: str) -> str:
    """Generate consistent unique ID from source + external ID."""
    return hashlib.sha256(f"{source}:{external_id}".encode()).hexdigest()[:32]


async def is_duplicate(external_lead_id: str) -> bool:
    """Check if a lead with this external ID already exists."""
    try:
        sb = get_supabase()
        result = (
            sb.table("leads")
            .select("id")
            .eq("external_lead_id", external_lead_id)
            .execute()
        )
        return len(result.data) > 0
    except Exception as e:
        logger.error(f"Dedup check error: {e}")
        return False


async def get_existing_lead_by_phone(phone: str) -> dict | None:
    """Find an existing lead by phone number (secondary dedup check)."""
    try:
        # Normalise phone
        clean_phone = phone.replace("+", "").replace(" ", "").replace("-", "")
        sb = get_supabase()
        result = (
            sb.table("leads")
            .select("*")
            .ilike("phone", f"%{clean_phone[-9:]}")  # match last 9 digits
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Phone dedup check error: {e}")
        return None
