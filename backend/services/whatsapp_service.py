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
      1. Agent's active BYON communication_accounts row (if agent_id provided)
      2. Agency's active default communication_accounts row (agent_id IS NULL)
      3. Agency's legacy provisioned Twilio dedicated number (agencies.dedicated_whatsapp_number)
      4. Default agency environment fallback (settings.DEFAULT_AGENCY_ID only)
      5. Fails explicitly with ValueError for non-default agencies without active account/number.
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


# ─── Window-Aware Smart Send (Gap 1) ─────────────────────────────────────────


async def send_whatsapp_smart(
    agency_id: str,
    lead_id: str,
    to_phone: str,
    body: str,
    agent_id: str | None = None,
    template_name: str = "andios_lead_first_contact",
    template_params: list[str] | None = None,
    last_inbound_at: str | None = None,
) -> dict:
    """
    Window-aware WhatsApp send for outbound AI messages addressed to a lead.

    Resolution order:
      1. If last_inbound_at is not supplied, it is fetched from leads.last_inbound_at.
      2. is_within_24h_window() is evaluated:
           True  → free-form text send via send_whatsapp_for_agency().
           False → attempt approved-template send:
                     a. Resolve provider + account via get_whatsapp_provider_for_agency().
                     b. Look up whatsapp_templates for an APPROVED row matching
                        waba_id (account.external_account_id) and template_name.
                     c. Found  → provider.send_message(template_name=..., template_params=...).
                     d. Missing → _queue_and_notify_agent() (Gap 2).
                        Returns {"status": "queued"}.

    Returns:
        {"status": "sent", "sid": ...}      on successful delivery
        {"status": "queued", "lead_id": ...} when queued due to no approved template
        {"status": "error", "error": ...}   on provider-level failure
    """
    from services.communication.template_service import (
        is_within_24h_window,
        get_approved_template_or_none,
    )
    from services.communication.provider_factory import get_whatsapp_provider_for_agency
    from database.supabase_client import get_supabase

    clean_phone = to_phone.replace("whatsapp:", "").replace("+", "").strip()

    # ── Step 1: Resolve last_inbound_at ──────────────────────────────────────
    if last_inbound_at is None and lead_id:
        try:
            sb = get_supabase()
            lead_res = (
                sb.table("leads")
                .select("last_inbound_at")
                .eq("id", lead_id)
                .limit(1)
                .execute()
            )
            if lead_res and lead_res.data:
                last_inbound_at = lead_res.data[0].get("last_inbound_at")
        except Exception as db_err:
            logger.warning(
                "[SmartSend] Could not fetch last_inbound_at for lead %s: %s — treating as closed window.",
                lead_id, db_err,
            )

    # ── Step 2: 24h-window check ─────────────────────────────────────────────
    in_window = is_within_24h_window(last_inbound_at)

    if in_window:
        logger.debug(
            "[SmartSend] Lead %s is within 24h window — sending free-form to %s****.",
            lead_id, clean_phone[-4:],
        )
        return await send_whatsapp_for_agency(agency_id, clean_phone, body, agent_id=agent_id)

    # ── Step 3: Outside window — attempt approved template send ──────────────
    logger.info(
        "[SmartSend] Lead %s outside 24h window — attempting template '%s'.",
        lead_id, template_name,
    )
    try:
        provider, account = await get_whatsapp_provider_for_agency(agency_id, agent_id=agent_id)
    except Exception as pf_err:
        logger.error(
            "[SmartSend] Could not resolve provider for agency %s lead %s: %s — queuing.",
            agency_id, lead_id, pf_err,
        )
        await _queue_and_notify_agent(agency_id, lead_id, clean_phone, body,
                                      template_name=template_name, reason="no_provider")
        return {"status": "queued", "lead_id": lead_id}

    # ── Case A: Env-fallback path (no communication_accounts row at all) ─────
    # Platform-level default: account is None. Template management is WABA-specific;
    # without a BYON account we cannot look up approved templates. Free-form send
    # via the resolved provider preserves the pre-Gap-1 behavior for agencies using
    # platform env tokens (e.g. the live default agency which has no BYON row).
    if account is None:
        logger.info(
            "[SmartSend] Lead %s: platform env fallback (account is None) — sending free-form.",
            lead_id,
        )
        try:
            result = await provider.send_message(clean_phone, body)
            if result.success:
                return {"status": "sent", "sid": result.message_id}
            logger.error(
                "[SmartSend] Lead %s: env-fallback free-form send failed: %s — queuing.",
                lead_id, result.error,
            )
        except Exception as fb_err:
            logger.error(
                "[SmartSend] Lead %s: exception in env-fallback send: %s — queuing.",
                lead_id, fb_err,
            )
        await _queue_and_notify_agent(
            agency_id, lead_id, clean_phone, body,
            template_name=template_name, agent_id=agent_id,
            reason="no_template",
        )
        return {"status": "queued", "lead_id": lead_id}

    # ── Case B: BYON account exists but missing external_account_id (WABA ID) ──
    # A real BYON row that is somehow missing its WABA ID (misconfigured, mid-reconnect,
    # or corrupted). Sending free-form on a customer-connected number outside the 24h
    # window will be REJECTED by Meta (error 131047). Must NOT silently free-form send;
    # log CRITICAL, queue message, and notify agent.
    waba_id = account.external_account_id or ""
    if not waba_id:
        logger.critical(
            "[SmartSend] BYON account %s for agency %s is missing external_account_id (WABA ID). "
            "Connection may be broken or misconfigured. Cannot send template; queuing message for lead %s.",
            account.id, agency_id, lead_id,
        )
        await _queue_and_notify_agent(
            agency_id, lead_id, clean_phone, body,
            template_name=template_name, agent_id=agent_id,
            reason="misconfigured_waba",
        )
        return {"status": "queued", "lead_id": lead_id}

    approved_template = None
    try:
        sb = get_supabase()
        approved_template = await get_approved_template_or_none(sb, waba_id, template_name)
    except Exception as tmpl_err:
        logger.warning(
            "[SmartSend] Template lookup failed for WABA %s: %s — queuing.", waba_id, tmpl_err
        )

    if approved_template:
        params = template_params or []
        try:
            result = await provider.send_message(
                clean_phone, body, template_name=template_name, template_params=params
            )
            if result.success:
                logger.info(
                    "[SmartSend] Lead %s: template '%s' delivered (waba=%s).",
                    lead_id, template_name, waba_id,
                )
                return {"status": "sent", "sid": result.message_id}
            logger.error(
                "[SmartSend] Lead %s: template send failed: %s — queuing.", lead_id, result.error
            )
        except Exception as send_err:
            logger.error(
                "[SmartSend] Lead %s: exception during template send: %s — queuing.", lead_id, send_err
            )

    # ── Step 4: No approved template or send failed → queue + notify ─────────
    await _queue_and_notify_agent(
        agency_id, lead_id, clean_phone, body,
        template_name=template_name,
        agent_id=agent_id,
        reason="no_template",
    )
    return {"status": "queued", "lead_id": lead_id}


