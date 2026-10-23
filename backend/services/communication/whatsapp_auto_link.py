"""
WhatsApp Auto-Link Service — Agency Centralized WABA / BYON

When an agent is created or updated with a phone number, this service
automatically checks if that number exists under the agency's Meta WABA
and, if found, creates or updates the agent's communication_accounts row
so the agent is instantly WhatsApp-AI-ready without any manual steps.

Resolution logic:
  1. Fetch the agency's active Meta WABA comm account (agent_id IS NULL).
  2. Decrypt the stored access token.
  3. Call Meta Graph API: GET /{waba_id}/phone_numbers (paginated).
  4. Normalize both phone numbers (digits only) and compare.
  5. If match found → upsert communication_accounts for the agent.
  6. If no match → log a warning, do NOT raise (non-blocking background op).

This function is called in a FastAPI BackgroundTasks so it never blocks
the agent create/update response.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import httpx

from config import settings
from database.supabase_client import get_supabase
from utils.crypto import decrypt_token, encrypt_token

logger = logging.getLogger(__name__)


def _graph_base() -> str:
    version = getattr(settings, "META_GRAPH_API_VERSION", "v26.0") or "v26.0"
    return f"https://graph.facebook.com/{version}"


def _normalize_phone(raw: str) -> str:
    """Strip all non-digit characters for comparison."""
    return re.sub(r"\D", "", raw or "")


async def auto_link_agent_whatsapp(
    agency_id: str,
    agent_id: str,
    phone_number: str,
) -> None:
    """
    Background task: auto-detect and link an agent's WhatsApp number
    from the agency's Meta WABA.

    Args:
        agency_id:    The agency's UUID string.
        agent_id:     The agent's UUID string.
        phone_number: The agent's phone number (any format — will be normalized).

    Does NOT raise on soft failures (no WABA, number not in WABA, Meta API errors).
    Hard errors (DB failure) are logged at ERROR level.
    """
    tag = f"[AutoLink][agency={agency_id[:8]}][agent={agent_id[:8]}]"
    normalized_input = _normalize_phone(phone_number)

    if not normalized_input:
        logger.warning("%s Skipping — empty phone_number after normalization.", tag)
        return

    sb = get_supabase()

    # ── Step 1: Get agency's active Meta WABA account ────────────────────────
    try:
        waba_res = (
            sb.table("communication_accounts")
            .select("id, external_account_id, access_token_enc, phone_number_id")
            .eq("agency_id", agency_id)
            .is_("agent_id", "null")
            .eq("channel", "whatsapp")
            .eq("provider", "meta")
            .eq("status", "active")
            .limit(1)
            .execute()
        )
    except Exception as db_err:
        logger.error("%s DB error fetching agency WABA account: %s", tag, db_err)
        return

    if not waba_res.data:
        logger.info(
            "%s No active Meta WABA account for agency. Skipping auto-link "
            "(connect agency WhatsApp first via Embedded Signup).",
            tag,
        )
        return

    waba_row = waba_res.data[0]
    waba_id: str = waba_row.get("external_account_id", "")
    token_enc: str = waba_row.get("access_token_enc", "")

    if not waba_id or not token_enc:
        logger.warning(
            "%s Agency WABA row is missing waba_id or access_token_enc. Skipping.",
            tag,
        )
        return

    # ── Step 2: Decrypt access token ────────────────────────────────────────
    try:
        access_token = decrypt_token(token_enc)
    except Exception as dec_err:
        logger.error("%s Failed to decrypt agency WABA token: %s", tag, dec_err)
        return

    if not access_token:
        logger.error("%s Decrypted token is empty. Skipping.", tag)
        return

    # ── Step 3: Fetch all phone numbers from Meta WABA (paginated) ───────────
    headers = {"Authorization": f"Bearer {access_token}"}
    matched_phone_id: Optional[str] = None
    matched_display: Optional[str] = None

    try:
        next_url: Optional[str] = (
            f"{_graph_base()}/{waba_id}/phone_numbers"
            f"?fields=id,display_phone_number,verified_name,status"
        )
        async with httpx.AsyncClient(timeout=20) as client:
            while next_url:
                resp = await client.get(next_url, headers=headers)
                if resp.status_code != 200:
                    logger.warning(
                        "%s Meta WABA phone_numbers API error %s: %s",
                        tag, resp.status_code, resp.text[:300],
                    )
                    return

                payload = resp.json()
                for entry in payload.get("data", []):
                    meta_display = entry.get("display_phone_number", "")
                    meta_normalized = _normalize_phone(meta_display)
                    # Match on last 9 digits to handle country code variants
                    if (
                        meta_normalized == normalized_input
                        or meta_normalized.endswith(normalized_input[-9:])
                        or normalized_input.endswith(meta_normalized[-9:])
                    ):
                        matched_phone_id = str(entry.get("id", ""))
                        matched_display = meta_display
                        logger.info(
                            "%s Matched phone '%s' → Meta phone_number_id=%s",
                            tag, meta_display, matched_phone_id,
                        )
                        break

                if matched_phone_id:
                    break

                paging = payload.get("paging", {})
                next_url = paging.get("next")

    except Exception as api_err:
        logger.warning("%s Meta Graph API call failed: %s", tag, api_err)
        return

    if not matched_phone_id:
        logger.info(
            "%s Phone %s not found in WABA %s. "
            "Add the number to the WABA in Meta Business Manager first.",
            tag, phone_number, waba_id,
        )
        return

    # ── Step 4: Upsert communication_accounts for agent ──────────────────────
    upsert_data = {
        "agency_id": agency_id,
        "agent_id": agent_id,
        "channel": "whatsapp",
        "provider": "meta",
        "phone_number": normalized_input,
        "phone_number_id": matched_phone_id,
        "external_account_id": waba_id,
        "access_token_enc": token_enc,   # Reuse agency's encrypted token
        "status": "active",
        "meta_onboarding_state": "agency_provisioned",
        "metadata": {
            "auto_linked": True,
            "display_phone_number": matched_display,
            "source": "agent_create_or_update",
        },
    }

    try:
        # Check if a row already exists for this agent
        existing = (
            sb.table("communication_accounts")
            .select("id, status")
            .eq("agency_id", agency_id)
            .eq("agent_id", agent_id)
            .eq("channel", "whatsapp")
            .limit(1)
            .execute()
        )

        if existing.data:
            row_id = existing.data[0]["id"]
            existing_status = existing.data[0].get("status")
            # Never downgrade an already-active row
            if existing_status == "active" and existing.data[0].get("phone_number_id") == matched_phone_id:
                logger.info(
                    "%s Agent already has active WhatsApp account (id=%s). No change.",
                    tag, row_id,
                )
                return
            sb.table("communication_accounts").update(upsert_data).eq("id", row_id).execute()
            logger.info(
                "%s Updated existing communication_accounts row (id=%s) for agent. status=active",
                tag, row_id,
            )
        else:
            ins = sb.table("communication_accounts").insert(upsert_data).execute()
            new_id = ins.data[0]["id"] if ins.data else "?"
            logger.info(
                "%s Created new communication_accounts row (id=%s) for agent. "
                "phone_number_id=%s — Agent is now WhatsApp AI-ready! 🎉",
                tag, new_id, matched_phone_id,
            )

    except Exception as db_upsert_err:
        logger.error(
            "%s DB upsert failed for agent communication_accounts: %s",
            tag, db_upsert_err,
        )
