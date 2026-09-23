"""
Meta Cloud API WhatsApp Adapter.

Handles:
  - Outbound text messages via graph.facebook.com/{version}/{phone_number_id}/messages
  - Inbound webhook verification (X-Hub-Signature-256 HMAC-SHA256)
  - Inbound payload parsing (Meta Cloud API webhook format)

This adapter is initialized per-agency using the agency's own
CommunicationAccount credentials (WABA ID, phone_number_id, access_token).
It does NOT read from global .env settings — credentials come from the DB.

Graph API version is controlled by META_GRAPH_API_VERSION in .env (default: v22.0).
Do NOT hardcode versions here — Meta deprecates old versions regularly.

Reference: https://developers.facebook.com/docs/whatsapp/cloud-api/messages
Changelog: https://developers.facebook.com/docs/graph-api/changelog
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

logger = logging.getLogger(__name__)

META_GRAPH_BASE = "https://graph.facebook.com"


def _graph_url() -> str:
    """Return the versioned Graph API base URL from settings."""
    from config import settings
    version = getattr(settings, "META_GRAPH_API_VERSION", "v26.0") or "v26.0"
    return f"{META_GRAPH_BASE}/{version}"


class MetaWhatsAppAdapter(WhatsAppProvider):
    """
    WhatsApp provider backed by Meta Cloud API.

    Initialized with a CommunicationAccount that contains:
      - phone_number_id: Meta's phone number ID for this agency
      - access_token:    Long-lived System User token (stored encrypted in DB)
      - global META_APP_SECRET: Meta App Secret (for HMAC webhook verification)
    """

    def __init__(
        self,
        account: CommunicationAccount | None = None,
        app_secret: str = "",
    ) -> None:
        from config import settings
        self._account = account
        self._phone_number_id = (account.phone_number_id if account else "") or getattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "") or ""
        self._access_token = (account.access_token if account else "") or getattr(settings, "WHATSAPP_API_KEY", "") or ""
        # App secret used for webhook HMAC verification (global META_APP_SECRET)
        self._app_secret: str = app_secret or getattr(settings, "META_APP_SECRET", "") or ""

    @property
    def provider_name(self) -> str:
        return "meta"

    # ── Outbound ──────────────────────────────────────────────────────────────

    async def send_message(
        self,
        to_phone: str,
        body: str,
        *,
        template_name: str | None = None,
        template_params: list[str] | None = None,
    ) -> SendResult:
        """
        Send a WhatsApp message via Meta Cloud API.

        For template messages (24-hour window enforcement by Meta):
          pass template_name and template_params.
        For free-form text (within the 24h window):
          pass only body.
        """
        url = f"{_graph_url()}/{self._phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

        # Build payload
        if template_name:
            payload = self._build_template_payload(to_phone, template_name, template_params or [])
        else:
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to_phone,
                "type": "text",
                "text": {"preview_url": False, "body": body},
            }

        async with httpx.AsyncClient(timeout=15) as client:
            try:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                message_id = (data.get("messages") or [{}])[0].get("id")
                logger.info(
                    f"[Meta] Sent to {to_phone[-4:]}**** via phone_number_id={self._phone_number_id} "
                    f"msg_id={message_id}"
                )
                return SendResult(success=True, message_id=message_id, raw=data)
            except httpx.HTTPStatusError as exc:
                error_body = exc.response.text
                logger.error(
                    f"[Meta] HTTP {exc.response.status_code} sending to {to_phone[-4:]}****: {error_body}"
                )
                return SendResult(success=False, error=f"HTTP {exc.response.status_code}: {error_body}")
            except Exception as exc:
                logger.error(f"[Meta] Unexpected error sending to {to_phone[-4:]}****: {exc}")
                return SendResult(success=False, error=str(exc))

    def _build_template_payload(
        self,
        to_phone: str,
        template_name: str,
        params: list[str],
    ) -> dict:
        components = []
        if params:
            components.append({
                "type": "body",
                "parameters": [{"type": "text", "text": p} for p in params],
            })
        return {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": "en"},
                "components": components,
            },
        }

    # ── Webhook Verification ──────────────────────────────────────────────────

    def verify_webhook(self, raw_body: bytes, headers: dict[str, str]) -> bool:
        """
        Verify Meta webhook using X-Hub-Signature-256 HMAC-SHA256.

        Meta signs the raw request body with the app secret.
        Header format: "sha256=<hex_digest>"

        Fails closed (returns False) when:
          - App secret is not configured
          - Signature header is missing
          - Signatures do not match
        """
        if not self._app_secret:
            logger.warning(
                "[Meta] app_secret not configured for account "
                f"{self._account.id} — rejecting webhook (fail closed)"
            )
            return False

        signature_header = (
            headers.get("x-hub-signature-256") or
            headers.get("X-Hub-Signature-256") or ""
        )
        if not signature_header.startswith("sha256="):
            logger.warning("[Meta] Missing or malformed X-Hub-Signature-256 header")
            return False

        expected = "sha256=" + hmac.new(
            key=self._app_secret.encode("utf-8"),
            msg=raw_body,
            digestmod=hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected, signature_header)

    # ── Inbound Parsing ───────────────────────────────────────────────────────

    def parse_inbound(self, payload: Any) -> list[InboundMessage]:
        """
        Parse a Meta Cloud API webhook payload into canonical InboundMessages.

        Meta payload structure (simplified):
        {
          "entry": [{
            "changes": [{
              "value": {
                "metadata": {"phone_number_id": "..."},
                "messages": [{"from": "...", "id": "...", "text": {"body": "..."}}]
              }
            }]
          }]
        }
        """
        messages: list[InboundMessage] = []

        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                phone_number_id = value.get("metadata", {}).get("phone_number_id", "")

                for msg in value.get("messages", []):
                    msg_type = msg.get("type")
                    if msg_type != "text":
                        # TODO: handle image, audio, interactive etc.
                        logger.debug(f"[Meta] Skipping non-text message type: {msg_type}")
                        continue

                    from_phone = msg.get("from", "")
                    body = msg.get("text", {}).get("body", "")
                    message_id = msg.get("id", "")

                    if not from_phone or not body:
                        continue

                    messages.append(InboundMessage(
                        from_phone=from_phone,
                        to_identifier=phone_number_id,  # Used to resolve agency
                        body=body,
                        message_id=message_id,
                        provider="meta",
                        raw=msg,
                    ))

        return messages