# ─── Internal: Queue + Notify Agent (Gap 2) ───────────────────────────────────


async def _notify_agent_for_lead(
    agency_id: str,
    lead_id: str,
    to_phone: str,
    body: str,
    agent_id: str | None = None,
    reason: str = "no_template",
    alert_type: str = "queued",
) -> None:
    """
    Resolve assigned agent (or fallback to agency admin) and send a WhatsApp alert
    regarding a queued or failed outbound message.
    """
    from database.supabase_client import get_supabase
    sb = get_supabase()

    # 1. Resolve lead + assigned agent ────────────────────────────────────────
    lead_row: dict = {}
    assigned_agent_id: str | None = agent_id
    try:
        lead_res = sb.table("leads").select("name, phone, assigned_agent_id").eq("id", lead_id).limit(1).execute()
        if lead_res and lead_res.data:
            lead_row = lead_res.data[0]
            assigned_agent_id = lead_row.get("assigned_agent_id") or agent_id
    except Exception as lead_err:
        logger.warning("[QueueNotify] Could not fetch lead row for notify: %s", lead_err)

    # 2. Resolve agent phone ───────────────────────────────────────────────────
    agent_phone: str | None = None
    if assigned_agent_id:
        try:
            agent_res = (
                sb.table("agents")
                .select("name, phone, whatsapp_number")
                .eq("id", assigned_agent_id)
                .limit(1)
                .execute()
            )
            if agent_res and agent_res.data:
                a = agent_res.data[0]
                agent_phone = a.get("whatsapp_number") or a.get("phone")
        except Exception as ag_err:
            logger.warning("[QueueNotify] Could not fetch agent row for notify: %s", ag_err)
    else:
        # Fallback: agency admin/owner agent
        try:
            admin_res = (
                sb.table("agents")
                .select("id, name, phone, whatsapp_number")
                .eq("agency_id", agency_id)
                .eq("is_admin", True)
                .limit(1)
                .execute()
            )
            if admin_res and admin_res.data:
                a = admin_res.data[0]
                agent_phone = a.get("whatsapp_number") or a.get("phone")
                assigned_agent_id = a.get("id")
        except Exception as admin_err:
            logger.warning("[QueueNotify] Could not fetch admin agent for notify: %s", admin_err)

    if not agent_phone:
        logger.warning(
            "[QueueNotify] No agent phone found for lead %s (agency %s) — no alert sent.",
            lead_id, agency_id,
        )
        return

    # 3. Format message and send ───────────────────────────────────────────────
    lead_name = lead_row.get("name", "Unknown") if lead_row else "Unknown"
    lead_phone_display = (lead_row.get("phone", "") or to_phone)[-4:] if (lead_row.get("phone") or to_phone) else "????"

    if alert_type == "drain_failed":
        notify_msg = (
            f"⚠️ *Queued Message Delivery Failed*\n\n"
            f"A previously queued message to lead *{lead_name}* (***{lead_phone_display}) failed to send "
            f"even after the 24h window reopened.\n"
            f"📋 Error: {reason.replace('_', ' ')}\n"
            f"💬 Message: _{body[:200]}_\n\n"
            f"Please follow up manually with this lead on WhatsApp."
        )
    else:
        notify_msg = (
            f"📬 *Queued Message Alert*\n\n"
            f"A message to lead *{lead_name}* (***{lead_phone_display}) could not be sent.\n"
            f"📋 Reason: {reason.replace('_', ' ')}\n"
            f"💬 Queued message: _{body[:200]}_\n\n"
            f"No approved WhatsApp template exists for this WABA.\n"
            f"Please send the message manually or connect approved templates in your account settings."
        )

    try:
        await send_whatsapp_for_agency(agency_id, agent_phone, notify_msg)
        logger.info("[QueueNotify] Sent %s alert to agent for lead %s.", alert_type, lead_id)
    except Exception as notify_err:
        logger.error(
            "[QueueNotify] Failed to send alert to agent for lead %s: %s", lead_id, notify_err
        )


