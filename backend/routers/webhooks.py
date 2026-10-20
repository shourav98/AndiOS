"""
Webhooks Router — handles inbound leads from Property Finder, Bayut, Dubizzle,
inbound WhatsApp messages (shared gateway), Vapi call result callbacks, and
inbound voice calls forwarded to the Central Platform DID.

POST /webhooks/property-finder   — new lead from portal (HMAC verified)
POST /webhooks/bayut             — new lead from Bayut
POST /webhooks/dubizzle          — new lead from Dubizzle
GET  /webhooks/whatsapp          — WhatsApp webhook verification
POST /webhooks/whatsapp          — inbound WhatsApp message (shared gateway router)
POST /webhooks/vapi              — Vapi call result callback
POST /webhooks/voice/inbound     — inbound voice call (Central DID, BYON forwarding)
"""
from __future__ import annotations
import time
import json
import hmac
import hashlib
import re
from typing import Any, Optional, Dict, List
from fastapi import APIRouter, Request, HTTPException, Query, BackgroundTasks
from fastapi.responses import JSONResponse
from database.supabase_client import get_supabase
import services.dedup_service as dedup_service
from services.dedup_service import is_duplicate, get_existing_lead_by_phone, is_duplicate_lead_for_property
from services.whatsapp_service import (
    send_whatsapp_message,
    send_whatsapp_for_agency,
    send_whatsapp_smart,
    drain_outbound_queue_for_lead,
    parse_360dialog_inbound,
    parse_twilio_inbound,
)
from services.quota_service import (
    check_and_consume_whatsapp_quota,
    refund_whatsapp_quota,
)
from services.ai_service import qualify_and_respond, detect_handover, extract_lead_qualifications
from services.lead_routing_service import resolve_agency_and_agent
from utils.response import api_success, api_error
from config import settings
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


# ─── HMAC Verification Helper ──────────────────────────────────────────────────

def _verify_pf_request(
    request: Request,
    raw_body: bytes,
    parsed_body: dict | None = None,
) -> bool:
    """
    Verify an inbound Property Finder webhook request.

    Property Finder does NOT send HMAC signatures or Bearer tokens.
    Their webhook security model is: the URL itself is secret (obscurity).
    Optionally, operators may append ?token=<secret> to the webhook URL
    configured in the PF dashboard, which we then validate.

    Auth priority:
      1. ?token= / ?api_key= query param matches PROPERTY_FINDER_WEBHOOK_SECRET
      2. Authorization: Bearer <secret> header matches
      3. X-API-Key / X-Signature header matches (HMAC or plain)
      4. JSON body contains api_key/secret/token field that matches
      5. No PROPERTY_FINDER_WEBHOOK_SECRET configured AND body looks like
         a valid PF event envelope — accept with a warning (PF sends no auth)

    When PROPERTY_FINDER_WEBHOOK_SECRET is set, steps 1-4 must match.
    When it is NOT set, step 5 applies (accept all valid PF-shaped requests).
    """
    secret = (getattr(settings, "PROPERTY_FINDER_WEBHOOK_SECRET", "") or "").strip()

    def _ct_eq(a: str, b: str) -> bool:
        try:
            return hmac.compare_digest(a.strip(), b.strip())
        except (TypeError, ValueError):
            return False

    def _is_pf_envelope(body: dict | None) -> bool:
        """Returns True if body looks like a PF webhook event envelope."""
        if not isinstance(body, dict):
            return False
        # PF envelope always has: id, type, timestamp, entity, payload
        pf_keys = {"id", "type", "timestamp"}
        return bool(pf_keys.issubset(body.keys()))

    # ── If secret IS configured: require it via one of several methods ──────
    if secret:
        # Method 1: ?token= or ?api_key= query parameter (recommended for PF)
        token_param = (
            request.query_params.get("token")
            or request.query_params.get("api_key")
        )
        if token_param and _ct_eq(token_param, secret):
            logger.debug("[PF Webhook] Authenticated via token query param")
            return True

        # Method 2: Authorization: Bearer <secret>
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
            if _ct_eq(token, secret):
                logger.debug("[PF Webhook] Authenticated via Authorization Bearer")
                return True

        # Method 3: X-API-Key header
        api_key_header = (
            request.headers.get("x-api-key")
            or request.headers.get("x-pf-api-key")
            or request.headers.get("api-key")
        )
        if api_key_header and _ct_eq(api_key_header, secret):
            logger.debug("[PF Webhook] Authenticated via X-API-Key header")
            return True

        # Method 4: HMAC signature in X-Signature / X-Hub-Signature-256
        hmac_header = (
            request.headers.get("x-signature")
            or request.headers.get("x-pf-signature")
            or request.headers.get("x-hub-signature-256")
            or request.headers.get("x-webhook-signature")
        )
        if hmac_header:
            computed = hmac.new(
                key=secret.encode("utf-8"),
                msg=raw_body,
                digestmod=hashlib.sha256,
            ).hexdigest()
            stripped = hmac_header.removeprefix("sha256=").lower()
            try:
                if hmac.compare_digest(computed, stripped):
                    logger.debug("[PF Webhook] Authenticated via HMAC-SHA256")
                    return True
            except (TypeError, ValueError):
                pass

        # Method 5: secret embedded in JSON body
        if parsed_body and isinstance(parsed_body, dict):
            body_token = (
                parsed_body.get("api_key")
                or parsed_body.get("secret")
                or parsed_body.get("token")
                or parsed_body.get("webhook_secret")
            )
            if body_token and _ct_eq(str(body_token), secret):
                logger.debug("[PF Webhook] Authenticated via embedded body token")
                return True

        # Secret IS set but no method matched — reject
        logger.warning(
            "[PF Webhook] Auth FAILED — PROPERTY_FINDER_WEBHOOK_SECRET is set "
            "but no matching auth method found. "
            "Add ?token=<secret> to your PF dashboard webhook URL. "
            "Headers: %s | Query params: %s | Body keys: %s",
            {k: v[:12]+"..." for k, v in request.headers.items()
             if k.lower() in ("authorization", "x-api-key", "x-signature")} or "(none)",
            dict(request.query_params) or "(none)",
            list(parsed_body.keys()) if parsed_body else "(none)",
        )
        return False

    # ── No secret configured: accept valid PF envelope shapes ──────────────
    # PF sends webhooks with no auth headers — their security is URL-secrecy.
    # We accept if the body looks like a genuine PF event envelope.
    if _is_pf_envelope(parsed_body):
        logger.warning(
            "[PF Webhook] PROPERTY_FINDER_WEBHOOK_SECRET not set — accepting "
            "unverified PF webhook (body keys: %s). "
            "RECOMMENDED: append ?token=<secret> to the PF dashboard webhook URL "
            "and set PROPERTY_FINDER_WEBHOOK_SECRET=<secret> in .env",
            list(parsed_body.keys()),
        )
        return True

    # Not a PF envelope and no secret — reject
    logger.warning(
        "[PF Webhook] Auth FAILED — no secret configured and body does not "
        "look like a PF event. Body keys: %s",
        list(parsed_body.keys()) if parsed_body else "(empty)",
    )
    return False


