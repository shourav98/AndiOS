"""
Voice Service — Central DID Inbound Call Routing & BYON Voice

Responsibilities:
  1. Resolve which agent/agency an inbound forwarded call belongs to.
     Resolution order (multi-tier):
       Tier 1 — ForwardedFrom exact match via communication_accounts (voice channel)
       Tier 2 — CRM recent-contact graph lookup (caller's From number matched to active leads)
       Tier 3 — Fallback: generic AI receptionist context (no match found)

  2. Generate TwiML that bridges a live Twilio inbound call to Vapi AI via
     BYO SIP Trunk using the <Dial><Sip> verb.
     Custom SIP headers (X-Agent-Id, X-Agency-Id, X-Agent-Name) are injected
     so Vapi can dynamically set the assistant's persona via prompt variables.

     ⚠️  BYO SIP Trunk mode ONLY — never use Twilio "Simple Number Import" into
         Vapi. Simple import bypasses our backend webhook entirely, so custom
         SIP headers never reach Vapi and agent resolution silently fails.

  3. Initiate and confirm Twilio OutgoingCallerIds verification for BYON
     outbound caller ID — lets agents' personal numbers appear as caller ID
     on calls AndiOS makes on their behalf.

Dependencies:
  - Supabase (communication_accounts, leads, conversations, agents)
  - Twilio REST API (OutgoingCallerIds)
  - config.settings (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, VAPI_SIP_URI)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import TypedDict

import httpx
from config import settings
from database.supabase_client import get_supabase

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

# Vapi BYO SIP URI structure per Vapi official specification:
#   sip:{phone_number_or_id}@{credential_id}.sip.vapi.ai (or .sip.eu.vapi.ai)
# The credential_id must be in the subdomain position so Vapi can identify
# which BYO SIP Trunk account/credential the INVITE belongs to.
# The user part (@-before) is the dialed number or configured trunk identifier.
# Custom SIP headers (X-Agent-Id, X-Agency-Id) pass persona context into Vapi's prompt.

# How far back to scan CRM conversations for Tier 2 graph lookup (hours)
CRM_LOOKUP_WINDOW_HOURS = 48

# Fallback receptionist text — used in Tier 3 TwiML when no agent is identified
FALLBACK_GREETING = (
    "Hello! Thank you for calling. "
    "Which agent or property are you trying to reach today?"
)


# ─── Types ────────────────────────────────────────────────────────────────────

class InboundCallContext(TypedDict):
    """Resolved context for an inbound forwarded call."""
    agency_id: str | None
    agent_id: str | None
    agent_name: str
    custom_greeting: str | None
    resolution_tier: int            # 1 = ForwardedFrom, 2 = CRM, 3 = Fallback
    comm_account_id: str | None


# ─── Tier 1: ForwardedFrom Exact Match ────────────────────────────────────────

async def _resolve_by_forwarded_from(
    forwarded_from: str,
) -> InboundCallContext | None:
    """
    Tier 1 resolution: exact match on ForwardedFrom against communication_accounts
    (channel='voice') using the resolve_agent_by_voice_number() DB RPC.

    Returns None if no active voice account is registered for that number.
    """
    try:
        sb = get_supabase()
        result = sb.rpc(
            "resolve_agent_by_voice_number",
            {"p_phone": forwarded_from},
        ).execute()

        if result.data:
            row = result.data[0]
            logger.info(
                f"[Voice Tier1] ForwardedFrom={forwarded_from} resolved to "
                f"agent={row.get('agent_id')} agency={row.get('agency_id')}"
            )
            return InboundCallContext(
                agency_id=row.get("agency_id"),
                agent_id=row.get("agent_id"),
                agent_name=row.get("agent_name") or "Your Agent",
                custom_greeting=row.get("custom_greeting"),
                resolution_tier=1,
                comm_account_id=row.get("comm_account_id"),
            )
    except Exception as exc:
        logger.warning(f"[Voice Tier1] DB RPC failed: {exc}")
    return None


# ─── Tier 2: CRM Recent-Contact Graph Lookup ──────────────────────────────────

async def _resolve_by_crm_graph(
    from_phone: str,
) -> InboundCallContext | None:
    """
    Tier 2 resolution: when ForwardedFrom is missing (carrier stripped the
    SIP Diversion header), try to match the caller's number (From) against
    leads/conversations to infer which agent the call is for.

    Finds the agent who last had a WhatsApp/SMS conversation with this caller
    within CRM_LOOKUP_WINDOW_HOURS hours.
    """
    try:
        sb = get_supabase()
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=CRM_LOOKUP_WINDOW_HOURS)
        ).isoformat()

        # Normalize phone — strip leading + so it matches DB storage
        normalized = from_phone.lstrip("+")

        # Find the most recent lead matching this phone
        leads_res = (
            sb.table("leads")
            .select("id, agency_id, assigned_agent_id, phone")
            .ilike("phone", f"%{normalized[-9:]}")   # last 9 digits, tolerant
            .order("created_at", desc=True)
            .limit(5)
            .execute()
        )
        if not leads_res.data:
            return None

        # Among matching leads, find one that had a recent conversation
        for lead in leads_res.data:
            conv_res = (
                sb.table("conversations")
                .select("id, agent_id, agency_id, created_at")
                .eq("lead_id", lead["id"])
                .gte("created_at", cutoff)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if conv_res.data:
                conv = conv_res.data[0]
                agent_id = conv.get("agent_id") or lead.get("assigned_agent_id")
                agency_id = conv.get("agency_id") or lead.get("agency_id")

                # Fetch agent name
                agent_name = "Your Agent"
                if agent_id:
                    ag_res = (
                        sb.table("agents")
                        .select("name")
                        .eq("id", agent_id)
                        .limit(1)
                        .execute()
                    )
                    if ag_res.data:
                        agent_name = ag_res.data[0].get("name") or agent_name

                logger.info(
                    f"[Voice Tier2] From={from_phone} matched via CRM to "
                    f"agent={agent_id} agency={agency_id}"
                )
                return InboundCallContext(
                    agency_id=agency_id,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    custom_greeting=None,
                    resolution_tier=2,
                    comm_account_id=None,
                )
    except Exception as exc:
        logger.warning(f"[Voice Tier2] CRM lookup failed: {exc}")
    return None


# ─── Public: Resolve Inbound Caller ───────────────────────────────────────────

async def resolve_inbound_caller(
    from_phone: str,
    forwarded_from: str | None,
    to_number: str,
) -> InboundCallContext:
    """
    Resolve the agent/agency for an inbound forwarded call.

    Resolution order:
      Tier 1 — ForwardedFrom exact match (fast DB RPC)
      Tier 2 — CRM recent-contact graph match on From phone
      Tier 3 — Fallback generic AI receptionist

    Args:
        from_phone:     Caller's phone number (E.164, from Twilio 'From' param).
        forwarded_from: Agent's number that forwarded the call (from Twilio
                        'ForwardedFrom' param). May be None if carrier stripped
                        the SIP Diversion header.
        to_number:      Central platform DID that received the call ('To' param).

    Returns:
        InboundCallContext with resolution_tier indicating how the match was made.
    """
    # Tier 1
    if forwarded_from:
        normalized_ff = forwarded_from.lstrip("+")
        ctx = await _resolve_by_forwarded_from(normalized_ff)
        if ctx:
            return ctx
        # Try with + prefix as stored
        ctx = await _resolve_by_forwarded_from(forwarded_from)
        if ctx:
            return ctx

    # Tier 2
    ctx = await _resolve_by_crm_graph(from_phone)
    if ctx:
        return ctx

    # Tier 3 — Fallback generic receptionist
    logger.info(
        f"[Voice Tier3] No agent resolved for From={from_phone} "
        f"ForwardedFrom={forwarded_from}. Using generic AI receptionist."
    )
    return InboundCallContext(
        agency_id=None,
        agent_id=None,
        agent_name="",
        custom_greeting=None,
        resolution_tier=3,
        comm_account_id=None,
    )


# ─── TwiML Generator (BYO SIP Trunk) ─────────────────────────────────────────

def build_vapi_sip_uri(
    destination_number: str | None = None,
    credential_id: str | None = None,
    domain: str | None = None,
) -> str:
    """
    Build Vapi BYO SIP Trunk URI according to Vapi's official specification:
      sip:{phone_number_or_id}@{credential_id}.sip.vapi.ai

    The credential_id MUST be present as a subdomain so Vapi can identify
    the BYO SIP trunk account/credential. The user part (@-before) is the
    phone number or trunk identifier.

    Args:
        destination_number: Dialed phone number or central DID.
        credential_id: Vapi SIP trunk credential ID (falls back to settings.VAPI_SIP_CREDENTIAL_ID).
        domain: SIP domain (falls back to settings.VAPI_SIP_DOMAIN, default sip.vapi.ai).

    Returns:
        Base SIP URI string e.g. "sip:97141234567@cred-123.sip.vapi.ai"
    """
    cred_id = credential_id or getattr(settings, "VAPI_SIP_CREDENTIAL_ID", "") or ""
    sip_domain = domain or getattr(settings, "VAPI_SIP_DOMAIN", "sip.vapi.ai") or "sip.vapi.ai"

    # User part: clean phone number or central DID
    raw_dest = destination_number or getattr(settings, "CENTRAL_INBOUND_DID", "") or "andios-voice"
    dest_clean = raw_dest.strip().lstrip("+").replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if not dest_clean:
        dest_clean = "andios-voice"

    if cred_id:
        host = f"{cred_id}.{sip_domain}"
    else:
        # Fallback if credential_id is not yet set (e.g. dev/local testing)
        host = sip_domain
        logger.warning(
            "[Voice BYON] VAPI_SIP_CREDENTIAL_ID is not configured. "
            "Calls may fail routing at Vapi without the credential_id subdomain."
        )

    return f"sip:{dest_clean}@{host}"


def generate_vapi_sip_twiml(
    ctx: InboundCallContext,
    call_sid: str,
    to_number: str | None = None,
    credential_id: str | None = None,
) -> str:
    """
    Generate TwiML that bridges a Twilio call to Vapi AI via BYO SIP Trunk.

    Uses <Dial><Sip> verb with custom X- SIP headers to pass agent/agency
    context into Vapi. Vapi reads these as prompt template variables:
      {{Agent-Id}}, {{Agency-Id}}, {{Agent-Name}}

    ⚠️  This requires the Twilio number to be configured as a BYO SIP Trunk
        in Vapi — NOT via Twilio Simple Number Import, which bypasses our
        backend and drops all custom SIP headers.

    The SIP URI format follows Vapi's required structure:
      sip:{to_number}@{credential_id}.sip.vapi.ai?X-Agent-Id=...&X-Agency-Id=...

    For Tier 3 (fallback), the SIP dial still occurs but agent headers are empty,
    causing Vapi's pre-configured assistant to run the generic receptionist persona.
    """
    sip_uri = build_vapi_sip_uri(
        destination_number=to_number,
        credential_id=credential_id,
    )

    agent_id = ctx.get("agent_id") or ""
    agency_id = ctx.get("agency_id") or ""
    agent_name = ctx.get("agent_name") or ""
    tier = ctx.get("resolution_tier", 3)

    # URL-encode params in SIP URI
    import urllib.parse
    params = {}
    if agent_id:
        params["X-Agent-Id"] = agent_id
    if agency_id:
        params["X-Agency-Id"] = agency_id
    if agent_name:
        params["X-Agent-Name"] = urllib.parse.quote(agent_name)
    if call_sid:
        params["X-Call-Sid"] = call_sid
    params["X-Resolution-Tier"] = str(tier)

    if params:
        sip_uri += "?" + "&".join(f"{k}={v}" for k, v in params.items())

    import html
    sip_uri_escaped = html.escape(sip_uri)

    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        '  <Dial timeout="30" record="record-from-answer">\n'
        f"    <Sip>{sip_uri_escaped}</Sip>\n"
        "  </Dial>\n"
        "</Response>"
    )
    logger.info(
        f"[Voice TwiML] Generated SIP TwiML for CallSid={call_sid} "
        f"tier={tier} agent_id={agent_id or 'none'}"
    )
    return twiml


# ─── Outbound Caller ID — BYON Verification ───────────────────────────────────

async def initiate_outbound_caller_id_verification(
    agent_phone: str,
    agency_id: str,
    agent_id: str,
) -> dict:
    """
    Start Twilio OutgoingCallerIds verification for an agent's personal number.

    Twilio calls (or SMS) the agent with a 6-digit code.
    The agent reads the code and submits it via confirm_outbound_caller_id_verification().

    Returns:
        dict with 'validation_code' (for confirmation) and 'call_sid'.
    """
    account_sid = (getattr(settings, "TWILIO_ACCOUNT_SID", "") or "").strip()
    auth_token = (getattr(settings, "TWILIO_AUTH_TOKEN", "") or "").strip()

    if not account_sid or not auth_token:
        logger.warning("[Voice BYON] Twilio credentials not configured — mock verification")
        return {
            "status": "mock",
            "message": "Twilio not configured — skipping real verification",
            "validation_code": "MOCK123",
        }

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/OutgoingCallerIds.json"
    payload = {
        "PhoneNumber": f"+{agent_phone.lstrip('+')}",
        "FriendlyName": f"AndiOS Agent ({agent_id[:8]})",
        "CallDelay": 0,
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(url, data=payload, auth=(account_sid, auth_token))
            resp.raise_for_status()
            data = resp.json()
            validation_code = data.get("validation_code", "")
            call_sid = data.get("call_sid", "")
            logger.info(
                f"[Voice BYON] OutgoingCallerId verification initiated for "
                f"agent={agent_id} phone={agent_phone} code={validation_code}"
            )
            return {
                "status": "verification_initiated",
                "validation_code": validation_code,
                "call_sid": call_sid,
                "message": (
                    f"Twilio will call {agent_phone} with a 6-digit code. "
                    "Submit it via the confirm endpoint."
                ),
            }
    except httpx.HTTPStatusError as e:
        logger.error(f"[Voice BYON] Twilio verification failed: {e.response.text}")
        return {"status": "error", "message": str(e)}
    except Exception as e:
        logger.error(f"[Voice BYON] Verification error: {e}")
        return {"status": "error", "message": str(e)}


async def confirm_outbound_caller_id_verification(
    agent_phone: str,
    validation_code: str,
    agency_id: str,
    agent_id: str,
) -> dict:
    """
    Confirm Twilio OutgoingCallerIds verification code.

    On success, stores the OutgoingCallerId SID in communication_accounts
    (verified_caller_id_sid column) so outbound calls can reference it.

    Returns dict with 'status' and 'outgoing_caller_id_sid'.
    """
    account_sid = (getattr(settings, "TWILIO_ACCOUNT_SID", "") or "").strip()
    auth_token = (getattr(settings, "TWILIO_AUTH_TOKEN", "") or "").strip()

    if not account_sid or not auth_token:
        logger.warning("[Voice BYON] Twilio credentials not configured — mock confirm")
        return {"status": "mock", "outgoing_caller_id_sid": "PHXXXMOCK"}

    # Fetch OutgoingCallerIds list to find the pending entry for this phone
    list_url = (
        f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/OutgoingCallerIds.json"
        f"?PhoneNumber=%2B{agent_phone.lstrip('+')}"
    )
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(list_url, auth=(account_sid, auth_token))
            resp.raise_for_status()
            data = resp.json()
            items = data.get("outgoing_caller_ids", [])
            if not items:
                return {
                    "status": "not_found",
                    "message": (
                        "No pending verification found for this number. "
                        "Please initiate verification first."
                    ),
                }
            caller_id_sid = items[0]["sid"]
            # Store the verified SID back in communication_accounts
            sb = get_supabase()
            sb.table("communication_accounts").upsert(
                {
                    "agency_id": agency_id,
                    "agent_id": agent_id,
                    "channel": "voice",
                    "provider": "twilio",
                    "phone_number": agent_phone.lstrip("+"),
                    "verified_caller_id_sid": caller_id_sid,
                    "status": "active",
                    "forwarding_status": "not_configured",
                },
                on_conflict="agency_id,agent_id,channel",
            ).execute()

            logger.info(
                f"[Voice BYON] Caller ID verified: agent={agent_id} "
                f"sid={caller_id_sid}"
            )
            return {
                "status": "verified",
                "outgoing_caller_id_sid": caller_id_sid,
                "message": (
                    f"Number {agent_phone} is now verified as an outbound caller ID."
                ),
            }
    except httpx.HTTPStatusError as e:
        logger.error(f"[Voice BYON] Confirm failed: {e.response.text}")
        return {"status": "error", "message": str(e)}
    except Exception as e:
        logger.error(f"[Voice BYON] Confirm error: {e}")
        return {"status": "error", "message": str(e)}
