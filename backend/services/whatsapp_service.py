"""
WhatsApp Service — supports 360dialog and Twilio.
Set WHATSAPP_PROVIDER in .env to switch providers.
"""
import httpx
from config import settings
import logging

logger = logging.getLogger(__name__)


async def send_whatsapp_message(to_phone: str, message: str) -> dict:
    """Send a WhatsApp message via the configured provider."""
    if settings.WHATSAPP_PROVIDER == "360dialog":
        return await _send_360dialog(to_phone, message)
    elif settings.WHATSAPP_PROVIDER == "twilio":
        return await _send_twilio(to_phone, message)
    else:
        logger.warning(f"Unknown WhatsApp provider: {settings.WHATSAPP_PROVIDER}")
        return {"status": "error", "error": "Unknown provider"}


async def _send_360dialog(to_phone: str, message: str) -> dict:
    """Send via 360dialog Cloud API."""
    # Normalise phone: remove + and spaces, ensure 971 prefix for UAE
    phone = to_phone.replace("+", "").replace(" ", "").replace("-", "")

    url = f"https://waba.360dialog.io/v1/messages"
    headers = {
        "D360-API-KEY": settings.WHATSAPP_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": message},
    }

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return {"status": "sent", "response": resp.json()}
        except httpx.HTTPError as e:
            logger.error(f"360dialog send error: {e}")
            return {"status": "error", "error": str(e)}


import os

async def _send_twilio(to_phone: str, message: str) -> dict:
    """Send via Twilio WhatsApp API."""
    from twilio.rest import Client  # type: ignore
    account_sid = (os.getenv("TWILIO_ACCOUNT_SID") or settings.TWILIO_ACCOUNT_SID).strip()
    auth_token = (os.getenv("TWILIO_AUTH_TOKEN") or settings.TWILIO_AUTH_TOKEN).strip()
    whatsapp_from = (os.getenv("TWILIO_WHATSAPP_NUMBER") or settings.TWILIO_WHATSAPP_NUMBER).strip()
    
    masked_token = f"{auth_token[:4]}...{auth_token[-4:]}" if len(auth_token) >= 8 else "(too short)"
    logger.info(f"Twilio Attempt: SID={account_sid}, Token={masked_token} (len={len(auth_token)}), From={whatsapp_from}")

    twilio = Client(account_sid, auth_token)
    try:
        msg = twilio.messages.create(
            body=message,
            from_=whatsapp_from,
            to=f"whatsapp:{to_phone}",
        )
        return {"status": "sent", "sid": msg.sid}
    except Exception as e:
        logger.error(f"Twilio send error: {e}")
        return {"status": "error", "error": str(e)}


def verify_360dialog_webhook(payload: bytes | str | dict, signature: str | None, secret: str | None = None) -> bool:
    """
    Verify 360dialog webhook signature (HMAC-SHA256).
    Fails closed when secret or signature is missing in production.
    """
    import hmac
    import hashlib
    import json

    webhook_secret = secret or getattr(settings, "WHATSAPP_WEBHOOK_TOKEN", "") or getattr(settings, "WHATSAPP_API_KEY", "")
    is_production = getattr(settings, "APP_ENV", "development") != "development"

    if not webhook_secret:
        if is_production:
            logger.critical("360dialog webhook secret is not configured — rejecting webhook (fail closed)")
            return False
        logger.warning("360dialog webhook secret not set — failing safe in development")
        return False

    if not signature or payload is None:
        return False

    if isinstance(payload, bytes):
        raw_body = payload
    elif isinstance(payload, dict):
        raw_body = json.dumps(payload, separators=(',', ':')).encode("utf-8")
    else:
        raw_body = str(payload).encode("utf-8")

    expected = hmac.new(
        key=webhook_secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    clean_signature = signature.replace("sha256=", "").strip()
    return hmac.compare_digest(expected, clean_signature)


def parse_360dialog_inbound(payload: dict) -> list[dict]:
    """
    Parse 360dialog inbound webhook payload into a list of normalised messages.
    Returns: [{"from_phone": str, "message": str, "message_id": str}]
    """
    messages = []
    for entry in payload.get("messages", []):
        msg_type = entry.get("type")
        if msg_type == "text":
            messages.append({
                "from_phone": entry.get("from"),
                "message": entry.get("text", {}).get("body", ""),
                "message_id": entry.get("id"),
            })
        # TODO: handle image/audio/location types
    return messages


def parse_twilio_inbound(form_data: dict) -> dict:
    """Parse Twilio inbound form data into normalised message."""
    return {
        "from_phone": form_data.get("From", "").replace("whatsapp:", ""),
        "message": form_data.get("Body", ""),
        "message_id": form_data.get("SmsMessageSid"),
    }
