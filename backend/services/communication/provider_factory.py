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

# Providers that require a per-row access token stored in communication_accounts.
# Twilio uses platform-level master credentials (TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN)
# and must NOT raise when the row carries no token.
_PROVIDERS_REQUIRING_TOKEN = {"meta", "360dialog"}


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
    Look up the active communication_account for (agency_id, agent_id, channel='whatsapp'),
    decrypt the stored access token (when required by the provider), and return (provider, account).

    Resolution order:
      1. Agent-specific active account (if agent_id provided).
         If the agent has no row and ALLOW_AGENCY_NUMBER_FALLBACK_FOR_AGENTS is False (default),
         raises ValueError immediately — does NOT silently fall back to agency default.
      2. Agency-default active account (agent_id IS NULL).
      3. DEFAULT_AGENCY_ID env fallback — checked BEFORE legacy Twilio so the default agency
         is NEVER silently rerouted to a legacy dedicated Twilio number.
      4. Legacy dedicated number from agencies.dedicated_whatsapp_number (non-default agencies only).
      5. Raise ValueError — non-default agency must connect its own WhatsApp account.

    Token rules:
      - meta / 360dialog: per-row token is required. Empty token → env fallback for the
        default agency, explicit ValueError for every other agency.
      - twilio: uses platform master credentials (TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN).
        A row with no stored token is valid; no error is raised.

    Failure rules:
      - On DB error: raises RuntimeError. NEVER falls back to platform default on DB errors.
      - On decrypt failure: logs critical, records non-destructive marker metadata.token_error_at,
        and raises TokenDecryptionError. NEVER auto-mutates account status to token_error.

    Returns:
        (provider, account) — account is None only for the default-agency platform fallback.

    Raises:
        ValueError: If the agent has no row and fallback is disabled, if the provider is
                    unrecognized, or if a non-default agency has no active account/token.
        TokenDecryptionError: If access token decryption fails.
        RuntimeError: If a database error occurs.
    """
    from datetime import datetime, timezone
    from database.supabase_client import get_supabase
    from utils.crypto import decrypt_token, TokenDecryptionError

    sb = get_supabase()
    row = None

    # ── Step 1: Agent-specific active account ─────────────────────────────────
    if agent_id:
        try:
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
        except Exception as db_err:
            logger.error(
                "[Factory] DB error fetching agent-specific account for agency %s / agent %s: %s. "
                "Failing explicitly.",
                agency_id, agent_id, db_err,
            )
            raise RuntimeError(
                f"Database error resolving agent account for agency {agency_id}, agent {agent_id}: {db_err}"
            ) from db_err

        if not row:
            # Always log a warning — misconfigured agents should be visible in logs.
            logger.warning(
                "[Factory] No active WhatsApp account for agent_id=%s in agency %s. "
                "ALLOW_AGENCY_NUMBER_FALLBACK_FOR_AGENTS=%s.",
                agent_id,
                agency_id,
                settings.ALLOW_AGENCY_NUMBER_FALLBACK_FOR_AGENTS,
            )
            if not settings.ALLOW_AGENCY_NUMBER_FALLBACK_FOR_AGENTS:
                raise ValueError(
                    f"Agent {agent_id} has no active WhatsApp account for agency {agency_id}. "
                    "Set ALLOW_AGENCY_NUMBER_FALLBACK_FOR_AGENTS=true to fall back to the agency default."
                )
            # Fallback allowed — continue to agency-default query below.

    # ── Step 2: Agency-default active account (agent_id IS NULL) ──────────────
    if not row:
        try:
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
        except Exception as db_err:
            logger.error(
                "[Factory] DB error fetching agency-default account for agency %s (agent %s): %s. "
                "Failing explicitly (no platform default fallback on DB error).",
                agency_id, agent_id, db_err,
            )
            raise RuntimeError(
                f"Database error resolving communication account for agency {agency_id}: {db_err}"
            ) from db_err

    # ── Token decryption + provider instantiation ──────────────────────────────
    if row:
        row_provider = (row.get("provider") or "meta").lower().strip()
        raw_token = row.get("access_token_enc") or row.get("access_token") or ""
        decrypted_token = ""

        if raw_token:
            try:
                decrypted_token = decrypt_token(raw_token, account_id=row.get("id"))
            except Exception as dec_err:
                logger.critical(
                    "[Factory] Token decryption failed for account %s (agency %s): %s. "
                    "Recording non-destructive marker metadata.token_error_at; "
                    "failing explicitly (no status mutation, no silent fallback).",
                    row.get("id"), row.get("agency_id"), dec_err,
                )
                try:
                    meta = row.get("metadata") or {}
                    if not isinstance(meta, dict):
                        meta = {}
                    meta["token_error_at"] = datetime.now(timezone.utc).isoformat()
                    sb.table("communication_accounts").update({"metadata": meta}).eq("id", row["id"]).execute()
                except Exception as upd_err:
                    logger.error("[Factory] Failed to record token_error_at marker: %s", upd_err)
                raise TokenDecryptionError(
                    f"Account {row.get('id')} token decryption failed: {dec_err}"
                ) from dec_err

        if not decrypted_token:
            if row_provider in _PROVIDERS_REQUIRING_TOKEN:
                # meta / 360dialog: a per-row token is mandatory.
                is_default_agency = bool(
                    (settings.DEFAULT_AGENCY_ID and agency_id == settings.DEFAULT_AGENCY_ID)
                    or settings.ALLOW_PLATFORM_DEFAULT_FALLBACK
                )
                if is_default_agency:
                    env_token = settings.WHATSAPP_API_KEY or ""
                    if env_token:
                        logger.info(
                            "[Factory] Account %s has no stored token; "
                            "using WHATSAPP_API_KEY env fallback for default agency %s.",
                            row.get("id"), agency_id,
                        )
                        decrypted_token = env_token
                    else:
                        raise ValueError(
                            f"Account {row.get('id')} has no stored token and "
                            "WHATSAPP_API_KEY is not configured in environment."
                        )
                else:
                    logger.error(
                        "[Factory] Account %s for agency %s (provider=%r) has no valid token. "
                        "Failing explicitly — non-default agency cannot use shared credentials.",
                        row.get("id"), agency_id, row_provider,
                    )
                    raise ValueError(
                        f"Account {row.get('id')} for agency {agency_id} has no valid access token."
                    )
            else:
                # Twilio: uses platform master credentials — no per-row token is needed.
                logger.debug(
                    "[Factory] Account %s (provider=%r) requires no per-row token; "
                    "platform master credentials will be used.",
                    row.get("id"), row_provider,
                )

        account = CommunicationAccount(
            id=row["id"],
            agency_id=row["agency_id"],
            agent_id=row.get("agent_id"),
            channel=row.get("channel", "whatsapp"),
            provider=row.get("provider", "meta"),
            phone_number=row.get("phone_number", ""),
            external_account_id=row.get("external_account_id", ""),
            phone_number_id=row.get("phone_number_id", ""),
            access_token=decrypted_token,
            status=row.get("status", "active"),
            metadata=row.get("metadata", {}),
        )
        return get_whatsapp_provider(account), account

    # ── Step 3: DEFAULT_AGENCY_ID env fallback ────────────────────────────────
    # MUST come before legacy Twilio — the default agency must NEVER be silently
    # rerouted to a legacy dedicated Twilio number when a platform-level Meta
    # credential exists in the environment.
    is_default_agency = bool(
        (settings.DEFAULT_AGENCY_ID and agency_id == settings.DEFAULT_AGENCY_ID)
        or settings.ALLOW_PLATFORM_DEFAULT_FALLBACK
    )
    if is_default_agency:
        logger.warning(
            "[Factory] No active communication_account found for default agency %s (agent %s). "
            "Using platform default provider via environment settings.",
            agency_id,
            agent_id,
        )
        return get_whatsapp_provider(None), None

    # ── Step 4: Legacy dedicated Twilio number from agencies table ─────────────
    # Only reached for non-default agencies that have no communication_accounts row.
    try:
        ag_res = (
            sb.table("agencies")
            .select("id, dedicated_whatsapp_number, whatsapp_number_status")
            .eq("id", agency_id)
            .maybe_single()
            .execute()
        )
        if ag_res and hasattr(ag_res, "data") and ag_res.data:
            ag_data = ag_res.data
            dedicated_num = ag_data.get("dedicated_whatsapp_number")
            num_status = ag_data.get("whatsapp_number_status")
            if dedicated_num and num_status in ("active", "provisioned"):
                logger.info(
                    "[Factory] Found legacy provisioned Twilio dedicated number for agency %s (status=%s). "
                    "Routing via legacy Twilio path.",
                    agency_id,
                    num_status,
                )
                legacy_account = CommunicationAccount(
                    id=f"legacy-agency-{agency_id}",
                    agency_id=agency_id,
                    agent_id=None,
                    channel="whatsapp",
                    provider="twilio",
                    phone_number=dedicated_num.replace("whatsapp:", "").replace("+", "").strip(),
                    status="active",
                    metadata={"legacy_source": "agencies.dedicated_whatsapp_number"},
                )
                return get_whatsapp_provider(legacy_account), legacy_account
    except Exception as legacy_err:
        logger.error(
            "[Factory] Error checking legacy dedicated number for agency %s: %s",
            agency_id, legacy_err,
        )
        raise RuntimeError(
            f"Database error checking legacy agency dedicated number: {legacy_err}"
        ) from legacy_err

    # Step 5: No account found - fail explicitly


    logger.error(
        "[Factory] No active WhatsApp communication account found for agency %s (agent %s). "
        "Failing explicitly — non-default agency must connect their own WhatsApp account.",
        agency_id,
        agent_id,
    )
    raise ValueError(f"No active WhatsApp communication account found for agency {agency_id}.")