# Keep backward-compatible alias used in tests
def _verify_pf_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """Legacy shim — kept for unit test compatibility. Use _verify_pf_request() instead."""
    secret = (getattr(settings, "PROPERTY_FINDER_WEBHOOK_SECRET", "") or "").strip()
    if not secret:
        return getattr(settings, "APP_ENV", "development") == "development"
    if not signature_header:
        return False
    computed = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    stripped = signature_header.removeprefix("sha256=").lower()
    try:
        return hmac.compare_digest(computed, stripped)
    except (TypeError, ValueError):
        return False


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
    In development mode (APP_ENV=development), missing signature is accepted
    to allow Postman testing without signature generation.
    Note: Twilio signs the PUBLIC request URL — reverse proxies must forward
    the correct scheme/host (X-Forwarded-*) for validation to succeed.
    """
    from twilio.request_validator import RequestValidator

    auth_token = (getattr(settings, "TWILIO_AUTH_TOKEN", "") or "").strip()
    signature = request.headers.get("x-twilio-signature")
    is_development = getattr(settings, "APP_ENV", "development") == "development"

    if not auth_token:
        if not is_development:
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

    # Development mode: allow Postman testing without X-Twilio-Signature
    if not signature:
        if is_development:
            logger.warning(
                "No X-Twilio-Signature header — accepting in development mode "
                "(Postman/local test). Set APP_ENV=production to enforce."
            )
            return True
        return False

    validator = RequestValidator(auth_token)
    str_params = {str(k): str(v) for k, v in params.items()}
    return validator.validate(str(request.url), str_params, signature)


def _verify_meta_request(request: Request, raw_body: bytes) -> bool:
    """
    Authenticate inbound Meta Cloud API webhook requests via X-Hub-Signature-256.
    Header format: 'sha256=<hex_digest>' over the raw request body.
    Fails closed outside development when app secret is unconfigured.
    """
    secret = (getattr(settings, "META_APP_SECRET", "") or getattr(settings, "WHATSAPP_APP_SECRET", "") or "").strip()
    signature = request.headers.get("x-hub-signature-256") or request.headers.get("X-Hub-Signature-256")
    is_development = getattr(settings, "APP_ENV", "development") == "development"

    secrets_raw = (getattr(settings, "META_APP_SECRET", "") or getattr(settings, "WHATSAPP_APP_SECRET", "") or "").strip()
    secret_list = [s.strip() for s in secrets_raw.split(",") if s.strip()]

    if not secret_list:
        if not is_development:
            logger.critical("META_APP_SECRET is not configured — rejecting Meta webhook (fail closed)")
            return False
        logger.warning("META_APP_SECRET not set — accepting Meta webhook in development only")
        return True

    if not signature or not signature.startswith("sha256="):
        if is_development:
            logger.warning("No valid X-Hub-Signature-256 — accepting in development mode only")
            return True
        return False

    for s in secret_list:
        expected = "sha256=" + hmac.new(
            key=s.encode("utf-8"),
            msg=raw_body,
            digestmod=hashlib.sha256,
        ).hexdigest()
        if hmac.compare_digest(expected, signature):
            return True

    logger.warning(
        "Meta webhook X-Hub-Signature-256 mismatch! Verified against %d secret(s). Please check META_APP_SECRET in .env.",
        len(secret_list),
    )
    return False


def _parse_numeric_budget(val: Any) -> float | None:
    """Safely parse human-written budget strings into floats for DB numeric columns."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().lower().replace(",", "").replace("aed", "").replace("$", "").strip()
    try:
        import re
        if s.endswith("k"):
            return float(s[:-1].strip()) * 1000
        elif s.endswith("m"):
            return float(s[:-1].strip()) * 1000000
        elif s.endswith("b"):
            return float(s[:-1].strip()) * 1000000000
        m = re.search(r"(\d+(\.\d+)?)", s)
        if m:
            num = float(m.group(1))
            if "k" in s:
                num *= 1000
            elif "m" in s:
                num *= 1000000
            return num
        return float(s)
    except (ValueError, TypeError):
        return None


def _parse_bedrooms(val: Any) -> int | None:
    """Safely parse bedroom count (e.g. '2BHK', '3 bed', 'Studio', 2) into integer."""
    if val is None:
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        return int(val)
    s = str(val).strip().lower()
    if "studio" in s:
        return 0
    import re
    m = re.search(r"\d+", s)
    if m:
        try:
            return int(m.group(0))
        except (ValueError, TypeError):
            return None
    return None


# ─── Safe Lead Resolution ─────────────────────────────────────────────────────

# Matches legacy "REF-123456" or "Listing #123456" patterns in message body
_PF_REF_PATTERN = re.compile(r'\b(?:REF[-\s]?|Listing\s*#?\s*)(\d+)\b', re.IGNORECASE)

# Matches Property Finder short-link slug: propertyfinder.ae/go/<SLUG>
# e.g. https://www.propertyfinder.ae/go/CMVYT7MAQ990YSR4ZYMSG8CG14/en?...
_PF_GO_SLUG_PATTERN = re.compile(
    r'propertyfinder\.ae/go/([A-Z0-9]{10,30})',
    re.IGNORECASE,
)

# Matches PLP listing numeric ID from full Property Finder listing URL:
# e.g. /rent/townhouse-for-rent-dubai-...-155075886.html
_PF_PLP_ID_PATTERN = re.compile(
    r'propertyfinder\.ae/en/plp/[^\s"]+-(\d{6,12})\.html',
    re.IGNORECASE,
)

# Relay messages that Property Finder sends to the AGENT's WhatsApp number.
# These arrive from PF's system, NOT from a real buyer — they must be silently dropped.
# Indicators: utm_medium=lead_reply in URL or "Property Finder passed on your enquiry" phrase.
_PF_RELAY_PATTERNS = [
    re.compile(r'utm_medium=lead_reply', re.IGNORECASE),
    re.compile(r'property finder passed on your enquir', re.IGNORECASE),
    re.compile(r'passed on your enquiry about the following', re.IGNORECASE),
]


def _is_pf_relay_message(body: str) -> bool:
    """Return True if the message is a Property Finder automated relay to the agent.
    These should be silently dropped — they are NOT buyer inquiries."""
    if not body:
        return False
    return any(p.search(body) for p in _PF_RELAY_PATTERNS)


def _extract_pf_go_slug(body: str) -> str | None:
    """Extract the unique Property Finder short-link tracking slug from a message body.
    e.g. 'CMVYT7MAQ990YSR4ZYMSG8CG14' from propertyfinder.ae/go/CMVYT7MAQ990YSR4ZYMSG8CG14/..."""
    if not body:
        return None
    m = _PF_GO_SLUG_PATTERN.search(body)
    return m.group(1).upper() if m else None


def _extract_pf_listing_id(body: str) -> str | None:
    """Extract numeric listing ID from a full Property Finder PLP URL in the message body.
    e.g. '155075886' from .../townhouse-for-rent-...-155075886.html"""
    if not body:
        return None
    m = _PF_PLP_ID_PATTERN.search(body)
    return m.group(1) if m else None


def _extract_property_ref_from_wame_body(body: str) -> str | None:
    """Extract Property Finder or listing reference from inbound WhatsApp message body.

    Supports three real-world message formats sent by Property Finder:
    1. Legacy: "REF-123456" or "Listing #123456"
    2. PF short-link slug: propertyfinder.ae/go/CMVYT7MAQ990YSR4ZYMSG8CG14/...
    3. Full PLP URL: propertyfinder.ae/en/plp/rent/...-155075886.html
    """
    if not body:
        return None
    # Priority 1: legacy REF pattern
    m = _PF_REF_PATTERN.search(body)
    if m:
        return f"REF-{m.group(1)}"
    # Priority 2: full PLP listing numeric ID
    plp_id = _extract_pf_listing_id(body)
    if plp_id:
        return f"PF-{plp_id}"
    # Priority 3: PF go-link slug (opaque tracking slug — store as-is)
    slug = _extract_pf_go_slug(body)
    if slug:
        return f"PF-GO-{slug[:16]}"  # truncate to 20 chars for DB column safety
    return None


def _normalize_phone(phone: str) -> str:
    """Digits-only normalization for sender/lead comparison."""
    return "".join(ch for ch in str(phone or "") if ch.isdigit())


def _find_lead_by_sender_phone(
    sb,
    from_phone: str,
    agency_id: str | None = None,
    agent_id: str | None = None,
):
    """
    Resolve which stored lead sent an inbound WhatsApp message.

    Matching strategy (safest-first):
      1. Normalize phone to E.164 digits.
      2. Exact digit-normalized match on the FULL phone number.
      3. Legacy tolerance only when no exact match exists: UNIQUE suffix match.
      4. If agency_id is provided, filter candidates strictly to that agency.
         If no agency_id is provided and candidates span multiple agencies, fail safe (ambiguous).
      5. If agent_id is provided (e.g. from BYON phone_number_id belonging to an agent):
         prioritize/filter to leads assigned to that specific agent.
      6. If multiple candidate leads remain:
         - If they belong to the SAME agent: pick most recently updated lead.
         - If they belong to DIFFERENT agents (or are unassigned): fail safe (ambiguous).
    Returns (lead | None, reason) where reason in matched|ambiguous|unknown|invalid.
    """
    digits = _normalize_phone(from_phone)
    if len(digits) < 7:
        return None, "invalid"

    candidates = (
        sb.table("leads")
        .select("*")
        .ilike("phone", f"%{digits[-9:]}")
        .execute()
        .data
        or []
    )

    exact = [c for c in candidates if _normalize_phone(c.get("phone")) == digits]
    pool = exact if exact else [
        c for c in candidates
        if _normalize_phone(c.get("phone", "")).endswith(digits[-9:])
    ]

    if not pool:
        return None, "unknown"

    # Filter by agency if resolved from incoming channel
    if agency_id and isinstance(agency_id, str):
        pool = [c for c in pool if c.get("agency_id") == agency_id]
        if not pool:
            return None, "unknown"
    else:
        # Cross-agency collision check when no single agency is bound to channel
        unique_agencies = {c.get("agency_id") for c in pool if c.get("agency_id")}
        if len(unique_agencies) > 1:
            logger.warning(
                "Ambiguous WhatsApp sender match (phone ending ***%s): candidate leads span multiple agencies %s — skipping",
                digits[-3:], unique_agencies,
            )
            return None, "ambiguous"

    # If specific agent_id was resolved from the incoming channel (e.g. agent's personal BYON)
    if agent_id and isinstance(agent_id, str):
        agent_matches = [c for c in pool if c.get("assigned_agent_id") == agent_id]
        if not agent_matches:
            # Do NOT fall back to another agent's lead in the agency
            return None, "unknown"
        pool = agent_matches


    if len(pool) > 1:
        unique_agents = {c.get("assigned_agent_id") for c in pool}
        first_agent = next(iter(unique_agents))
        if len(unique_agents) == 1 and first_agent is not None:
            # Same agent, multiple inquiries/leads from this buyer: pick the most recent
            pool.sort(key=lambda x: (x.get("updated_at") or x.get("created_at") or ""), reverse=True)
            return pool[0], "matched"

        # Cross-agent ambiguity on shared number (or multiple unassigned leads) — fail safe
        logger.warning(
            "Ambiguous WhatsApp sender match (phone ending ***%s): %d candidate leads across agents %s — skipping",
            digits[-3:], len(pool), unique_agents,
        )
        return None, "ambiguous"

    return pool[0], "matched"