async def _queue_and_notify_agent(
    agency_id: str,
    lead_id: str,
    to_phone: str,
    body: str,
    template_name: str | None = None,
    template_params: list[str] | None = None,
    agent_id: str | None = None,
    reason: str = "no_template",
) -> None:
    """
    Persist a blocked outbound message to whatsapp_outbound_queue and send a
    WhatsApp alert to the lead's assigned agent (or the agency admin if none).

    Reuses the same WhatsApp-to-agent notification pattern as the handover alert
    in routers/webhooks.py (lines 1270-1290) — no new notification channel needed.

    NOTE: whatsapp_outbound_queue must exist before this runs in production
    (see database/schema_v12_whatsapp_queue.sql). In tests the table is mocked.
    """
    from database.supabase_client import get_supabase
    sb = get_supabase()

    # 1. Persist to queue ─────────────────────────────────────────────────────
    try:
        sb.table("whatsapp_outbound_queue").insert({
            "agency_id": agency_id,
            "agent_id": agent_id,
            "lead_id": lead_id,
            "to_phone": to_phone,
            "body": body,
            "template_name": template_name,
            "template_params": template_params or [],
            "status": "pending",
            "reason": reason,
        }).execute()
        logger.info(
            "[Queue] Outbound message queued for lead %s (agency %s, reason=%s).",
            lead_id, agency_id, reason,
        )
    except Exception as q_err:
        logger.error(
            "[Queue] Failed to persist queued message for lead %s: %s",
            lead_id, q_err,
        )

    # 2. Notify assigned agent / admin ────────────────────────────────────────
    await _notify_agent_for_lead(
        agency_id,
        lead_id,
        to_phone,
        body,
        agent_id=agent_id,
        reason=reason,
        alert_type="queued",
    )


