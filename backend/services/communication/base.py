"""
Abstract base classes for all communication providers.

Every concrete adapter (Meta, Twilio, 360dialog, Vapi, Telnyx, etc.)
must implement the relevant interface defined here.

Design principles:
  - All methods are async.
  - Adapters raise exceptions on failure (callers handle retry/fallback).
  - send_message() accepts a normalized phone (E.164 digits, no 'whatsapp:' prefix).
  - parse_inbound() converts any provider payload into a canonical InboundMessage dict.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


# ─── Canonical Data Types ─────────────────────────────────────────────────────

@dataclass
class InboundMessage:
    """Normalized inbound message, provider-agnostic."""
    from_phone: str                   # E.164 digits (no '+'), e.g. "971501234567"
    to_identifier: str                # Destination — phone_number_id (Meta) or phone (Twilio)
    body: str
    message_id: str
    provider: str                     # "meta" | "twilio" | "360dialog"
    raw: dict = field(default_factory=dict)  # Original provider payload for debugging


@dataclass
class SendResult:
    """Result of an outbound send attempt."""
    success: bool
    message_id: str | None = None
    error: str | None = None
    raw: dict = field(default_factory=dict)


# ─── CommunicationAccount ─────────────────────────────────────────────────────

@dataclass
class CommunicationAccount:
    """
    Represents a row from the communication_accounts table.
    Passed to provider_factory.get_whatsapp_provider() to get the
    right adapter with the right credentials.
    """
    id: str
    agency_id: str
    channel: str             # "whatsapp" | "voice" | "sms"
    provider: str            # "meta" | "twilio" | "360dialog"
    phone_number: str        # E.164 digits (no '+')
    agent_id: str | None = None     # Specific agent's BYON ID (or None for agency default)
    external_account_id: str = ""   # Meta WABA ID, Twilio SID, etc.
    phone_number_id: str = ""       # Meta phone_number_id
    access_token: str = ""          # Provider API token (decrypted at runtime)
    status: str = "active"
    metadata: dict = field(default_factory=dict)


# ─── WhatsApp Provider Interface ──────────────────────────────────────────────

class WhatsAppProvider(ABC):
    """
    Interface all WhatsApp adapters must implement.

    Concrete adapters: MetaWhatsAppAdapter, TwilioWhatsAppAdapter (legacy)
    """

    @abstractmethod
    async def send_message(
        self,
        to_phone: str,
        body: str,
        *,
        template_name: str | None = None,
        template_params: list[str] | None = None,
    ) -> SendResult:
        """
        Send a text message (or template) to a WhatsApp number.

        Args:
            to_phone: Recipient E.164 phone digits (no '+').
            body: Plain text body (used when template_name is None).
            template_name: Meta template name (for template messages).
            template_params: List of parameter values for the template.
        """
        ...

    @abstractmethod
    def verify_webhook(self, raw_body: bytes, headers: dict[str, str]) -> bool:
        """
        Verify the authenticity of an inbound webhook request.
        Returns True if the request is authentic, False otherwise.
        Implementations must use constant-time comparison.
        """
        ...

    @abstractmethod
    def parse_inbound(self, payload: Any) -> list[InboundMessage]:
        """
        Parse a provider webhook payload into a list of InboundMessages.
        Returns an empty list if the payload contains no text messages.
        """
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider identifier, e.g. 'meta', 'twilio'."""
        ...