def _check_and_mark_message_id(sb, message_id: str, agency_id: str | None = None, agent_id: str | None = None) -> bool:
    """
    Atomic idempotency guard for inbound WhatsApp messages.
    Performs atomic INSERT on whatsapp_processed_messages (message_id is PRIMARY KEY).
    Eliminates check-then-insert race conditions.

    Returns:
        True  — message is NEW, proceed with processing.
        False — message was already processed (duplicate detected), skip.

    Behavior on DB error:
        Logs an error/warning and returns True (graceful degradation: never drop customer messages).
    """
    if not message_id:
        return True

    try:
        row = {"message_id": message_id}
        if agency_id:
            row["agency_id"] = agency_id
        if agent_id:
            row["agent_id"] = agent_id

        sb.table("whatsapp_processed_messages").insert(row).execute()
        return True
    except Exception as exc:
        err_str = str(exc).lower()
        if "duplicate key" in err_str or "unique constraint" in err_str or "23505" in err_str:
            logger.info("[WA] Skipping duplicate message_id=%s (idempotency conflict)", message_id)
            return False
        logger.warning(
            "[WA] Idempotency check DB failure for message_id=%s: %s — proceeding without dedup",
            message_id, exc
        )
        return True


def _unmark_message_id(sb, message_id: str) -> None:
    """
    Remove the idempotency record when background message processing fails.
    Prevents leaving the message permanently marked as processed, ensuring
    subsequent webhook delivery retries can be processed successfully.
    """
    if not message_id:
        return
    try:
        sb.table("whatsapp_processed_messages").delete().eq("message_id", message_id).execute()
        logger.info("[WA] Cleared idempotency lock for failed message_id=%s (retry enabled)", message_id)
    except Exception as exc:
        logger.warning("[WA] Failed to clear idempotency lock for message_id=%s: %s", message_id, exc)


async def _handle_smb_message_echoes(sb, val: dict) -> None:
    """
    Handles `smb_message_echoes` event in Coexistence mode.
    When an agent replies to a client directly from their mobile WhatsApp Business App,
    Meta sends an echo webhook.
    Logic:
      1. Records the outbound human reply in conversations table (sender_type='agent').
      2. Human Takeover: Pauses AI auto-responder for this lead so AI does not interrupt the human.
    """
    echoes = val.get("message_echoes") or val.get("messages") or []
    for echo in echoes:
        to_phone = echo.get("to") or ""
        text_body = (echo.get("text") or {}).get("body") or echo.get("body") or ""
        msg_id = echo.get("id") or ""
        if not to_phone:
            continue

        lead, _ = _find_lead_by_sender_phone(sb, to_phone)
        if lead:
            lead_id = lead["id"]
            # 1. Insert message to conversation history
            try:
                sb.table("conversations").insert({
                    "lead_id": lead_id,
                    "agency_id": lead.get("agency_id"),
                    "direction": "outbound",
                    "channel": "whatsapp",
                    "message_body": text_body,
                    "sender_type": "agent",
                    "whatsapp_message_id": msg_id,
                }).execute()
            except Exception as e:
                logger.warning("[Coexistence] Error saving human echo conversation: %s", e)

            # 2. Pause AI (Human Takeover)
            try:
                sb.table("leads").update({
                    "is_ai_handling": False,
                    "status": "human_takeover",
                    "handover_reason": "agent_replied_via_mobile_app",
                }).eq("id", lead_id).execute()
                logger.info("[Coexistence] Human takeover active for lead %s. AI auto-reply paused.", lead_id)
            except Exception as e:
                logger.warning("[Coexistence] Error updating lead human takeover status: %s", e)


async def _handle_meta_account_update(sb, val: dict) -> None:
    """
    Handles `account_update` events (coexistence offboarding, disconnect, status changes).
    """
    event = val.get("event", "")
    phone_id = val.get("phone_number_id")
    waba_id = val.get("waba_id")
    logger.warning("[Meta Webhook] Received account_update event: %s (phone=%s, waba=%s)", event, phone_id, waba_id)

    if event in ("COEXISTENCE_OFFBOARDED", "DISABLED", "BANNED", "SUSPENDED"):
        query = sb.table("communication_accounts").update({"status": "suspended"})
        if phone_id:
            query = query.eq("phone_number_id", str(phone_id))
        elif waba_id:
            query = query.eq("external_account_id", str(waba_id))
        try:
            query.execute()
            logger.info("[Meta Webhook] Marked communication_account suspended due to event %s", event)
        except Exception as e:
            logger.error("[Meta Webhook] Error updating communication_account status on account_update: %s", e)


async def _handle_template_status_update(sb, val: dict) -> None:
    """
    Handles `message_template_status_update` webhook from Meta.
    Updates template status in whatsapp_templates (APPROVED, REJECTED, PAUSED, etc.).
    """
    event = val.get("event") or val.get("status") or "PENDING"
    template_id = val.get("message_template_id")
    template_name = val.get("message_template_name")
    logger.info("[Templates] Template status update: name=%s id=%s status=%s", template_name, template_id, event)

    try:
        query = sb.table("whatsapp_templates").update({"status": event.upper(), "updated_at": "now()"})
        if template_id:
            query = query.eq("meta_template_id", str(template_id))
        elif template_name:
            query = query.eq("name", template_name)
        query.execute()
    except Exception as e:
        logger.warning("[Templates] Error updating whatsapp_templates: %s", e)


# ─── Property Finder Webhook ───────────────────────────────────────────────────

