"""
Provider Factory — resolves and returns the correct WhatsApp adapter.

Resolution order:
  1. CommunicationAccount.provider (per-agency, from DB) — highest priority
  2. Platform default WHATSAPP_PROVIDER setting — fallback

Usage:
    from services.communication.provider_factory import get_whatsapp_provider
    from services.communication.base import CommunicationAccount

    # From a communication_accounts DB row:
    account = CommunicationAccount(
        id="...", agency_id="...", channel="whatsapp",
        provider="meta", phone_number="971501234567",
        phone_number_id="12345", access_token="EAAxxxxx",
    )
    provider = get_whatsapp_provider(account)
    result = await provider.send_message(to_phone, body)

    # Legacy fallback (Twilio shared gateway, no specific account):
    provider = get_whatsapp_provider(None)
    result = await provider.send_message(to_phone, body)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config import settings
from services.communication.base import CommunicationAccount, WhatsAppProvider

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def get_whatsapp_provider(account: CommunicationAccount | None = None) -> WhatsAppProvider:
    """
    Return the correct WhatsApp adapter for the given account.

    Args:
        account: Agency's CommunicationAccount from DB. If None, uses the
                 platform default provider (WHATSAPP_PROVIDER setting).

    Returns:
        Concrete WhatsAppProvider adapter instance.

    Raises:
        ValueError: If the provider string is unknown/unsupported.
    """
    # Determine which provider to use
    provider_name = (
        account.provider.lower().strip()
        if account and account.provider
        else (settings.WHATSAPP_PROVIDER or "twilio").lower().strip()
    )

    if provider_name == "meta":
        from services.communication.meta_adapter import MetaWhatsAppAdapter
        logger.debug(
            f"[Factory] Using MetaWhatsAppAdapter"
            + (f" for agency {account.agency_id} (phone_number_id={account.phone_number_id})" if account else " (platform default)")
        )
        return MetaWhatsAppAdapter(account)

    elif provider_name == "twilio":
        from services.communication.twilio_adapter import TwilioWhatsAppAdapter
        logger.debug(
            f"[Factory] Using TwilioWhatsAppAdapter"
            + (f" for agency {account.agency_id}" if account else " (master gateway)")
        )
        return TwilioWhatsAppAdapter(account)

    elif provider_name == "360dialog":
        from services.communication.dialog360_adapter import Dialog360WhatsAppAdapter
        logger.debug("[Factory] Using Dialog360WhatsAppAdapter")
        return Dialog360WhatsAppAdapter(account)

    else:
        raise ValueError(
            f"Unknown WhatsApp provider: '{provider_name}'. "
            f"Supported: 'meta', 'twilio', '360dialog'."
        )


async def get_whatsapp_provider_for_agency(
    agency_id: str,
    agent_id: str | None = None,
) -> tuple[WhatsAppProvider, CommunicationAccount | None]:
    """
    Convenience: fetch the agency's (or agent's) active communication_account from DB
    and return the correct provider.

    Resolution:
      1. If agent_id provided: check communication_accounts for that specific agent's BYON number
      2. If not found or agent_id None: check communication_accounts for agency default (agent_id IS NULL)
      3. Fallback: platform legacy Twilio shared gateway

    Returns:
        (provider, account) — account is None for legacy Twilio fallback.
    """
    from database.supabase_client import get_supabase

    sb = get_supabase()
    try:
        row = None
        if agent_id:
            res = (
                sb.table("communication_accounts")
                .select("*")
                .eq("agency_id", agency_id)
                .eq("agent_id", agent_id)
                .eq("channel", "whatsapp")
                .eq("status", "active")
                .limit(1)
                .execute()
            )
            if res and hasattr(res, "data") and res.data:
                row = res.data[0]

        if not row:
            res = (
                sb.table("communication_accounts")
                .select("*")
                .eq("agency_id", agency_id)
                .is_("agent_id", "null")
                .eq("channel", "whatsapp")
                .eq("status", "active")
                .limit(1)
                .execute()
            )
            if res and hasattr(res, "data") and res.data:
                row = res.data[0]

        if row:
            account = CommunicationAccount(
                id=row["id"],
                agency_id=row["agency_id"],
                agent_id=row.get("agent_id"),
                channel=row["channel"],
                provider=row["provider"],
                phone_number=row.get("phone_number", ""),
                external_account_id=row.get("external_account_id", ""),
                phone_number_id=row.get("phone_number_id", ""),
                access_token=row.get("access_token") or getattr(settings, "WHATSAPP_API_KEY", ""),
                status=row.get("status", "active"),
                metadata=row.get("metadata", {}),
            )
            return get_whatsapp_provider(account), account
    except Exception as exc:
        logger.warning(
            f"[Factory] Could not fetch communication_account for agency {agency_id} (agent {agent_id}): {exc} "
            "— falling back to legacy Twilio gateway"
        )

    # Fallback: legacy Twilio shared gateway (agencies provisioned before BYON)
    return get_whatsapp_provider(None), None
