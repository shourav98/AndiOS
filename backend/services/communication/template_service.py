"""
WhatsApp Message Templates Service (Step D).

Handles:
  1. Per-WABA WhatsApp template registry (`whatsapp_templates` table).
  2. Auto-creating a standard starter set of utility/lead templates when an agent connects their WABA.
  3. Enforcing the Meta 24-hour Customer Care Window:
     - Free-form messages permitted ONLY within 24h of the lead's last inbound message.
     - Outside 24h: Must use an APPROVED template.
     - If no approved template exists: Queue outbound message and notify agent (do NOT send free-form).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# Standard starter templates registered for new WABAs
STANDARD_TEMPLATES = [
    {
        "name": "andios_lead_first_contact",
        "category": "UTILITY",
        "language": "en",
        "components": [
            {
                "type": "BODY",
                "text": "Hello {{1}}! 👋 Thank you for your inquiry about {{2}} on Property Finder. How can we assist you with details or arranging a viewing?",
                "example": {
                    "body_text": [["Sarah", "2BR Luxury Apartment in Dubai Marina"]]
                },
            }
        ],
    },
    {
        "name": "andios_viewing_confirmation",
        "category": "UTILITY",
        "language": "en",
        "components": [
            {
                "type": "BODY",
                "text": "Hi {{1}}, your viewing for {{2}} is confirmed for {{3}} at {{4}}. Address: {{5}}. Please reply CONFIRM or CANCEL.",
                "example": {
                    "body_text": [["John", "Villa 12", "Tomorrow", "3:00 PM", "Palm Jumeirah"]]
                },
            }
        ],
    },
]


def _graph_version() -> str:
    from config import settings
    return getattr(settings, "META_GRAPH_API_VERSION", "v26.0") or "v26.0"


async def auto_create_standard_templates(
    waba_id: str,
    access_token: str,
    communication_account_id: str,
) -> list[dict]:
    """
    Register standard starter templates with Meta Graph API and persist in whatsapp_templates.
    Safe to call multiple times (checks for existing before creating).
    """
    from database.supabase_client import get_supabase
    sb = get_supabase()
    version = _graph_version()
    url = f"https://graph.facebook.com/{version}/{waba_id}/message_templates"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    created = []
    async with httpx.AsyncClient(timeout=25) as client:
        for tmpl in STANDARD_TEMPLATES:
            tmpl_name = tmpl["name"]
            # Check local table first
            try:
                existing = (
                    sb.table("whatsapp_templates")
                    .select("id, status")
                    .eq("waba_id", waba_id)
                    .eq("name", tmpl_name)
                    .limit(1)
                    .execute()
                )
                if existing.data:
                    logger.debug("[Templates] Template %s already recorded for WABA %s", tmpl_name, waba_id)
                    continue
            except Exception as e:
                logger.warning("[Templates] Error checking existing template: %s", e)

            # Submit to Meta
            meta_template_id = None
            status = "PENDING"
            try:
                resp = await client.post(url, json=tmpl, headers=headers)
                if resp.status_code in (200, 201):
                    data = resp.json()
                    meta_template_id = data.get("id")
                    status = data.get("status", "PENDING")
                    logger.info("[Templates] Created template %s on Meta: id=%s status=%s", tmpl_name, meta_template_id, status)
                elif resp.status_code == 400 and "already exists" in resp.text.lower():
                    status = "APPROVED"
                    logger.info("[Templates] Template %s already exists on Meta WABA %s", tmpl_name, waba_id)
                else:
                    logger.warning("[Templates] Failed to create template %s on Meta: %s %s", tmpl_name, resp.status_code, resp.text)
            except Exception as meta_err:
                logger.warning("[Templates] Exception creating template %s: %meta_err", tmpl_name, meta_err)

            # Persist in DB
            try:
                row_data = {
                    "communication_account_id": communication_account_id,
                    "waba_id": waba_id,
                    "name": tmpl_name,
                    "language": tmpl["language"],
                    "category": tmpl["category"],
                    "status": status,
                    "components": tmpl["components"],
                    "meta_template_id": meta_template_id,
                }
                sb.table("whatsapp_templates").upsert(
                    row_data, on_conflict="waba_id,name,language"
                ).execute()
                created.append(row_data)
            except Exception as db_err:
                logger.warning("[Templates] DB error saving template %s: %s", tmpl_name, db_err)

    return created


def is_within_24h_window(last_inbound_at: Optional[Union[str, datetime]]) -> bool:
    """
    Check if the customer service 24-hour window is currently open.
    """
    if not last_inbound_at:
        return False

    if isinstance(last_inbound_at, str):
        try:
            # Handle ISO formats
            dt = datetime.fromisoformat(last_inbound_at.replace("Z", "+00:00"))
        except ValueError:
            return False
    else:
        dt = last_inbound_at

    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return (now - dt) <= timedelta(hours=24)


async def get_approved_template_or_none(
    sb: Any,
    waba_id: str,
    template_name: str,
) -> Optional[dict]:
    """
    Fetch an approved template from whatsapp_templates.
    Returns None if no approved template is available.
    """
    try:
        res = (
            sb.table("whatsapp_templates")
            .select("*")
            .eq("waba_id", waba_id)
            .eq("name", template_name)
            .eq("status", "APPROVED")
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0]
    except Exception as e:
        logger.error("[Templates] Error fetching approved template %s for WABA %s: %s", template_name, waba_id, e)
    return None
