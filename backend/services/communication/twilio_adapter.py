"""
Twilio WhatsApp Adapter (Legacy / Shared Gateway).

Kept for backward compatibility with agencies that were auto-provisioned
with a dedicated Twilio number before the BYON migration.

New agencies should use MetaWhatsAppAdapter instead.

This adapter can be initialized either with a CommunicationAccount (per-agency
dedicated number) or fall back to the global master credentials in settings.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Any

from services.communication.base import (
    CommunicationAccount,
    InboundMessage,
    SendResult,
    WhatsAppProvider,
)
from config import settings

logger = logging.getLogger(__name__)


class TwilioWhatsAppAdapter(WhatsAppProvider):
    """
    WhatsApp provider backed by Twilio (legacy shared gateway model).

    When account is provided, uses account.phone_number as the sender (dedicated).
    When account is None, falls back to master TWILIO_WHATSAPP_NUMBER from settings.
    """

    def __init__(self, account: CommunicationAccount | None = None) -> None:
        self._account = account

        # Resolve credentials: per-account if provided, else global master
        if account and account.access_token:
            # Future: support per-agency Twilio sub-accounts via access_token
            # For now, all agencies share the master Twilio account
            pass

        self._account_sid = (os.getenv("TWILIO_ACCOUNT_SID") or settings.TWILIO_ACCOUNT_SID or "").strip()
        self._auth_token = (os.getenv("TWILIO_AUTH_TOKEN") or settings.TWILIO_AUTH_TOKEN or "").strip()
        self._from_number = (
            account.phone_number if account and account.phone_number
            else (os.getenv("TWILIO_WHATSAPP_NUMBER") or settings.TWILIO_WHATSAPP_NUMBER or "").strip()
        )

    @property
    def provider_name(self) -> str:
        return "twilio"

    # ── Outbound ──────────────────────────────────────────────────────────────

    async def send_message(
        self,
        to_phone: str,
        body: str,
        *,
        template_name: str | None = None,
        template_params: list[str] | None = None,
    ) -> SendResult:
        """Send via Twilio WhatsApp API."""
        try:
            from twilio.rest import Client  # type: ignore
        except ImportError:
            return SendResult(success=False, error="Twilio library not installed")

        if not self._account_sid or not self._auth_token:
            return SendResult(success=False, error="Twilio credentials not configured")

        from_wa = (
            f"whatsapp:{self._from_number}"
            if not self._from_number.startswith("whatsapp:")
            else self._from_number
        )
        to_wa = (
            f"whatsapp:{to_phone}"
            if not to_phone.startswith("whatsapp:")
            else to_phone
        )

        try:
            client = Client(self._account_sid, self._auth_token)
            msg = client.messages.create(body=body, from_=from_wa, to=to_wa)
            logger.info(f"[Twilio] Sent to {to_phone[-4:]}**** from {self._from_number} sid={msg.sid}")
            return SendResult(success=True, message_id=msg.sid)
        except Exception as exc:
            logger.error(f"[Twilio] Send error to {to_phone[-4:]}****: {exc}")
            return SendResult(success=False, error=str(exc))

    # ── Webhook Verification ──────────────────────────────────────────────────

    def verify_webhook(self, raw_body: bytes, headers: dict[str, str]) -> bool:
        """
        Verify Twilio webhook via X-Twilio-Signature.
        Returns True in development when auth token is missing (allows Postman testing).
        """
        if not self._auth_token:
            is_dev = getattr(settings, "APP_ENV", "development") == "development"
            if is_dev:
                logger.warning("[Twilio] Auth token not set — accepting in development")
                return True
            logger.critical("[Twilio] TWILIO_AUTH_TOKEN not set — rejecting webhook (fail closed)")
            return False

        # Full Twilio signature validation requires the request URL + POST params
        # which are handled by the webhook router (_verify_twilio_request).
        # This method is a lightweight check for header presence only.
        signature = headers.get("x-twilio-signature") or headers.get("X-Twilio-Signature")
        if not signature:
            is_dev = getattr(settings, "APP_ENV", "development") == "development"
            if is_dev:
                logger.warning("[Twilio] No X-Twilio-Signature — accepting in development")
                return True
            return False
        return True  # Full validation done in router via RequestValidator

    # ── Inbound Parsing ───────────────────────────────────────────────────────

    def parse_inbound(self, payload: Any) -> list[InboundMessage]:
        """
        Parse Twilio form-data inbound payload into canonical InboundMessages.
        payload is expected to be a dict (from request.form()).
        """
        if not isinstance(payload, dict):
            return []

        from_raw = payload.get("From", "")
        to_raw = payload.get("To", "")
        body = payload.get("Body", "")
        message_id = payload.get("SmsMessageSid", "")

        from_phone = from_raw.replace("whatsapp:", "").replace("+", "").strip()
        to_phone = to_raw.replace("whatsapp:", "").replace("+", "").strip()

        if not from_phone or not body:
            return []

        return [InboundMessage(
            from_phone=from_phone,
            to_identifier=to_phone,  # Agency's dedicated number — used for tenant resolution
            body=body,
            message_id=message_id,
            provider="twilio",
            raw=payload,
        )]