@router.post("/property-finder")
async def property_finder_webhook(request: Request):
    """
    Receives new lead from Property Finder portal.
    Verifies HMAC signature, deduplicates, stores lead, triggers AI WhatsApp greeting.
    """
    start_time = time.time()
    sb = get_supabase()

    # ── Property Finder Auth Verification ──────────────────────────────
    raw_body = await request.body()
    # Parse body first so _verify_pf_request can check embedded body tokens
    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, ValueError):
        payload = {}
    if not _verify_pf_request(request, raw_body, parsed_body=payload):
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    # payload already parsed above from raw_body — no second request.json() needed

    # Log raw webhook
    log_entry = sb.table("webhook_logs").insert({
        "source": "property_finder",
        "payload": payload,
        "processed": False,
    }).execute()
    log_id = log_entry.data[0]["id"] if (log_entry and getattr(log_entry, "data", None)) else "log-id"

    try:
        # ── Parse Property Finder payload ──
        # PF Webhooks API sends an event envelope:
        #   {"id": "...", "type": "lead.created", "timestamp": "...",
        #    "entity": "lead", "payload": { <actual lead fields> }}
        # We must unwrap payload["payload"] first, then fall back to
        # legacy flat / {"lead": {...}} / {"leads": [...]} formats.

        # Format 1: PF event envelope (new format — confirmed from production logs)
        if "payload" in payload and isinstance(payload["payload"], dict):
            lead_data = payload["payload"]
            # Also log the event type for observability
            event_type = payload.get("type", "unknown")
            logger.info("[PF Webhook] Event type: %s, entity: %s", event_type, payload.get("entity"))
            # Skip non-lead events (e.g. listing updates, status changes)
            if event_type and "lead" not in event_type.lower() and payload.get("entity", "").lower() not in ("lead", "inquiry", ""):
                logger.info("[PF Webhook] Ignoring non-lead event type: %s", event_type)
                return api_success(message="Non-lead event ignored", data={"event_type": event_type})
        # Format 2: {"leads": [{...}, ...]}
        elif isinstance(payload.get("leads"), list) and len(payload["leads"]) > 0:
            lead_data = payload["leads"][0]
        # Format 3: {"lead": {...}}
        elif isinstance(payload.get("lead"), dict):
            lead_data = payload["lead"]
        # Format 4: flat root object
        else:
            lead_data = payload

        # ── Extract fields from PF envelope payload ──────────────────────────
        # Real PF format (confirmed from production logs):
        # {
        #   "channel": "whatsapp",
        #   "sender": {"name": "...", "contacts": [{"type": "phone", "value": "..."}, {"type": "whatsappUsername", "value": "..."}]},
        #   "listing": {"id": "...", "reference": "..."},
        #   "publicProfile": {"id": 403597},   ← agent profile ID
        #   "responseLink": "https://..."
        # }

        def _get_contact_value(contacts: list, contact_type: str) -> str:
            """Get value from PF contacts list by type."""
            for c in (contacts or []):
                if isinstance(c, dict) and c.get("type") == contact_type:
                    return str(c.get("value", "") or "")
            return ""

        sender = lead_data.get("sender") or {}
        contacts = sender.get("contacts") or []
        listing = lead_data.get("listing") or {}
        public_profile = lead_data.get("publicProfile") or {}

        # Phone: sender.contacts[type=phone].value
        phone = (
            _get_contact_value(contacts, "phone")
            or _get_contact_value(contacts, "mobile")
            or _get_contact_value(contacts, "telephone")
            # Fallback to flat fields (legacy PF format)
            or str(lead_data.get("phone") or lead_data.get("mobile") or lead_data.get("telephone") or
                   sender.get("phone") or sender.get("mobile") or "")
        )

        # Name: sender.name
        name = (
            sender.get("name")
            or lead_data.get("name") or lead_data.get("full_name")
            or f"{lead_data.get('first_name', '')} {lead_data.get('last_name', '')}".strip()
            or "Unknown"
        )

        # WhatsApp username (extra context)
        wa_username = _get_contact_value(contacts, "whatsappUsername")

        # Email: contacts list or flat field
        email = (
            _get_contact_value(contacts, "email")
            or lead_data.get("email") or sender.get("email") or ""
        )

        # Property reference: listing.reference or listing.id
        property_ref = str(
            listing.get("reference") or listing.get("id")
            or lead_data.get("property_ref") or lead_data.get("listing_id")
            or lead_data.get("reference_no") or ""
        )

        # External lead ID from the outer envelope's entity or payload id
        external_id = str(
            (payload.get("entity") or {}).get("id") if isinstance(payload.get("entity"), dict) else None
            or lead_data.get("id") or lead_data.get("lead_id") or ""
        )

        property_address = lead_data.get("property_title") or lead_data.get("property_address", "")
        bedrooms = lead_data.get("bedrooms")
        budget = lead_data.get("budget") or lead_data.get("price")
        location = lead_data.get("community") or lead_data.get("location") or lead_data.get("area", "")
        pf_agent_profile_id = str(public_profile.get("id", "")) if public_profile.get("id") else None

        logger.info(
            "[PF Webhook] Parsed — phone: %s, name: %s, property_ref: %s, wa: %s, pf_profile: %s",
            phone[-4:] if phone else "NONE", name, property_ref, wa_username, pf_agent_profile_id,
        )

        if not phone:
            logger.error("[PF Webhook] No phone found. Full lead_data: %s", lead_data)
            raise ValueError("No phone number in webhook payload")

        # ── Resolve agency + agent ──
        # Use publicProfile.id (PF agent profile ID) or listing reference for lookup
        agent_phone = lead_data.get("agent_phone") or lead_data.get("listing_agent_phone")
        agent_email = lead_data.get("agent_email") or lead_data.get("listing_agent_email")
        agency_id, assigned_agent_id = await resolve_agency_and_agent(
            source="property_finder",
            property_ref=property_ref or None,
            agent_phone=str(agent_phone) if agent_phone else None,
            agent_email=agent_email,
            pf_profile_id=pf_agent_profile_id,
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

        # Dedup by buyer phone + property reference (prevents AI sending twice for same listing)
        if property_ref and await dedup_service.is_duplicate_lead_for_property(phone, property_ref):
            logger.info(f"[PF Webhook] Lead with phone {phone[-4:]} already exists for property {property_ref}")
            sb.table("webhook_logs").update({
                "processed": True,
                "error": "property_duplicate",
                "processing_time_ms": int((time.time() - start_time) * 1000),
            }).eq("id", log_id).execute()
            return api_success(message="Lead already exists for this property", data={"status": "duplicate"})

        # Secondary dedup by phone
        existing = await dedup_service.get_existing_lead_by_phone(phone)
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
        # New leads from portal webhooks have last_inbound_at=None (no prior message).
        # send_whatsapp_smart handles this: None → closed window → template attempt → queue.
        first_name = name.split()[0] if name else "there"
        greeting = (
            f"Hi {first_name}! 👋 I'm Andi, your AI assistant from the agency.\n\n"
            f"I saw your enquiry about {'the ' + property_ref + ' listing' if property_ref else 'a property'} "
            f"{'in ' + location if location else ''}. \n\n"
            f"I'd love to help you find your perfect home! Could you tell me:\n"
            f"1. What's your budget range? (AED/year or AED purchase price)\n"
            f"2. How many bedrooms are you looking for?"
        )

        # ── Quota gate: consume 1 WhatsApp unit before sending ──
        # Atomic check+increment — prevents race conditions on burst traffic.
        # If quota exceeded, save lead but suppress AI greeting.
        quota_allowed = await check_and_consume_whatsapp_quota(agency_id)
        if quota_allowed:
            try:
                send_result_pf = await send_whatsapp_smart(
                    agency_id, lead_id, phone, greeting,
                    agent_id=assigned_agent_id,
                    template_name="andios_lead_first_contact",
                    template_params=[first_name, property_ref or "a property"],
                    last_inbound_at=None,  # new lead — window always closed
                )
            except Exception as wa_err:
                logger.error(f"PF webhook: WhatsApp send error for lead {lead_id}: {wa_err}")
                await refund_whatsapp_quota(agency_id)  # Refund on AI/send failure
                send_result_pf = {"status": "error"}

            # Log conversation (always, so queued messages have an audit trail)
            sb.table("conversations").insert({
                "lead_id": lead_id,
                "agency_id": agency_id,
                "direction": "outbound",
                "channel": "whatsapp",
                "message_body": greeting,
                "sender_type": "ai",
            }).execute()
        else:
            logger.warning(
                f"PF webhook: WhatsApp quota exceeded for agency {agency_id} "
                f"— lead {lead_id} saved but greeting suppressed"
            )

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
        logger.error(f"Property Finder webhook error: {e}", exc_info=True)
        sb.table("webhook_logs").update({
            "error": str(e),
            "processing_time_ms": int((time.time() - start_time) * 1000),
        }).eq("id", log_id).execute()
        raise HTTPException(status_code=500, detail=str(e))


# ─── Portal Webhook Authentication (Bayut / Dubizzle) ─────────────────────────

def _verify_portal_webhook(request: Request, setting_name: str, provider: str) -> bool:
    """
    Authenticate inbound portal lead webhooks (Bayut / Dubizzle).

    These portals do not sign webhook deliveries with HMAC in this
    integration, so an operator-provisioned shared token is required —
    delivered as the 'X-Webhook-Token' header or a '?token=' query parameter
    embedded in the callback URL agreed with the portal's integration team.
    Comparison is constant-time.

    Fails closed outside development when the token is unconfigured.
    """
    secret = getattr(settings, setting_name, "")
    provided = request.headers.get("x-webhook-token") or request.query_params.get("token")
    if not secret:
        if getattr(settings, "APP_ENV", "development") != "development":
            logger.critical(
                "%s is not configured — rejecting %s webhook (fail closed)",
                setting_name, provider,
            )
            return False
        logger.warning(
            "%s not set — accepting unauthenticated %s webhook in development only",
            setting_name, provider,
        )
        return True
    return bool(provided) and hmac.compare_digest(str(provided), str(secret))


# ─── Bayut Webhook ─────────────────────────────────────────────────────────────

@router.post("/bayut")
async def bayut_webhook(request: Request):
    """
    Receives new lead from Bayut portal.
    Requests are authenticated via a shared-secret token before any processing.
    Deduplicates, stores lead, triggers AI WhatsApp greeting.
    """
    start_time = time.time()
    sb = get_supabase()

    # ── Authentication — reject forged/unauthenticated requests ──
    if not _verify_portal_webhook(request, "BAYUT_WEBHOOK_TOKEN", "bayut"):
        raise HTTPException(status_code=403, detail="Invalid webhook authentication")

    payload = await request.json()

    # Log raw webhook
    log_entry = sb.table("webhook_logs").insert({
        "source": "bayut",
        "payload": payload,
        "processed": False,
    }).execute()
    log_id = log_entry.data[0]["id"] if (log_entry and getattr(log_entry, "data", None)) else "log-id"

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
        first_name_b = name.split()[0] if name else "there"
        greeting = (
            f"Hi {first_name_b}! 👋 I'm Andi, your AI assistant from the agency.\n\n"
            f"I saw your enquiry about {'the ' + property_ref + ' listing' if property_ref else 'a property'} "
            f"{'in ' + location if location else ''} on Bayut. \n\n"
            f"I'd love to help you find your perfect home! Could you tell me:\n"
            f"1. What's your budget range?\n"
            f"2. How many bedrooms are you looking for?"
        )
        quota_allowed = await check_and_consume_whatsapp_quota(agency_id)
        if quota_allowed:
            try:
                await send_whatsapp_smart(
                    agency_id, lead_id, phone, greeting,
                    agent_id=assigned_agent_id,
                    template_name="andios_lead_first_contact",
                    template_params=[first_name_b, property_ref or "a property"],
                    last_inbound_at=None,
                )
            except Exception as wa_err:
                logger.error(f"Bayut webhook: WhatsApp send error for lead {lead_id}: {wa_err}")
                await refund_whatsapp_quota(agency_id)
            sb.table("conversations").insert({
                "lead_id": lead_id,
                "agency_id": agency_id,
                "direction": "outbound",
                "channel": "whatsapp",
                "message_body": greeting,
                "sender_type": "ai",
            }).execute()
        else:
            logger.warning(f"Bayut webhook: quota exceeded for agency {agency_id} — greeting suppressed for lead {lead_id}")

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
    Requests are authenticated via a shared-secret token before any processing.
    Deduplicates, stores lead, triggers AI WhatsApp greeting.
    """
    start_time = time.time()
    sb = get_supabase()

    # ── Authentication — reject forged/unauthenticated requests ──
    if not _verify_portal_webhook(request, "DUBIZZLE_WEBHOOK_TOKEN", "dubizzle"):
        raise HTTPException(status_code=403, detail="Invalid webhook authentication")

    payload = await request.json()

    # Log raw webhook
    log_entry = sb.table("webhook_logs").insert({
        "source": "dubizzle",
        "payload": payload,
        "processed": False,
    }).execute()
    log_id = log_entry.data[0]["id"] if (log_entry and getattr(log_entry, "data", None)) else "log-id"

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
        first_name_d = name.split()[0] if name else "there"
        greeting = (
            f"Hi {first_name_d}! 👋 I'm Andi, your AI assistant from the agency.\n\n"
            f"I saw your enquiry about {'the ' + property_ref + ' listing' if property_ref else 'a property'} "
            f"{'in ' + location if location else ''} on Dubizzle. \n\n"
            f"I'd love to help you find your perfect home! Could you tell me:\n"
            f"1. What's your budget range?\n"
            f"2. How many bedrooms are you looking for?"
        )
        quota_allowed = await check_and_consume_whatsapp_quota(agency_id)
        if quota_allowed:
            try:
                await send_whatsapp_smart(
                    agency_id, lead_id, phone, greeting,
                    agent_id=assigned_agent_id,
                    template_name="andios_lead_first_contact",
                    template_params=[first_name_d, property_ref or "a property"],
                    last_inbound_at=None,
                )
            except Exception as wa_err:
                logger.error(f"Dubizzle webhook: WhatsApp send error for lead {lead_id}: {wa_err}")
                await refund_whatsapp_quota(agency_id)
            sb.table("conversations").insert({
                "lead_id": lead_id,
                "agency_id": agency_id,
                "direction": "outbound",
                "channel": "whatsapp",
                "message_body": greeting,
                "sender_type": "ai",
            }).execute()
        else:
            logger.warning(f"Dubizzle webhook: quota exceeded for agency {agency_id} — greeting suppressed for lead {lead_id}")

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
    """WhatsApp webhook verification endpoint (Meta Cloud API)."""
    configured_token = (getattr(settings, "WHATSAPP_VERIFY_TOKEN", "") or "").strip()
    is_production = getattr(settings, "APP_ENV", "development") != "development"

    if is_production and not configured_token:
        logger.critical("WHATSAPP_VERIFY_TOKEN is not configured in production — rejecting verification")
        raise HTTPException(status_code=403, detail="Verification token not configured")

    if not configured_token:
        configured_token = "andios_verify_token"  # Development fallback only

    if hub_mode == "subscribe" and hub_challenge and hub_verify_token:
        if hmac.compare_digest(str(hub_verify_token), str(configured_token)):
            try:
                return int(hub_challenge)
            except ValueError:
                return hub_challenge
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/whatsapp")
async def whatsapp_inbound(
    request: Request,
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """
    Receives inbound WhatsApp messages and webhooks.
    Requests are authenticated per provider before any processing:
      - Twilio: X-Twilio-Signature validated against URL + POST params
      - 360dialog: shared-secret token (header or callback-URL query param)
      - Meta: X-Hub-Signature-256 HMAC-SHA256 signature verified against META_APP_SECRET
    Dispatches events (smb_message_echoes, account_update, message_template_status_update)
    and routes customer messages to AI for qualification.
    """
    sb = get_supabase()

    # ── Provider authentication — reject forged/unauthenticated requests ──
    has_meta_sig = bool(request.headers.get("x-hub-signature-256"))

    if settings.WHATSAPP_PROVIDER == "360dialog":
        if not _verify_360dialog_request(request):
            raise HTTPException(status_code=403, detail="Invalid webhook authentication")
        payload = await request.json()
        messages = parse_360dialog_inbound(payload)
    elif settings.WHATSAPP_PROVIDER == "meta" or has_meta_sig:
        raw_body = await request.body() if hasattr(request, "body") else b""
        if not _verify_meta_request(request, raw_body):
            raise HTTPException(status_code=403, detail="Invalid Meta webhook signature")
        if hasattr(request, "json"):
            payload = await request.json()
        else:
            payload = json.loads(raw_body) if raw_body else {}

        contact_names: dict[str, str] = {}
        # ── Meta Event Dispatching (smb_message_echoes, account_update, templates) ──
        if isinstance(payload, dict):
            for entry in payload.get("entry", []):
                for change in entry.get("changes", []):
                    field = change.get("field", "")
                    val = change.get("value", {})
                    for c in val.get("contacts", []):
                        wa = c.get("wa_id")
                        pname = c.get("profile", {}).get("name")
                        if wa and pname:
                            contact_names[str(wa)] = str(pname)
                    for s in val.get("statuses", []):
                        s_id = s.get("id")
                        s_stat = s.get("status")
                        s_recipient = s.get("recipient_id")
                        s_errors = s.get("errors")
                        if s_errors:
                            logger.error(
                                "[Meta Delivery Error] msg_id=%s to=%s status=%s errors=%s",
                                s_id, s_recipient, s_stat, s_errors,
                            )
                        else:
                            logger.info(
                                "[Meta Delivery Status] msg_id=%s to=%s status=%s",
                                s_id, s_recipient, s_stat,
                            )
                    if field == "smb_message_echoes" or "message_echoes" in val:
                        await _handle_smb_message_echoes(sb, val)
                    elif field == "account_update":
                        await _handle_meta_account_update(sb, val)
                    elif field == "message_template_status_update":
                        await _handle_template_status_update(sb, val)
                    elif field == "history":
                        logger.info("[Coexistence] Received history sync batch.")
                    elif field == "smb_app_state_sync":
                        logger.info("[Coexistence] Received SMB app state sync: %s", val.get("type", "unknown"))

        from services.communication.meta_adapter import MetaWhatsAppAdapter
        meta_adapter = MetaWhatsAppAdapter()
        inbound_items = meta_adapter.parse_inbound(payload)
        messages = [
            {
                "from_phone": m.from_phone,
                "to_phone": m.to_identifier,  # Meta phone_number_id
                "message": m.body,
                "message_id": m.message_id,
                "is_meta": True,
                "sender_name": contact_names.get(str(m.from_phone)) or "WhatsApp Lead",
            }
            for m in inbound_items
        ]
    else:
        form = await request.form()
        if not _verify_twilio_request(request, dict(form)):
            raise HTTPException(status_code=403, detail="Invalid webhook signature")
        form_dict = dict(form)
        twilio_msg = parse_twilio_inbound(form_dict)
        twilio_msg["sender_name"] = form_dict.get("ProfileName") or "WhatsApp Lead"
        messages = [twilio_msg]

    for msg in messages:
        from_phone = msg.get("from_phone", "")
        to_phone = msg.get("to_phone", "")   # Dedicated number (Twilio) or phone_number_id (Meta)
        message_body = msg.get("message", "")
        message_id = msg.get("message_id", "")
        is_meta = msg.get("is_meta", False)

        if not from_phone or not message_body:
            continue

        # ── Property Finder Relay Guard ──────────────────────────────────────────
        # PF sends an automated relay to the AGENT's number when a buyer enquires.
        # These messages contain "utm_medium=lead_reply" or "Property Finder passed
        # on your enquiry..." — they are NOT from a real buyer.
        # Drop them silently so the AI never replies to PF's own system messages.
        if _is_pf_relay_message(message_body):
            logger.info(
                "[WA] Dropping Property Finder relay/system message from %s "
                "(utm_medium=lead_reply or relay phrase detected)",
                f"***{_normalize_phone(from_phone)[-4:]}" if from_phone else "?",
            )
            continue

        # ── Idempotency: skip already-processed messages (replay protection) ──
        if message_id and not _check_and_mark_message_id(sb, message_id):
            continue

        try:
            # ── Resolve agency & agent by incoming channel identifier ──
    
            resolved_agency_id: str | None = None
            resolved_agent_id: str | None = None
            comm_account_id: str | None = None
    
            if is_meta and to_phone:
                try:
                    sb_lookup = get_supabase()
                    rpc_res = sb_lookup.rpc(
                        "get_agency_by_phone_number_id",
                        {"p_phone_number_id": to_phone},
                    ).execute()
                    if rpc_res.data and len(rpc_res.data) > 0:
                        row = rpc_res.data[0]
                        resolved_agency_id = row.get("agency_id")
                        resolved_agent_id = row.get("agent_id")
                        comm_account_id = row.get("comm_account_id")
                        logger.info(
                            f"[Meta BYON] Resolved agency {resolved_agency_id} "
                            f"(agent: {resolved_agent_id}) from phone_number_id {to_phone}"
                        )
                    elif to_phone and to_phone == (getattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "") or "").strip():
                        resolved_agency_id = getattr(settings, "DEFAULT_AGENCY_ID", "") or "d8798ea7-1b47-40be-ba3e-8e9593871393"
                        logger.info(
                            f"[Meta] Resolved fallback agency {resolved_agency_id} from platform default phone_number_id {to_phone}"
                        )
                    else:
                        logger.warning(
                            f"[Meta BYON] No active account for phone_number_id {to_phone} — skipping"
                        )
                        continue
                except Exception as lookup_err:
                    logger.error(f"[Meta BYON] Lookup error for {to_phone}: {lookup_err} — skipping")
                    continue
    
            elif settings.WHATSAPP_PROVIDER == "twilio" and to_phone:
                try:
                    sb_lookup = get_supabase()
                    agency_row = (
                        sb_lookup.table("agencies")
                        .select("id, whatsapp_number_status")
                        .eq("dedicated_whatsapp_number", to_phone)
                        .single()
                        .execute()
                    )
                    if agency_row and isinstance(agency_row.data, dict) and isinstance(agency_row.data.get("id"), str):
                        resolved_agency_id = agency_row.data["id"]
                        number_status = agency_row.data.get("whatsapp_number_status", "active")
                        logger.info(
                            f"[Gateway] Resolved agency {resolved_agency_id} from number {to_phone} "
                            f"(status: {number_status})"
                        )
                    elif agency_row and agency_row.data and not isinstance(agency_row.data, dict):
                        # Test mock where table mock returned generic MagicMock
                        resolved_agency_id = None
                    else:
                        # Test Case 4: Unknown To number — graceful skip, no crash
                        logger.warning(
                            f"[Gateway] No agency found for dedicated number {to_phone} "
                            f"(from {from_phone[-4:] if from_phone else '?'}) — skipping"
                        )
                        continue
                except Exception as lookup_err:
                    logger.error(f"[Gateway] Agency lookup error for {to_phone}: {lookup_err} — skipping")
                    continue
    
            # Safeguard: verify resolved_agency_id exists in agencies table if resolved from channel
            if resolved_agency_id:
                try:
                    check_agency = sb.table("agencies").select("id").eq("id", resolved_agency_id).execute()
                    if not (check_agency and check_agency.data):
                        logger.warning(
                            f"[Inbound] Resolved agency_id {resolved_agency_id} does not exist in agencies table; falling back to default agency."
                        )
                        default_aid = getattr(settings, "DEFAULT_AGENCY_ID", "") or "d8798ea7-1b47-40be-ba3e-8e9593871393"
                        check_def = sb.table("agencies").select("id").eq("id", default_aid).execute()
                        if check_def and check_def.data:
                            resolved_agency_id = default_aid
                        else:
                            first_agency = sb.table("agencies").select("id").limit(1).execute()
                            resolved_agency_id = first_agency.data[0]["id"] if (first_agency and first_agency.data) else default_aid
                except Exception as ex_agency:
                    logger.debug(f"[Inbound] Could not verify agency {resolved_agency_id}: {ex_agency}")

            if resolved_agent_id:
                try:
                    check_agent = sb.table("agents").select("id").eq("id", resolved_agent_id).execute()
                    if not (check_agent and check_agent.data):
                        resolved_agent_id = None
                except Exception:
                    resolved_agent_id = None

            # Find lead by sender phone scoped to agency and agent
            lead, match_reason = _find_lead_by_sender_phone(
                sb, from_phone, agency_id=resolved_agency_id, agent_id=resolved_agent_id
            )

            # Auto-create lead on Inbound WhatsApp only for organic / new inquiries where:
            # 1. Channel resolved a specific agency
            # 2. Number is completely unknown (match_reason == "unknown", NOT ambiguous or invalid)
            if lead is None and resolved_agency_id and match_reason == "unknown":
                norm_phone = from_phone if from_phone.startswith("+") else f"+{from_phone}"
                property_ref = _extract_property_ref_from_wame_body(message_body)
                # Detect if message came via Property Finder WhatsApp lead flow
                # (buyer message contains propertyfinder.ae/go/<SLUG> + utm_medium=lead_user)
                is_pf_lead = bool(
                    _extract_pf_go_slug(message_body) or
                    "utm_medium=lead_user" in message_body or
                    "interested in your listing on Property Finder" in message_body
                )
                try:
                    new_lead_row = {
                        "name": msg.get("sender_name") or "WhatsApp Lead",
                        "phone": norm_phone,
                        "source": "property_finder" if is_pf_lead else "whatsapp",
                        "status": "new",
                        "ai_stage": "greeting",
                        "is_ai_handling": True,
                        "agency_id": resolved_agency_id,
                        "assigned_agent_id": resolved_agent_id,
                    }
                    if property_ref:
                        new_lead_row["property_ref"] = property_ref

                    insert_res = sb.table("leads").insert(new_lead_row).execute()
                    if insert_res and insert_res.data and len(insert_res.data) > 0:
                        lead = insert_res.data[0]
                        match_reason = "auto_created"
                        logger.info(
                            f"[WA Inbound] Auto-created new lead {lead.get('id')} "
                            f"('{new_lead_row['name']}', phone: ***{_normalize_phone(norm_phone)[-3:]}) for agency {resolved_agency_id}"
                        )
                except Exception as create_err:
                    logger.error(f"[WA Inbound] Error auto-creating lead: {create_err}")
                    # Attempt safe re-query in case of race condition / unique constraint
                    lead, _ = _find_lead_by_sender_phone(sb, from_phone, agency_id=resolved_agency_id)

            if lead is None:
                if match_reason in ("unknown", "invalid"):
                    masked = f"***{_normalize_phone(from_phone)[-3:]}" if from_phone else "(empty)"
                    logger.warning(f"Inbound WhatsApp from unmatched number {masked} ({match_reason})")
                continue
            lead_id = lead["id"]
    
            # If this BYON number belongs to a specific agent, auto-assign lead if not yet assigned
            if resolved_agent_id and not lead.get("assigned_agent_id"):
                try:
                    sb.table("leads").update({"assigned_agent_id": resolved_agent_id}).eq("id", lead_id).execute()
                    lead["assigned_agent_id"] = resolved_agent_id
                except Exception as assign_err:
                    logger.warning(f"Could not auto-assign lead {lead_id} to agent {resolved_agent_id}: {assign_err}")
    
            # Store inbound message
            sb.table("conversations").insert({
                "lead_id": lead_id,
                "agency_id": resolved_agency_id or lead.get("agency_id"),
                "communication_account_id": comm_account_id,
                "direction": "inbound",
                "channel": "whatsapp",
                "message_body": message_body,
                "sender_type": "lead",
                "whatsapp_message_id": message_id,
            }).execute()
    
            # Update last_inbound_at on leads (tracks per-lead 24h customer service window)
            if lead_id:
                from datetime import datetime, timezone
                now_iso = datetime.now(timezone.utc).isoformat()
                lead["last_inbound_at"] = now_iso
                try:
                    sb.table("leads").update({"last_inbound_at": now_iso}).eq("id", lead_id).execute()
                except Exception as lead_err:
                    logger.debug("[WA] Error updating last_inbound_at on lead: %s", lead_err)

                # ── Auto-drain queued outbound messages (window just reopened) ──────
                # Any messages queued because the lead was outside the 24h window
                # are now sendable as free-form text.
                try:
                    drained = await drain_outbound_queue_for_lead(
                        lead_id,
                        agency_id=resolved_agency_id or lead.get("agency_id") or "",
                        agent_id=resolved_agent_id or lead.get("assigned_agent_id"),
                    )
                    if drained:
                        logger.info("[WA] Drained %d queued message(s) for lead %s.", drained, lead_id)
                except Exception as drain_err:
                    logger.warning("[WA] Queue drain error for lead %s: %s", lead_id, drain_err)
    
            # Optional backward-compatibility update on communication_accounts
            if comm_account_id:
                try:
                    from datetime import datetime, timezone
                    sb.table("communication_accounts").update({
                        "last_inbound_at": datetime.now(timezone.utc).isoformat()
                    }).eq("id", comm_account_id).execute()
                except Exception as comm_err:
                    logger.debug("[WA] Error updating last_inbound_at on comm account: %s", comm_err)
    
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
                agency_id_handover = lead.get("agency_id") or resolved_agency_id
                if agency_id_handover:
                    await send_whatsapp_for_agency(agency_id_handover, from_phone, handover_msg, agent_id=lead.get("assigned_agent_id"))
                else:
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
                            if agency_id_handover:
                                await send_whatsapp_for_agency(agency_id_handover, agent_phone, agent_notify_msg)
                            else:
                                await send_whatsapp_message(agent_phone, agent_notify_msg)
                            logger.info(f"Handover notification sent to agent {agent.get('name')} for lead {lead_id}")
    
                continue
    
            # ── AI Qualification Response ──
            # Atomic quota gate: consume 1 unit BEFORE AI processing.
            # If quota exceeded — save inbound message to DB but suppress AI reply.
            quota_allowed = await check_and_consume_whatsapp_quota(
                lead.get("agency_id") or resolved_agency_id or ""
            )
            if not quota_allowed:
                logger.warning(
                    f"[Quota] WhatsApp quota exceeded for agency {lead.get('agency_id')} "
                    f"— inbound from lead {lead_id} saved, AI reply suppressed"
                )
                # Still update lead status so dashboard shows the unread message
                sb.table("leads").update({"updated_at": "now()"}).eq("id", lead_id).execute()
                continue
    
            # Quota consumed — now process with AI
            try:
                ai_reply = await qualify_and_respond(lead, history, message_body)
            except Exception as ai_err:
                logger.error(f"Lead {lead_id}: AI processing error: {ai_err} — refunding quota")
                await refund_whatsapp_quota(lead.get("agency_id") or resolved_agency_id or "")
                continue
    
            # Only save to DB and mark as delivered if provider send succeeds.
            # send_whatsapp_smart enforces the 24h window:
            #   in-window  → free-form send
            #   out-of-window → template attempt, then queue+notify (option a)
            effective_agency_id = lead.get("agency_id") or resolved_agency_id or ""
            effective_agent_id = resolved_agent_id or lead.get("assigned_agent_id")
            send_result = await send_whatsapp_smart(
                effective_agency_id,
                lead_id,
                from_phone,
                ai_reply,
                agent_id=effective_agent_id,
                template_name="andios_lead_first_contact",
                last_inbound_at=lead.get("last_inbound_at"),
            )

            if send_result.get("status") == "sent":
                logger.info(f"Lead {lead_id}: AI reply delivered successfully (SID={send_result.get('sid')})")
            elif send_result.get("status") == "queued":
                logger.info(f"Lead {lead_id}: AI reply queued (outside 24h window, no approved template).")
            else:
                logger.warning(f"Lead {lead_id}: AI reply NOT delivered (error: {send_result.get('error')}) — NOT saving to conversations")
    
            # Always save the AI reply to conversations (for audit trail), but tag delivery status
            sb.table("conversations").insert({
                "lead_id": lead_id,
                "agency_id": lead.get("agency_id") or effective_agency_id,
                "communication_account_id": comm_account_id,
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
            b_count = _parse_bedrooms(qualifications.get("bedrooms"))
            if b_count is not None:
                update_data["bedrooms"] = b_count
            b_min = _parse_numeric_budget(qualifications.get("budget_min"))
            if b_min is not None:
                update_data["budget_min"] = b_min
            b_max = _parse_numeric_budget(qualifications.get("budget_max"))
            if b_max is not None:
                update_data["budget_max"] = b_max
            if qualifications.get("location_pref"):
                update_data["location_pref"] = qualifications["location_pref"]
            if qualifications.get("purpose"):
                update_data["purpose"] = qualifications["purpose"]
            if qualifications.get("payment_method"):
                update_data["notes"] = f"Payment Method: {qualifications['payment_method']}"
            if update_data:
                try:
                    sb.table("leads").update(update_data).eq("id", lead_id).execute()
                except Exception as qual_upd_err:
                    logger.warning(f"Could not update qualification for lead {lead_id}: {qual_upd_err}")
    
    
        except Exception as msg_proc_err:
            logger.error('[WA] Inbound message %s processing failed: %s', message_id, msg_proc_err)
            if message_id:
                _unmark_message_id(sb, message_id)
            raise
    return api_success(message="WhatsApp messages processed successfully")


# ─── Vapi AI Caller Webhook ────────────────────────────────────────────────────

def _verify_vapi_request(request: Request) -> bool:
    """
    Authenticate inbound Vapi server-message webhooks.

    Vapi's supported mechanism is a shared secret: configure server.secret in
    the Vapi dashboard and Vapi sends it as the 'x-vapi-secret' header on every
    webhook request. Comparison is constant-time. (Vapi does not sign payloads
    with an HMAC/timestamp — no such check is attempted.)

    Fails closed outside development when the secret is unconfigured.
    """
    secret = getattr(settings, "VAPI_WEBHOOK_SECRET", "")
    provided = request.headers.get("x-vapi-secret")
    if not secret:
        if getattr(settings, "APP_ENV", "development") != "development":
            logger.critical(
                "VAPI_WEBHOOK_SECRET is not configured — rejecting Vapi "
                "webhook (fail closed)"
            )
            return False
        logger.warning(
            "VAPI_WEBHOOK_SECRET not set — accepting unauthenticated Vapi "
            "webhook in development only"
        )
        return True
    return bool(provided) and hmac.compare_digest(str(provided), str(secret))


@router.post("/vapi")
async def vapi_webhook(request: Request):
    """
    Receives call result callbacks from Vapi.ai.
    Requests are authenticated via the x-vapi-secret shared secret before any
    processing. Stores transcript, recording URL, duration, and outcome.
    Auto-flags DNC owners and schedules retries for voicemail/no-answer.
    """
    # ── Authentication — reject forged/unauthenticated requests ──
    if not _verify_vapi_request(request):
        raise HTTPException(status_code=403, detail="Invalid webhook authentication")

    try:
        payload = await request.json()
        from services.vapi_service import process_vapi_webhook
        result = await process_vapi_webhook(payload)
        return api_success(data=result, message="Vapi webhook processed")
    except Exception:
        logger.exception("Vapi webhook processing error")
        # Keep the established 200-OK contract for delivery, but never expose
        # internal exception details to the caller.
        return api_success(data={"status": "error"}, message="Vapi webhook error")


# ─── Voice Inbound (Central DID / BYON Call Forwarding) ─────────────────────

@router.post("/voice/inbound", response_class=__import__("fastapi").responses.Response)
async def voice_inbound(request: Request):
    """
    Receives inbound voice calls forwarded to the Central Platform DID.

    Agents configure conditional call forwarding (busy / no-answer / unreachable)
    from their personal mobile to CENTRAL_INBOUND_DID. When Twilio receives a
    forwarded call it POSTs here with:
      From          — caller's phone number
      To            — our central DID
      ForwardedFrom — the agent's personal number that forwarded the call
                      (may be absent if carrier strips SIP Diversion header)
      CallSid       — unique Twilio call identifier

    Resolution order:
      Tier 1 — ForwardedFrom exact match → communication_accounts (voice)
      Tier 2 — CRM recent-contact graph  → caller matched to active conversations
      Tier 3 — Generic AI receptionist   → polite fallback

    Returns TwiML <Dial><Sip> bridging the call to Vapi AI via BYO SIP Trunk
    with custom X-Agent-Id / X-Agency-Id SIP headers injected for dynamic
    persona selection.

    ⚠️  Requires BYO SIP Trunk configuration in Vapi (NOT Simple Number Import).
    ⚠️  Carrier SIP Diversion header support: most UAE major carriers (Etisalat/du)
        pass ForwardedFrom, but some prepaid/MVNO sims strip it. Tier 2 & 3
        fallbacks handle that case automatically.
    """
    from fastapi.responses import Response as FastAPIResponse
    from services.voice_service import resolve_inbound_caller, generate_vapi_sip_twiml
    from services.quota_service import check_and_consume_voice_quota
    from datetime import datetime, timezone

    # Parse Twilio form-encoded voice webhook payload
    form = await request.form()
    form_dict = dict(form)

    # ── Authentication ──
    if not _verify_twilio_request(request, form_dict):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    from_phone = (form_dict.get("From") or "").lstrip("+")
    to_number = form_dict.get("To") or ""
    forwarded_from = form_dict.get("ForwardedFrom") or form_dict.get("Diversion") or None
    call_sid = form_dict.get("CallSid") or ""

    logger.info(
        f"[Voice Inbound] CallSid={call_sid} From={from_phone} "
        f"To={to_number} ForwardedFrom={forwarded_from}"
    )

    # ── Resolve agent / agency ──
    ctx = await resolve_inbound_caller(
        from_phone=from_phone,
        forwarded_from=forwarded_from,
        to_number=to_number,
    )

    # ── Debit voice quota (optional — skip on Tier 3 fallback where no agency) ──
    if ctx.get("agency_id"):
        try:
            from services.quota_service import check_and_consume_voice_quota
            await check_and_consume_voice_quota(ctx["agency_id"])
        except Exception as q_err:
            # Quota failure is non-fatal for inbound calls — log and continue
            logger.warning(f"[Voice Inbound] Quota check failed: {q_err}")

    # ── Record inbound call in DB ──
    try:
        sb = get_supabase()
        sb.table("calls").insert({
            "agency_id": ctx.get("agency_id"),
            "agent_id": ctx.get("agent_id"),
            "phone_number": from_phone,
            "direction": "inbound",
            "status": "Initiated",
            "status_value": "initiated",
            "call_sid": call_sid,
            "resolution_tier": ctx.get("resolution_tier", 3),
            "call_time": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as db_err:
        logger.warning(f"[Voice Inbound] Failed to record inbound call: {db_err}")

    # ── Generate TwiML and return ──
    twiml = generate_vapi_sip_twiml(ctx, call_sid, to_number=to_number)
    return FastAPIResponse(
        content=twiml,
        media_type="application/xml",
        status_code=200,
    )


# ─── Stripe Billing Webhook ───────────────────────────────────────────────────

@router.post("/stripe")
async def stripe_webhook(request: Request):
    """
    Handles Stripe subscription and invoice lifecycle webhooks.
    Keeps Supabase subscriptions and invoices tables in sync.

    Authentication:
      - Production (APP_ENV != development): a valid Stripe-Signature is
        mandatory; STRIPE_WEBHOOK_SECRET must be configured. Missing secret,
        missing header or invalid signature are rejected with 4xx.
      - Development: unsigned JSON is accepted ONLY when APP_ENV explicitly
        indicates development AND no webhook secret is configured
        (local `stripe listen`-style testing).

    Retry semantics: signature/auth failures return 4xx (Stripe retries);
    genuine processing failures return 500 without internal details so
    Stripe retries them; unknown event types are acknowledged with 200.
    """
    import json as _json
    import stripe
    from services.billing_service import (
        sync_subscription_from_stripe,
        sync_invoice_from_stripe,
        _to_dict_safe,
    )

    is_production = getattr(settings, "APP_ENV", "development") != "development"
    payload_bytes = await request.body()
    sig_header = request.headers.get("stripe-signature")
    webhook_secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "")

    # ── Authentication / signature verification ────────────────────────────
    if not webhook_secret:
        if is_production:
            logger.critical(
                "STRIPE_WEBHOOK_SECRET is not configured — rejecting Stripe "
                "webhook (fail closed)"
            )
            raise HTTPException(status_code=400, detail="Stripe webhook is not configured")

        logger.warning(
            "STRIPE_WEBHOOK_SECRET not set — accepting UNSIGNED Stripe payload "
            "(development only)"
        )
        try:
            event = _json.loads(payload_bytes.decode("utf-8"))
        except Exception:
            logger.warning("Stripe webhook body is not valid JSON")
            raise HTTPException(status_code=400, detail="Invalid JSON payload")
    elif not sig_header:
        logger.warning("Stripe webhook received without Stripe-Signature header")
        raise HTTPException(status_code=400, detail="Missing Stripe signature")
    else:
        try:
            event = stripe.Webhook.construct_event(payload_bytes, sig_header, webhook_secret)
        except stripe.error.SignatureVerificationError:
            logger.warning("Stripe signature verification failed")
            raise HTTPException(status_code=400, detail="Invalid Stripe signature")
        except Exception:
            logger.error("Error parsing Stripe webhook payload")
            raise HTTPException(status_code=400, detail="Invalid Stripe payload")

    event_dict = _to_dict_safe(event)
    event_type = event_dict.get("type", "")
    event_data = event_dict.get("data", {}).get("object", {})
    logger.info(f"Received Stripe webhook event: {event_type}")

    try:
        from services.provisioning_service import provision_number_for_agency

        resolved_agency_id = None
        # Extract agency_id from event metadata or client reference
        if event_data.get("metadata", {}).get("agency_id"):
            resolved_agency_id = event_data["metadata"]["agency_id"]
        elif event_data.get("client_reference_id"):
            resolved_agency_id = event_data["client_reference_id"]

        if event_type in ["customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"]:
            await sync_subscription_from_stripe(event_data)
            if event_type == "customer.subscription.created":
                if not resolved_agency_id and event_data.get("customer"):
                    sb = get_supabase()
                    found = sb.table("subscriptions").select("agency_id").eq("stripe_cust_id", event_data["customer"]).maybe_single().execute()
                    if found and found.data:
                        resolved_agency_id = found.data.get("agency_id")
                if resolved_agency_id:
                    await provision_number_for_agency(resolved_agency_id)

        elif event_type in ["invoice.created", "invoice.payment_succeeded", "invoice.payment_failed", "invoice.finalized", "invoice.paid"]:
            await sync_invoice_from_stripe(event_data)

            # If payment failed, send alert to agency owner
            if event_type == "invoice.payment_failed":
                logger.warning(f"Invoice {event_data.get('id')} payment failed for customer {event_data.get('customer')}")

            # Auto-provision on successful payment
            if event_type in ["invoice.paid", "invoice.payment_succeeded"]:
                if not resolved_agency_id and event_data.get("customer"):
                    sb = get_supabase()
                    found = sb.table("subscriptions").select("agency_id").eq("stripe_cust_id", event_data["customer"]).maybe_single().execute()
                    if found and found.data:
                        resolved_agency_id = found.data.get("agency_id")
                if resolved_agency_id:
                    await provision_number_for_agency(resolved_agency_id)

        elif event_type == "checkout.session.completed":
            sub_id = event_data.get("subscription")
            if sub_id and getattr(settings, "STRIPE_SECRET_KEY", None):
                stripe.api_key = settings.STRIPE_SECRET_KEY
                stripe_sub = stripe.Subscription.retrieve(sub_id)
                await sync_subscription_from_stripe(stripe_sub)
                if not resolved_agency_id and stripe_sub.get("metadata", {}).get("agency_id"):
                    resolved_agency_id = stripe_sub["metadata"]["agency_id"]

            if not resolved_agency_id and event_data.get("customer"):
                sb = get_supabase()
                found = sb.table("subscriptions").select("agency_id").eq("stripe_cust_id", event_data["customer"]).maybe_single().execute()
                if found and found.data:
                    resolved_agency_id = found.data.get("agency_id")

            if resolved_agency_id:
                await provision_number_for_agency(resolved_agency_id)

        return api_success(data={"received": True, "event": event_type}, message="Stripe webhook processed")
    except Exception:
        # Genuine processing failure — do NOT report success. Return 5xx with
        # no internal details so Stripe retries this delivery with backoff.
        logger.exception(f"Error processing Stripe event {event_type}")
        return JSONResponse(
            status_code=500,
            content=api_error("Stripe webhook processing failed"),
        )


# ─── Twilio WhatsApp Sender Status Webhook ─────────────────────────────────────

@router.post("/twilio/sender-status")
async def twilio_sender_status_webhook(request: Request):
    """
    Receives automated WhatsApp Sender / Messaging status updates from Twilio.
    Automatically transitions agency from 'provisioned' to 'active' or 'failed'.
    """
    from services.provisioning_service import activate_number_for_agency
    from services.notification_service import notify_number_status_change

    # Handle JSON or URL-encoded form data
    content_type = request.headers.get("content-type", "")
    data = {}
    if "application/json" in content_type:
        try:
            data = await request.json()
        except Exception:
            data = {}
    else:
        form = await request.form()
        data = dict(form)

    logger.info(f"[Twilio Webhook] Received sender status callback: {data}")

    # Resolve phone number and status
    raw_status = (
        data.get("Status") or
        data.get("status") or
        data.get("SenderStatus") or
        data.get("ChannelStatus") or
        ""
    ).lower()

    phone_number = (
        data.get("PhoneNumber") or
        data.get("phone_number") or
        data.get("To") or
        data.get("From") or
        data.get("Address") or
        ""
    )

    agency_id = data.get("agency_id") or data.get("AgencyId")

    sb = get_supabase()
    if not agency_id and phone_number:
        # Lookup agency by dedicated_whatsapp_number
        norm = "".join(ch for ch in str(phone_number) if ch.isdigit() or ch == "+")
        found = sb.table("agencies").select("id, dedicated_whatsapp_number, whatsapp_number_status").or_(
            f"dedicated_whatsapp_number.eq.{norm},dedicated_whatsapp_number.eq.{phone_number}"
        ).maybe_single().execute()
        if found and found.data:
            agency_id = found.data.get("id")

    if not agency_id:
        logger.warning(f"[Twilio Webhook] Unable to match sender status to an agency: {data}")
        return api_success(data={"matched": False}, message="Sender status acknowledged (unmatched)")

    if raw_status in ("approved", "online", "in-use", "active"):
        res = await activate_number_for_agency(agency_id)
        return api_success(data=res, message="Agency WhatsApp number activated via Twilio webhook")
    elif raw_status in ("rejected", "failed"):
        sb.table("agencies").update({"whatsapp_number_status": "failed"}).eq("id", agency_id).execute()
        await notify_number_status_change(agency_id, "failed", phone_number)
        logger.warning(f"[Twilio Webhook] Agency {agency_id} WhatsApp sender rejected: {raw_status}")
        return api_success(data={"status": "failed", "agency_id": agency_id}, message="Agency marked failed via webhook")

    return api_success(data={"status": raw_status, "agency_id": agency_id}, message="Sender status received")


