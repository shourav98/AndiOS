"""
360dialog WhatsApp Adapter (Legacy).

Kept for any agencies still using 360dialog.
New agencies should use MetaWhatsAppAdapter.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

import httpx

from services.communication.base import (
    CommunicationAccount,
    InboundMessage,
    SendResult,
    WhatsAppProvider,
)
from config import settings

logger = logging.getLogger(__name__)


class Dialog360WhatsAppAdapter(WhatsAppProvider):
    """360dialog WhatsApp provider (legacy)."""

    def __init__(self, account: CommunicationAccount | None = None) -> None:
        self._account = account
        # Use per-account API key if present, else fall back to global
        self._api_key = (
            account.access_token if account and account.access_token
            else getattr(settings, "WHATSAPP_API_KEY", "")
        )

    @property
    def provider_name(self) -> str:
        return "360dialog"

    async def send_message(
        self,
        to_phone: str,
        body: str,
        *,
        template_name: str | None = None,
        template_params: list[str] | None = None,
    ) -> SendResult:
        phone = to_phone.replace("+", "").replace(" ", "").replace("-", "")
        url = "https://waba.360dialog.io/v1/messages"
        headers = {
            "D360-API-KEY": self._api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "text",
            "text": {"body": body},
        }
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                return SendResult(success=True, raw=resp.json())
            except Exception as exc:
                logger.error(f"[360dialog] Send error: {exc}")
                return SendResult(success=False, error=str(exc))

    def verify_webhook(self, raw_body: bytes, headers: dict[str, str]) -> bool:
        """
        360dialog does not sign deliveries with HMAC in this integration.
        Uses shared-secret token passed via header or query param.
        This method checks the token if available.
        """
        secret = getattr(settings, "WHATSAPP_WEBHOOK_TOKEN", "")
        if not secret:
            is_dev = getattr(settings, "APP_ENV", "development") == "development"
            return is_dev  # Fail closed in production

        provided = headers.get("x-webhook-token", "")
        if not provided:
            return False
        return hmac.compare_digest(str(provided), str(secret))

    def parse_inbound(self, payload: Any) -> list[InboundMessage]:
        messages: list[InboundMessage] = []
        for entry in payload.get("messages", []):
            if entry.get("type") != "text":
                continue
            messages.append(InboundMessage(
                from_phone=entry.get("from", ""),
                to_identifier="",
                body=entry.get("text", {}).get("body", ""),
                message_id=entry.get("id", ""),
                provider="360dialog",
                raw=entry,
            ))
        return messages