# ─── Auto-Drain: send queued messages when 24h window reopens (Gap 2) ─────────


async def drain_outbound_queue_for_lead(
    lead_id: str,
    agency_id: str,
    agent_id: str | None = None,
) -> int:
    """
    Called immediately after leads.last_inbound_at is updated (i.e., the lead
    just messaged in, reopening the 24h customer-service window).

    Atomically claims all 'pending' rows from whatsapp_outbound_queue for this lead
    via claim_queued_messages RPC (transitioning status='draining' to eliminate
    concurrent drain race conditions) and attempts to send each as a free-form
    text message (now within window).

    If delivery fails during drain, marks status='failed' and notifies the assigned
    agent so the message does not sit silently without visibility.

    Returns the number of messages successfully drained.
    """
    from datetime import datetime, timezone
    from database.supabase_client import get_supabase
    sb = get_supabase()

    try:
        claim_res = sb.rpc("claim_queued_messages", {"p_lead_id": lead_id}).execute()
        pending = claim_res.data or []
    except Exception as claim_err:
        logger.error("[Drain] Failed to claim queued messages for lead %s: %s", lead_id, claim_err)
        return 0

    if not pending:
        return 0

    logger.info("[Drain] Draining %d queued message(s) for lead %s.", len(pending), lead_id)
    drained = 0

    for item in pending:
        try:
            result = await send_whatsapp_for_agency(
                item.get("agency_id") or agency_id,
                item["to_phone"],
                item["body"],
                agent_id=item.get("agent_id") or agent_id,
            )
            if result.get("status") == "sent":
                sb.table("whatsapp_outbound_queue").update({
                    "status": "sent",
                    "sent_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", item["id"]).execute()
                drained += 1
                logger.info("[Drain] Queued message %s sent for lead %s.", item["id"], lead_id)
            else:
                sb.table("whatsapp_outbound_queue").update({
                    "status": "failed",
                }).eq("id", item["id"]).execute()
                logger.warning(
                    "[Drain] Queued message %s for lead %s could not be sent: %s — notifying agent.",
                    item["id"], lead_id, result.get("error"),
                )
                await _notify_agent_for_lead(
                    item.get("agency_id") or agency_id,
                    lead_id,
                    item["to_phone"],
                    item["body"],
                    agent_id=item.get("agent_id") or agent_id,
                    reason=str(result.get("error") or "send_failed"),
                    alert_type="drain_failed",
                )
        except Exception as send_err:
            try:
                sb.table("whatsapp_outbound_queue").update({
                    "status": "failed",
                }).eq("id", item["id"]).execute()
            except Exception:
                pass
            logger.error(
                "[Drain] Exception sending queued message %s for lead %s: %s — notifying agent.",
                item["id"], lead_id, send_err,
            )
            try:
                await _notify_agent_for_lead(
                    item.get("agency_id") or agency_id,
                    lead_id,
                    item["to_phone"],
                    item["body"],
                    agent_id=item.get("agent_id") or agent_id,
                    reason=str(send_err),
                    alert_type="drain_failed",
                )
            except Exception:
                pass

    return drained
