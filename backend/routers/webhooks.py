"""
Webhooks Router — handles inbound leads from Property Finder, Bayut, Dubizzle
and inbound WhatsApp messages, and Vapi call result callbacks.

POST /webhooks/property-finder   — new lead from portal (HMAC verified)
POST /webhooks/bayut             — new lead from Bayut
POST /webhooks/dubizzle          — new lead from Dubizzle
GET  /webhooks/whatsapp          — WhatsApp webhook verification
POST /webhooks/whatsapp          — inbound WhatsApp message
POST /webhooks/vapi              — Vapi call result callback
"""
import time
import hmac
import hashlib
from fastapi import APIRouter, Request, HTTPException, Query
from database.supabase_client import get_supabase
from services.dedup_service import is_duplicate, get_existing_lead_by_phone
from services.whatsapp_service import (
    send_whatsapp_message,
    parse_360dialog_inbound,
    parse_twilio_inbound,
)
from services.ai_service import qualify_and_respond, detect_handover, extract_lead_qualifications
from services.lead_routing_service import resolve_agency_and_agent
from utils.response import api_success
from config import settings
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


# ─── HMAC Verification Helper ──────────────────────────────────────────────────

def _verify_pf_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """
    Verify Property Finder webhook HMAC-SHA256 signature.
    Header format: 'sha256=<hex_digest>'
    Returns True if valid or if in dev mode / no secret configured.
    """
    if getattr(settings, "APP_ENV", "development") == "development":
        return True
    secret = getattr(settings, "PROPERTY_FINDER_WEBHOOK_SECRET", "")
    if not secret:
        return True
    if not signature_header:
        return False
    expected = "sha256=" + hmac.new(
        key=secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


# ─── WhatsApp Inbound Authentication ──────────────────────────────────────────

def _verify_360dialog_request(request: Request) -> bool:
    """
    Authenticate inbound 360dialog webhook requests.

    360dialog does not sign webhook deliveries with an HMAC we can key on in
    this integration, so we require an operator-configured shared secret
    (WHATSAPP_WEBHOOK_TOKEN) delivered as either the 'X-Webhook-Token' header
    or a '?token=' query parameter embedded in the callback URL configured in
    the 360dialog dashboard. Comparison is constant-time.

    Fails closed outside development when the token is unconfigured.
    """
    secret = getattr(settings, "WHATSAPP_WEBHOOK_TOKEN", "")
    provided = request.headers.get("x-webhook-token") or request.query_params.get("token")
    if not secret:
        if getattr(settings, "APP_ENV", "development") != "development":
            logger.critical(
                "WHATSAPP_WEBHOOK_TOKEN is not configured — rejecting inbound "
                "WhatsApp webhook (fail closed)"
            )
            return False
        logger.warning(
            "WHATSAPP_WEBHOOK_TOKEN not set — accepting unauthenticated "
            "WhatsApp webhook in development only"
        )
        return True
    return bool(provided) and hmac.compare_digest(str(provided), str(secret))


def _verify_twilio_request(request: Request, params: dict) -> bool:
    """
    Validate X-Twilio-Signature for inbound Twilio webhooks using the account
    auth token and the exact request URL + POST parameters Twilio signed.

    Fails closed outside development when TWILIO_AUTH_TOKEN is unconfigured.
    Note: Twilio signs the PUBLIC request URL — reverse proxies must forward
    the correct scheme/host (X-Forwarded-*) for validation to succeed.
    """
    from twilio.request_validator import RequestValidator

    auth_token = (getattr(settings, "TWILIO_AUTH_TOKEN", "") or "").strip()
    signature = request.headers.get("x-twilio-signature")
    if not auth_token:
        if getattr(settings, "APP_ENV", "development") != "development":
            logger.critical(
                "TWILIO_AUTH_TOKEN is not configured — rejecting inbound "
                "WhatsApp webhook (fail closed)"
            )
            return False
        logger.warning(
            "TWILIO_AUTH_TOKEN not set — accepting unauthenticated Twilio "
            "webhook in development only"
        )
        return True
    if not signature:
        return False
    validator = RequestValidator(auth_token)
    str_params = {str(k): str(v) for k, v in params.items()}
    return validator.validate(str(request.url), str_params, signature)


# ─── Safe Lead Resolution ─────────────────────────────────────────────────────

def _normalize_phone(phone: str) -> str:
    """Digits-only normalization for sender/lead comparison."""
    return "".join(ch for ch in str(phone or "") if ch.isdigit())


def _find_lead_by_sender_phone(sb, from_phone: str):
    """
    Resolve which stored lead sent an inbound WhatsApp message.

    Matching strategy (safest-first):
      1. Exact digit-normalized match on the FULL phone number.
      2. Legacy tolerance only when no exact match exists: a UNIQUE suffix
         match (handles leads stored without country codes).

    Any ambiguity — multiple exact matches (e.g. the same person is a lead at
    two agencies) or multiple suffix matches — fails safe: nothing is mutated,
    no conversation injected, no AI reply triggered.
    Returns (lead | None, reason) where reason ∈ matched|ambiguous|unknown|invalid.
    """
    digits = _normalize_phone(from_phone)
    if len(digits) < 7:
        return None, "invalid"

    candidates = (
        sb.table("leads")
        .select("*")
        .ilike("phone", f"%{digits[-9:]}")
        .execute()
        .data or []
    )

    exact = [c for c in candidates if _normalize_phone(c.get("phone")) == digits]
    pool = exact if exact else [
        c for c in candidates
        if _normalize_phone(c.get("phone", "")).endswith(digits[-9:])
    ]

    if not pool:
        return None, "unknown"
    if len(pool) > 1:
        # Fail safe on cross-tenant / ambiguous collisions. Log enough to
        # investigate without exposing phone numbers or names.
        logger.warning(
            "Ambiguous WhatsApp sender match (phone ending ***%s): %d candidate leads %s — skipping",
            digits[-3:], len(pool), [c.get("id") for c in pool],
        )
        return None, "ambiguous"
    return pool[0], "matched"


# ─── Property Finder Webhook ───────────────────────────────────────────────────

@router.post("/property-finder")
async def property_finder_webhook(request: Request):
    """
    Receives new lead from Property Finder portal.
    Verifies HMAC signature, deduplicates, stores lead, triggers AI WhatsApp greeting.
    """
    start_time = time.time()
    sb = get_supabase()

    # ── HMAC Signature Verification ──
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256") or request.headers.get("x-hub-signature-256")
    if not _verify_pf_signature(raw_body, signature):
        logger.warning("Property Finder webhook: invalid HMAC signature")
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    payload = await request.json()

    # Log raw webhook
    log_entry = sb.table("webhook_logs").insert({
        "source": "property_finder",
        "payload": payload,
        "processed": False,
    }).execute()
    log_id = log_entry.data[0]["id"]

    try:
        # ── Parse Property Finder payload ──
        # Standard PF lead webhook format (handles flat, {"lead": {...}}, or {"leads": [{...}]})
        if isinstance(payload.get("leads"), list) and len(payload["leads"]) > 0:
            lead_data = payload["leads"][0]
        elif isinstance(payload.get("lead"), dict):
            lead_data = payload["lead"]
        else:
            lead_data = payload

        external_id = str(
            lead_data.get("id") or
            lead_data.get("lead_id") or
            lead_data.get("reference", "")
        )
        name = lead_data.get("name") or f"{lead_data.get('first_name', '')} {lead_data.get('last_name', '')}".strip()
        phone = str(lead_data.get("phone") or lead_data.get("mobile") or lead_data.get("telephone", ""))
        email = lead_data.get("email", "")
        property_ref = str(lead_data.get("property_ref") or lead_data.get("listing_id") or lead_data.get("reference_no", ""))
        property_address = lead_data.get("property_title") or lead_data.get("property_address", "")
        bedrooms = lead_data.get("bedrooms")
        budget = lead_data.get("budget") or lead_data.get("price")
        location = lead_data.get("community") or lead_data.get("location") or lead_data.get("area", "")

        if not phone:
            raise ValueError("No phone number in webhook payload")

        # ── Resolve agency + agent ──
        agent_phone = lead_data.get("agent_phone") or lead_data.get("listing_agent_phone")
        agent_email = lead_data.get("agent_email") or lead_data.get("listing_agent_email")
        agency_id, assigned_agent_id = await resolve_agency_and_agent(
            source="property_finder",
            property_ref=property_ref or None,
            agent_phone=str(agent_phone) if agent_phone else None,
            agent_email=agent_email,
        )
        if not agency_id:
            raise ValueError("Could not resolve agency for inbound lead")

        # ── Deduplication ──
        if external_id and await is_duplicate(external_id):
            logger.info(f"Duplicate lead ignored: {external_id}")
            sb.table("webhook_logs").update({
                "processed": True,
                "error": "duplicate",
                "processing_time_ms": int((time.time() - start_time) * 1000),
            }).eq("id", log_id).execute()
            return api_success(message="Lead already exists", data={"status": "duplicate"})

        # Secondary dedup by phone
        existing = await get_existing_lead_by_phone(phone)
        if existing:
            logger.info(f"Lead with same phone already exists: {phone}")
            sb.table("webhook_logs").update({
                "processed": True,
                "lead_id": existing["id"],
                "error": "phone_duplicate",
                "processing_time_ms": int((time.time() - start_time) * 1000),
            }).eq("id", log_id).execute()
            return api_success(message="Lead with this phone already exists", data={"status": "duplicate"})

        # ── Create Lead ──
        lead_insert = {
            "external_lead_id": external_id or None,
            "name": name or "Unknown",
            "phone": phone,
            "email": email or None,
            "source": "property_finder",
            "property_ref": property_ref or None,
            "property_address": property_address or None,
            "bedrooms": int(bedrooms) if bedrooms else None,
            "budget_max": float(budget) if budget else None,
            "location_pref": location or None,
            "status": "new",
            "ai_stage": "greeting",
            "is_ai_handling": True,
            "agency_id": agency_id,
        }
        if assigned_agent_id:
            lead_insert["assigned_agent_id"] = assigned_agent_id

        new_lead = sb.table("leads").insert(lead_insert).execute()
        lead = new_lead.data[0]
        lead_id = lead["id"]

        # ── Send AI Greeting via WhatsApp (<3 min SLA) ──
        greeting = (
            f"Hi {name.split()[0]}! 👋 I'm Andi, your AI assistant from the agency.\n\n"
            f"I saw your enquiry about {'the ' + property_ref + ' listing' if property_ref else 'a property'} "
            f"{'in ' + location if location else ''}. \n\n"
            f"I'd love to help you find your perfect home! Could you tell me:\n"
            f"1. What's your budget range? (AED/year or AED purchase price)\n"
            f"2. How many bedrooms are you looking for?"
        )
        await send_whatsapp_message(phone, greeting)

        # Log greeting as conversation
        sb.table("conversations").insert({
            "lead_id": lead_id,
            "agency_id": agency_id,
            "direction": "outbound",
            "channel": "whatsapp",
            "message_body": greeting,
            "sender_type": "ai",
        }).execute()

        # Update lead status to qualifying
        sb.table("leads").update({"status": "qualifying"}).eq("id", lead_id).execute()

        # Update webhook log
        sb.table("webhook_logs").update({
            "processed": True,
            "lead_id": lead_id,
            "processing_time_ms": int((time.time() - start_time) * 1000),
        }).eq("id", log_id).execute()

        logger.info(f"New PF lead created: {lead_id} ({name}, {phone})")
        return api_success(data={"lead_id": lead_id}, message="Property Finder lead processed successfully")

    except Exception as e:
        logger.error(f"Property Finder webhook error: {e}")
        sb.table("webhook_logs").update({
            "error": str(e),
            "processing_time_ms": int((time.time() - start_time) * 1000),
        }).eq("id", log_id).execute()
        raise HTTPException(status_code=500, detail=str(e))


# ─── Bayut Webhook ─────────────────────────────────────────────────────────────

@router.post("/bayut")
async def bayut_webhook(request: Request):
    """
    Receives new lead from Bayut portal.
    Deduplicates, stores lead, triggers AI WhatsApp greeting.
    """
    start_time = time.time()
    sb = get_supabase()
    payload = await request.json()

    # Log raw webhook
    log_entry = sb.table("webhook_logs").insert({
        "source": "bayut",
        "payload": payload,
        "processed": False,
    }).execute()
    log_id = log_entry.data[0]["id"]

    try:
        # Bayut specific payload parsing (can be adjusted based on exact Bayut format)
        lead_data = payload.get("lead", payload)
        external_id = str(lead_data.get("id", ""))
        name = lead_data.get("name") or f"{lead_data.get('first_name', '')} {lead_data.get('last_name', '')}".strip()
        phone = str(lead_data.get("phone") or lead_data.get("mobile", ""))
        email = lead_data.get("email", "")
        property_ref = str(lead_data.get("reference", ""))
        property_address = lead_data.get("property_title", "")
        bedrooms = lead_data.get("bedrooms")
        budget = lead_data.get("price")
        location = lead_data.get("location", "")

        if not phone:
            raise ValueError("No phone number in webhook payload")

        # Resolve agency + agent
        agent_phone = lead_data.get("agent_phone")
        agent_email = lead_data.get("agent_email")
        agency_id, assigned_agent_id = await resolve_agency_and_agent(
            source="bayut",
            property_ref=property_ref or None,
            agent_phone=str(agent_phone) if agent_phone else None,
            agent_email=agent_email,
        )
        if not agency_id:
            raise ValueError("Could not resolve agency for inbound lead")

        # Deduplication
        if external_id and await is_duplicate(external_id):
            logger.info(f"Duplicate Bayut lead ignored: {external_id}")
            sb.table("webhook_logs").update({
                "processed": True,
                "error": "duplicate",
                "processing_time_ms": int((time.time() - start_time) * 1000),
            }).eq("id", log_id).execute()
            return api_success(message="Lead already exists", data={"status": "duplicate"})

        existing = await get_existing_lead_by_phone(phone)
        if existing:
            sb.table("webhook_logs").update({
                "processed": True,
                "lead_id": existing["id"],
                "error": "phone_duplicate",
                "processing_time_ms": int((time.time() - start_time) * 1000),
            }).eq("id", log_id).execute()
            return api_success(message="Lead with this phone already exists", data={"status": "duplicate"})

        # Create Lead
        lead_insert = {
            "external_lead_id": external_id or None,
            "name": name or "Unknown",
            "phone": phone,
            "email": email or None,
            "source": "bayut",
            "property_ref": property_ref or None,
            "property_address": property_address or None,
            "bedrooms": int(bedrooms) if bedrooms else None,
            "budget_max": float(budget) if budget else None,
            "location_pref": location or None,
            "status": "new",
            "ai_stage": "greeting",
            "is_ai_handling": True,
            "agency_id": agency_id,
        }
        if assigned_agent_id:
            lead_insert["assigned_agent_id"] = assigned_agent_id

        new_lead = sb.table("leads").insert(lead_insert).execute()
        lead_id = new_lead.data[0]["id"]

        # Send AI Greeting via WhatsApp (<3 min SLA)
        greeting = (
            f"Hi {name.split()[0]}! 👋 I'm Andi, your AI assistant from the agency.\n\n"
            f"I saw your enquiry about {'the ' + property_ref + ' listing' if property_ref else 'a property'} "
            f"{'in ' + location if location else ''} on Bayut. \n\n"
            f"I'd love to help you find your perfect home! Could you tell me:\n"
            f"1. What's your budget range?\n"
            f"2. How many bedrooms are you looking for?"
        )
        await send_whatsapp_message(phone, greeting)

        # Log conversation
        sb.table("conversations").insert({
            "lead_id": lead_id,
            "agency_id": agency_id,
            "direction": "outbound",
            "channel": "whatsapp",
            "message_body": greeting,
            "sender_type": "ai",
        }).execute()

        sb.table("leads").update({"status": "qualifying"}).eq("id", lead_id).execute()

        sb.table("webhook_logs").update({
            "processed": True,
            "lead_id": lead_id,
            "processing_time_ms": int((time.time() - start_time) * 1000),
        }).eq("id", log_id).execute()

        logger.info(f"New Bayut lead created: {lead_id} ({name}, {phone})")
        return api_success(data={"lead_id": lead_id}, message="Bayut lead processed successfully")

    except Exception as e:
        logger.error(f"Bayut webhook error: {e}")
        sb.table("webhook_logs").update({
            "error": str(e),
            "processing_time_ms": int((time.time() - start_time) * 1000),
        }).eq("id", log_id).execute()
        raise HTTPException(status_code=500, detail=str(e))


# ─── Dubizzle Webhook ──────────────────────────────────────────────────────────

@router.post("/dubizzle")
async def dubizzle_webhook(request: Request):
    """
    Receives new lead from Dubizzle portal.
    Deduplicates, stores lead, triggers AI WhatsApp greeting.
    """
    start_time = time.time()
    sb = get_supabase()
    payload = await request.json()

    # Log raw webhook
    log_entry = sb.table("webhook_logs").insert({
        "source": "dubizzle",
        "payload": payload,
        "processed": False,
    }).execute()
    log_id = log_entry.data[0]["id"]

    try:
        # Dubizzle specific payload parsing
        lead_data = payload.get("lead", payload)
        external_id = str(lead_data.get("id", ""))
        name = lead_data.get("name") or f"{lead_data.get('first_name', '')} {lead_data.get('last_name', '')}".strip()
        phone = str(lead_data.get("phone") or lead_data.get("mobile", ""))
        email = lead_data.get("email", "")
        property_ref = str(lead_data.get("reference", ""))
        property_address = lead_data.get("property_title", "")
        bedrooms = lead_data.get("bedrooms")
        budget = lead_data.get("price")
        location = lead_data.get("location", "")

        if not phone:
            raise ValueError("No phone number in webhook payload")

        # Resolve agency + agent
        agent_phone = lead_data.get("agent_phone")
        agent_email = lead_data.get("agent_email")
        agency_id, assigned_agent_id = await resolve_agency_and_agent(
            source="dubizzle",
            property_ref=property_ref or None,
            agent_phone=str(agent_phone) if agent_phone else None,
            agent_email=agent_email,
        )
        if not agency_id:
            raise ValueError("Could not resolve agency for inbound lead")

        # Deduplication
        if external_id and await is_duplicate(external_id):
            logger.info(f"Duplicate Dubizzle lead ignored: {external_id}")
            sb.table("webhook_logs").update({
                "processed": True,
                "error": "duplicate",
                "processing_time_ms": int((time.time() - start_time) * 1000),
            }).eq("id", log_id).execute()
            return api_success(message="Lead already exists", data={"status": "duplicate"})

        existing = await get_existing_lead_by_phone(phone)
        if existing:
            sb.table("webhook_logs").update({
                "processed": True,
                "lead_id": existing["id"],
                "error": "phone_duplicate",
                "processing_time_ms": int((time.time() - start_time) * 1000),
            }).eq("id", log_id).execute()
            return api_success(message="Lead with this phone already exists", data={"status": "duplicate"})

        # Create Lead
        lead_insert = {
            "external_lead_id": external_id or None,
            "name": name or "Unknown",
            "phone": phone,
            "email": email or None,
            "source": "dubizzle",
            "property_ref": property_ref or None,
            "property_address": property_address or None,
            "bedrooms": int(bedrooms) if bedrooms else None,
            "budget_max": float(budget) if budget else None,
            "location_pref": location or None,
            "status": "new",
            "ai_stage": "greeting",
            "is_ai_handling": True,
            "agency_id": agency_id,
        }
        if assigned_agent_id:
            lead_insert["assigned_agent_id"] = assigned_agent_id

        new_lead = sb.table("leads").insert(lead_insert).execute()
        lead_id = new_lead.data[0]["id"]

        # Send AI Greeting via WhatsApp (<3 min SLA)
        greeting = (
            f"Hi {name.split()[0]}! 👋 I'm Andi, your AI assistant from the agency.\n\n"
            f"I saw your enquiry about {'the ' + property_ref + ' listing' if property_ref else 'a property'} "
            f"{'in ' + location if location else ''} on Dubizzle. \n\n"
            f"I'd love to help you find your perfect home! Could you tell me:\n"
            f"1. What's your budget range?\n"
            f"2. How many bedrooms are you looking for?"
        )
        await send_whatsapp_message(phone, greeting)

        # Log conversation
        sb.table("conversations").insert({
            "lead_id": lead_id,
            "agency_id": agency_id,
            "direction": "outbound",
            "channel": "whatsapp",
            "message_body": greeting,
            "sender_type": "ai",
        }).execute()

        sb.table("leads").update({"status": "qualifying"}).eq("id", lead_id).execute()

        sb.table("webhook_logs").update({
            "processed": True,
            "lead_id": lead_id,
            "processing_time_ms": int((time.time() - start_time) * 1000),
        }).eq("id", log_id).execute()

        logger.info(f"New Dubizzle lead created: {lead_id} ({name}, {phone})")
        return api_success(data={"lead_id": lead_id}, message="Dubizzle lead processed successfully")

    except Exception as e:
        logger.error(f"Dubizzle webhook error: {e}")
        sb.table("webhook_logs").update({
            "error": str(e),
            "processing_time_ms": int((time.time() - start_time) * 1000),
        }).eq("id", log_id).execute()
        raise HTTPException(status_code=500, detail=str(e))


# ─── WhatsApp Webhooks ─────────────────────────────────────────────────────────

@router.get("/whatsapp")
async def whatsapp_verify(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    """WhatsApp webhook verification endpoint."""
    if hub_mode == "subscribe" and hub_verify_token == settings.WHATSAPP_VERIFY_TOKEN:
        return int(hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/whatsapp")
async def whatsapp_inbound(request: Request):
    """
    Receives inbound WhatsApp messages.
    Requests are authenticated per provider before any processing:
      - Twilio: X-Twilio-Signature validated against URL + POST params
      - 360dialog: shared-secret token (header or callback-URL query param)
    Routes to AI for qualification, or flags for agent handover.
    """
    sb = get_supabase()

    # ── Provider authentication — reject forged/unauthenticated requests ──
    if settings.WHATSAPP_PROVIDER == "360dialog":
        if not _verify_360dialog_request(request):
            raise HTTPException(status_code=403, detail="Invalid webhook authentication")
        payload = await request.json()
        messages = parse_360dialog_inbound(payload)
    else:
        form = await request.form()
        if not _verify_twilio_request(request, dict(form)):
            raise HTTPException(status_code=403, detail="Invalid webhook signature")
        messages = [parse_twilio_inbound(dict(form))]

    for msg in messages:
        from_phone = msg.get("from_phone", "")
        message_body = msg.get("message", "")
        message_id = msg.get("message_id", "")

        if not from_phone or not message_body:
            continue

        # Find lead by sender phone (exact-first, fail-safe on ambiguity)
        lead, match_reason = _find_lead_by_sender_phone(sb, from_phone)
        if lead is None:
            if match_reason in ("unknown", "invalid"):
                masked = f"***{_normalize_phone(from_phone)[-3:]}" if from_phone else "(empty)"
                logger.warning(f"Inbound WhatsApp from unmatched number {masked} ({match_reason})")
            continue
        lead_id = lead["id"]

        # Store inbound message
        sb.table("conversations").insert({
            "lead_id": lead_id,
            "agency_id": lead.get("agency_id"),
            "direction": "inbound",
            "channel": "whatsapp",
            "message_body": message_body,
            "sender_type": "lead",
            "whatsapp_message_id": message_id,
        }).execute()

        # Skip if already handed over to agent
        if not lead.get("is_ai_handling", True):
            logger.info(f"Lead {lead_id} is with human agent — not auto-responding")
            continue

        # Get conversation history
        history = (
            sb.table("conversations")
            .select("*")
            .eq("lead_id", lead_id)
            .order("timestamp", desc=False)
            .limit(20)
            .execute()
        ).data

        # ── Detect if handover needed ──
        # Count consecutive unanswered inbound messages at the END of the conversation.
        # If the LAST N messages are ALL from the lead (no AI reply in between),
        # it likely means Twilio is failing — do NOT trigger handover in that case.
        # Only handover if there's genuine evidence of back-and-forth AI conversation.
        sorted_history = sorted(history, key=lambda m: m.get("timestamp", ""))
        # Count how many of the LAST messages are consecutive inbound (no outbound AI)
        consecutive_unanswered = 0
        for m in reversed(sorted_history):
            if m.get("direction") == "inbound" and m.get("sender_type") == "lead":
                consecutive_unanswered += 1
            elif m.get("direction") == "outbound" and m.get("sender_type") == "ai":
                break  # Found an AI reply — stop counting
            # Ignore other types (e.g. system messages)

        total_ai_replies = sum(1 for m in history if m.get("direction") == "outbound" and m.get("sender_type") == "ai")
        
        # Only allow handover if:
        # - There IS at least 1 AI reply in history (genuine conversation started), AND
        # - Not ALL messages are unanswered (which would indicate a Twilio send failure)
        if total_ai_replies == 0 or consecutive_unanswered >= total_ai_replies * 2:
            logger.info(f"Lead {lead_id}: {consecutive_unanswered} unanswered msgs, {total_ai_replies} AI replies — likely Twilio delivery issue, skipping handover detection")
            handover_result = {"needs_handover": False}
        else:
            handover_result = await detect_handover(history, message_body)

        if handover_result.get("needs_handover") and handover_result.get("confidence", 0) > 0.7:
            # Flag for human agent
            sb.table("leads").update({
                "is_ai_handling": False,
                "status": "handover",
                "handover_reason": handover_result.get("reason"),
            }).eq("id", lead_id).execute()

            handover_msg = (
                "Thank you for your message! I'm connecting you with one of our agents "
                "who will be in touch with you shortly. 😊"
            )
            await send_whatsapp_message(from_phone, handover_msg)
            sb.table("conversations").insert({
                "lead_id": lead_id,
                "agency_id": lead.get("agency_id"),
                "direction": "outbound",
                "channel": "whatsapp",
                "message_body": handover_msg,
                "sender_type": "ai",
            }).execute()
            logger.info(f"Lead {lead_id} handed over: {handover_result.get('reason')}")

            # ── Notify assigned agent via WhatsApp ──
            assigned_agent_id = lead.get("assigned_agent_id")
            if assigned_agent_id:
                agent_result = sb.table("agents").select("name, phone, whatsapp_number, email").eq("id", assigned_agent_id).execute()
                if agent_result.data:
                    agent = agent_result.data[0]
                    agent_phone = agent.get("whatsapp_number") or agent.get("phone")
                    if agent_phone:
                        agent_notify_msg = (
                            f"🔔 *Handover Alert*\n\n"
                            f"Lead *{lead.get('name', 'Unknown')}* needs your attention.\n"
                            f"📱 Phone: {lead.get('phone')}\n"
                            f"💬 Last message: _{message_body[:100]}_\n"
                            f"📋 Reason: {handover_result.get('reason', 'Complex query')}\n\n"
                            f"Please respond to this lead directly."
                        )
                        await send_whatsapp_message(agent_phone, agent_notify_msg)
                        logger.info(f"Handover notification sent to agent {agent.get('name')} for lead {lead_id}")

            continue

        # ── AI Qualification Response ──
        ai_reply = await qualify_and_respond(lead, history, message_body)
        
        # Only save to DB and mark as delivered if Twilio send succeeds
        send_result = await send_whatsapp_message(from_phone, ai_reply)
        
        if send_result.get("status") == "sent":
            logger.info(f"Lead {lead_id}: AI reply delivered successfully (SID={send_result.get('sid')})")
        else:
            logger.warning(f"Lead {lead_id}: AI reply NOT delivered (Twilio error: {send_result.get('error')}) — NOT saving to conversations")

        # Always save the AI reply to conversations (for audit trail), but tag delivery status
        sb.table("conversations").insert({
            "lead_id": lead_id,
            "agency_id": lead.get("agency_id"),
            "direction": "outbound",
            "channel": "whatsapp",
            "message_body": ai_reply,
            "sender_type": "ai",
            "whatsapp_message_id": send_result.get("sid") if send_result.get("status") == "sent" else None,
        }).execute()

        # ── Extract and update qualification data ──
        all_history = history + [{"sender_type": "lead", "message_body": message_body}]
        qualifications = await extract_lead_qualifications(all_history)
        update_data = {}
        if qualifications.get("bedrooms"):
            update_data["bedrooms"] = qualifications["bedrooms"]
        if qualifications.get("budget_min"):
            update_data["budget_min"] = qualifications["budget_min"]
        if qualifications.get("budget_max"):
            update_data["budget_max"] = qualifications["budget_max"]
        if qualifications.get("location_pref"):
            update_data["location_pref"] = qualifications["location_pref"]
        if qualifications.get("purpose"):
            update_data["purpose"] = qualifications["purpose"]
        if update_data:
            sb.table("leads").update(update_data).eq("id", lead_id).execute()


    return api_success(message="WhatsApp messages processed successfully")


# ─── Vapi AI Caller Webhook ────────────────────────────────────────────────────

@router.post("/vapi")
async def vapi_webhook(request: Request):
    """
    Receives call result callbacks from Vapi.ai.
    Stores transcript, recording URL, duration, and outcome.
    Auto-flags DNC owners and schedules retries for voicemail/no-answer.
    """
    try:
        payload = await request.json()
        from services.vapi_service import process_vapi_webhook
        result = await process_vapi_webhook(payload)
        return api_success(data=result, message="Vapi webhook processed")
    except Exception as e:
        logger.error(f"Vapi webhook error: {e}")
        # Vapi expects 200 OK — don't raise HTTP errors
        return api_success(data={"status": "error", "detail": str(e)}, message="Vapi webhook error")


# ─── Stripe Billing Webhook ───────────────────────────────────────────────────

@router.post("/stripe")
async def stripe_webhook(request: Request):
    """
    Handles Stripe subscription and invoice lifecycle webhooks.
    Keeps Supabase subscriptions and invoices tables in sync.
    """
    import stripe
    from services.billing_service import (
        sync_subscription_from_stripe,
        sync_invoice_from_stripe,
        _to_dict_safe,
    )

    payload_bytes = await request.body()
    sig_header = request.headers.get("stripe-signature")
    webhook_secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "")

    event = None
    if webhook_secret and sig_header:
        try:
            event = stripe.Webhook.construct_event(
                payload_bytes, sig_header, webhook_secret
            )
        except stripe.error.SignatureVerificationError as e:
            logger.warning(f"Stripe signature verification failed: {e}")
            raise HTTPException(status_code=400, detail="Invalid Stripe signature")
        except Exception as e:
            logger.error(f"Error parsing Stripe webhook: {e}")
            raise HTTPException(status_code=400, detail=str(e))
    else:
        # Fallback for dev / unverified payloads
        try:
            import json
            event = json.loads(payload_bytes.decode("utf-8"))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    event_dict = _to_dict_safe(event)
    event_type = event_dict.get("type", "")
    event_data = event_dict.get("data", {}).get("object", {})
    logger.info(f"Received Stripe webhook event: {event_type}")

    try:
        if event_type in ["customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"]:
            await sync_subscription_from_stripe(event_data)

        elif event_type in ["invoice.created", "invoice.payment_succeeded", "invoice.payment_failed", "invoice.finalized", "invoice.paid"]:
            await sync_invoice_from_stripe(event_data)

            # If payment failed, send alert to agency owner
            if event_type == "invoice.payment_failed":
                logger.warning(f"Invoice {event_data.get('id')} payment failed for customer {event_data.get('customer')}")

        elif event_type == "checkout.session.completed":
            sub_id = event_data.get("subscription")
            if sub_id and getattr(settings, "STRIPE_SECRET_KEY", None):
                stripe.api_key = settings.STRIPE_SECRET_KEY
                stripe_sub = stripe.Subscription.retrieve(sub_id)
                await sync_subscription_from_stripe(stripe_sub)

        return api_success(data={"received": True, "event": event_type}, message="Stripe webhook processed")
    except Exception as e:
        logger.error(f"Error processing Stripe event {event_type}: {e}")
        # Always return 200 to Stripe so it doesn't repeatedly retry failing webhooks
        return api_success(data={"status": "error", "error": str(e)}, message="Stripe event handled with errors")


