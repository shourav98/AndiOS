"""
Provider Factory — resolves and returns the correct WhatsApp adapter.

Resolution order:
  1. CommunicationAccount.provider (per-agency, from DB) — highest priority
  2. Platform default WHATSAPP_PROVIDER setting — fallback (default: "meta")

IMPORTANT: If the resolved provider string is unknown/unsupported, the factory
raises ValueError with a clear error message and logs at ERROR level.
There is NO silent fallback to any specific provider — every failure is explicit.

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

    # Platform default (no specific account):
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

_SUPPORTED_PROVIDERS = {"meta", "twilio", "360dialog"}


def get_whatsapp_provider(account: CommunicationAccount | None = None) -> WhatsAppProvider:
    """
    Return the correct WhatsApp adapter for the given account.

    Args:
        account: Agency's CommunicationAccount from DB. If None, uses the
                 platform default provider (WHATSAPP_PROVIDER setting, default "meta").

    Returns:
        Concrete WhatsAppProvider adapter instance.

    Raises:
        ValueError: If the provider string is unknown/unsupported. This is ALWAYS
                    raised explicitly — there is no silent fallback.
    """
    # Determine which provider to use
    if account and account.provider:
        raw = account.provider
        if not isinstance(raw, str):
            raise ValueError(
                f"[Factory] CommunicationAccount.provider must be a string, "
                f"got {type(raw).__name__!r} for account {getattr(account, 'id', '?')}. "
                f"Supported: {sorted(_SUPPORTED_PROVIDERS)}."
            )
        provider_name = raw.lower().strip()
    else:
        provider_name = (settings.WHATSAPP_PROVIDER or "meta").lower().strip()

    if provider_name not in _SUPPORTED_PROVIDERS:
        logger.error(
            "[Factory] Unknown WhatsApp provider %r for account %s. "
            "Supported: %s. Aborting — check communication_accounts.provider column.",
            provider_name,
            getattr(account, "id", "platform-default"),
            sorted(_SUPPORTED_PROVIDERS),
        )
        raise ValueError(
            f"Unknown WhatsApp provider: {provider_name!r}. "
            f"Supported: {sorted(_SUPPORTED_PROVIDERS)}."
        )

    if provider_name == "meta":
        from services.communication.meta_adapter import MetaWhatsAppAdapter
        logger.debug(
            "[Factory] Using MetaWhatsAppAdapter"
            + (f" for agency {account.agency_id} (phone_number_id={account.phone_number_id})" if account else " (platform default)")
        )
        return MetaWhatsAppAdapter(account)

    elif provider_name == "twilio":
        from services.communication.twilio_adapter import TwilioWhatsAppAdapter
        logger.debug(
            "[Factory] Using TwilioWhatsAppAdapter (legacy)"
            + (f" for agency {account.agency_id}" if account else " (master gateway)")
        )
        return TwilioWhatsAppAdapter(account)

    else:  # 360dialog
        from services.communication.dialog360_adapter import Dialog360WhatsAppAdapter
        logger.debug("[Factory] Using Dialog360WhatsAppAdapter (legacy)")
        return Dialog360WhatsAppAdapter(account)


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
      3. Fallback: platform default provider (WHATSAPP_PROVIDER setting, default "meta")
         Note: this final fallback uses no per-account credentials — only useful if
         META_GRAPH_API settings are configured globally (development / single-agency setups).

    Returns:
        (provider, account) — account is None only for the final platform-default fallback.

    Raises:
        ValueError: If a DB account has an unrecognized provider string.
    """
    from database.supabase_client import get_supabase
    from utils.crypto import decrypt_token

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
            # Decrypt access token — decrypt_token() returns "" on failure (safe)
            raw_token = row.get("access_token") or ""
            decrypted_token = decrypt_token(raw_token) if raw_token else ""

            account = CommunicationAccount(
                id=row["id"],
                agency_id=row["agency_id"],
                agent_id=row.get("agent_id"),
                channel=row["channel"],
                provider=row["provider"],
                phone_number=row.get("phone_number", ""),
                external_account_id=row.get("external_account_id", ""),
                phone_number_id=row.get("phone_number_id", ""),
                access_token=decrypted_token or getattr(settings, "WHATSAPP_API_KEY", ""),
                status=row.get("status", "active"),
                metadata=row.get("metadata", {}),
            )
            return get_whatsapp_provider(account), account

    except ValueError:
        # Re-raise provider resolution errors — these are configuration bugs, not transient
        raise
    except Exception as exc:
        logger.error(
            "[Factory] Failed to fetch communication_account for agency %s (agent %s): %s. "
            "Falling back to platform default provider (%s).",
            agency_id,
            agent_id,
            exc,
            settings.WHATSAPP_PROVIDER or "meta",
        )

    # Final fallback: platform default (no per-account credentials)
    logger.warning(
        "[Factory] No active communication_account found for agency %s (agent %s). "
        "Using platform default provider — ensure WHATSAPP_PHONE_NUMBER_ID and "
        "WHATSAPP_API_KEY are set in .env for this to work.",
        agency_id,
        agent_id,
    )
    return get_whatsapp_provider(None), None
