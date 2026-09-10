"""
WhatsApp Service — thin compatibility shim over the Modular Communication Layer.

All new code should call the communication layer directly:
    from services.communication.provider_factory import get_whatsapp_provider_for_agency
    provider, account = await get_whatsapp_provider_for_agency(agency_id)
    result = await provider.send_message(to_phone, body)

This module keeps the old function signatures (send_whatsapp_message,
send_whatsapp_for_agency, parse_*) so existing routers continue to work
without changes. It delegates to the modular provider layer internally.

Legacy helpers (parse_360dialog_inbound, parse_twilio_inbound, verify_360dialog_webhook)
are re-exported here for any code that imports them directly.
"""
import logging

logger = logging.getLogger(__name__)


# ─── Primary API (used by all routers) ───────────────────────────────────────

async def send_whatsapp_message(
    to_phone: str,
    message: str,
    from_number: str | None = None,
) -> dict:
    """
    Send a WhatsApp message via the platform default provider.

    Prefer send_whatsapp_for_agency() when agency_id is available —
    that resolves the correct BYON account (Meta/Twilio) per agency.

    Args:
        to_phone: Recipient's phone number (E.164 or local).
        message: Message body text.
        from_number: Legacy override for Twilio sender number. Ignored for Meta.
    """
    from services.communication.provider_factory import get_whatsapp_provider
    from services.communication.twilio_adapter import TwilioWhatsAppAdapter
    from config import settings

    # If a specific from_number is given, use legacy Twilio path
    if from_number:
        from services.communication.base import CommunicationAccount
        # Build a minimal account object with the given number
        account = CommunicationAccount(
            id="legacy",
            agency_id="",
            channel="whatsapp",
            provider="twilio",
            phone_number=from_number.replace("whatsapp:", "").replace("+", ""),
        )
        provider = get_whatsapp_provider(account)
    else:
        provider = get_whatsapp_provider(None)  # Platform default

    # Normalize phone
    clean_phone = to_phone.replace("whatsapp:", "").replace("+", "").strip()
    result = await provider.send_message(clean_phone, message)

    if result.success:
        return {"status": "sent", "sid": result.message_id}
    return {"status": "error", "error": result.error}


async def send_whatsapp_for_agency(
    agency_id: str,
    to_phone: str,
    message: str,
    agent_id: str | None = None,
) -> dict:
    """
    Send a WhatsApp message using the agency's (or specific agent's) communication account.

    Resolution order:
      1. agent's active BYON communication_accounts row (if agent_id provided)
      2. agency's active default communication_accounts row (BYON — Meta preferred)
      3. agency's legacy dedicated_whatsapp_number (Twilio shared gateway)
      4. platform master Twilio number (final fallback)
    """
    from services.communication.provider_factory import get_whatsapp_provider_for_agency

    provider, account = await get_whatsapp_provider_for_agency(agency_id, agent_id=agent_id)
    clean_phone = to_phone.replace("whatsapp:", "").replace("+", "").strip()

    result = await provider.send_message(clean_phone, message)

    provider_name = account.provider if account else "twilio(master)"
    target_info = f"for agency {agency_id}" + (f" (agent {agent_id})" if agent_id else "")
    if result.success:
        logger.debug(
            f"[WA] Sent via {provider_name} to {clean_phone[-4:]}**** {target_info}"
        )
        return {"status": "sent", "sid": result.message_id}

    logger.error(
        f"[WA] Send failed via {provider_name} {target_info}: {result.error}"
    )
    return {"status": "error", "error": result.error}


# ─── Legacy Parse/Verify helpers (re-exported for backward compat) ────────────

def parse_360dialog_inbound(payload: dict) -> list[dict]:
    """
    Parse 360dialog inbound webhook into normalized message list.
    Returns: [{"from_phone": str, "message": str, "message_id": str}]
    """
    from services.communication.dialog360_adapter import Dialog360WhatsAppAdapter
    adapter = Dialog360WhatsAppAdapter()
    msgs = adapter.parse_inbound(payload)
    return [
        {"from_phone": m.from_phone, "message": m.body, "message_id": m.message_id}
        for m in msgs
    ]


def parse_twilio_inbound(form_data: dict) -> dict:
    """
    Parse Twilio inbound form data into normalized message.
    Returns: {"from_phone": str, "to_phone": str, "message": str, "message_id": str}
    """
    from services.communication.twilio_adapter import TwilioWhatsAppAdapter
    adapter = TwilioWhatsAppAdapter()
    msgs = adapter.parse_inbound(form_data)
    if msgs:
        m = msgs[0]
        return {
            "from_phone": m.from_phone,
            "to_phone": m.to_identifier,
            "message": m.body,
            "message_id": m.message_id,
        }
    return {"from_phone": "", "to_phone": "", "message": "", "message_id": ""}


def parse_meta_inbound(payload: dict) -> list[dict]:
    """
    Parse Meta Cloud API inbound webhook into normalized message list.
    Returns: [{"from_phone": str, "to_identifier": str, "message": str, "message_id": str}]
    Note: to_identifier is Meta's phone_number_id (use get_agency_by_phone_number_id RPC).
    """
    from services.communication.meta_adapter import MetaWhatsAppAdapter
    from services.communication.base import CommunicationAccount
    # Use a dummy account just for parsing (no credentials needed)
    dummy = CommunicationAccount(
        id="", agency_id="", channel="whatsapp", provider="meta",
        phone_number="", phone_number_id="", access_token="",
    )
    adapter = MetaWhatsAppAdapter(dummy)
    msgs = adapter.parse_inbound(payload)
    return [
        {
            "from_phone": m.from_phone,
            "to_identifier": m.to_identifier,
            "message": m.body,
            "message_id": m.message_id,
        }
        for m in msgs
    ]


def verify_360dialog_webhook(
    payload: bytes | str | dict,
    signature: str | None,
    secret: str | None = None,
) -> bool:
    """
    Legacy 360dialog webhook verification (kept for backward compatibility).

    Supports two modes:
      1. HMAC-SHA256 (when secret is provided) — matches original implementation.
      2. Shared-token header comparison (when no secret) — delegates to adapter.
    """
    import hmac as _hmac
    import hashlib
    import json

    # Normalize payload to bytes
    if isinstance(payload, bytes):
        raw = payload
    elif isinstance(payload, dict):
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    else:
        raw = str(payload).encode("utf-8")

    # Mode 1: HMAC-SHA256 (secret explicitly provided)
    if secret:
        if not signature:
            return False
        is_prod = getattr(__import__("config", fromlist=["settings"]).settings, "APP_ENV", "development") != "development"
        if not secret and is_prod:
            return False
        expected = _hmac.new(
            key=secret.encode("utf-8"),
            msg=raw,
            digestmod=hashlib.sha256,
        ).hexdigest()
        return _hmac.compare_digest(expected, str(signature))

    # Mode 2: Shared-token header comparison
    from services.communication.dialog360_adapter import Dialog360WhatsAppAdapter
    adapter = Dialog360WhatsAppAdapter()
    headers = {"x-webhook-token": signature or ""}
    return adapter.verify_webhook(raw, headers)
