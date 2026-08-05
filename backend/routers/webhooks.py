"""
Webhooks Router — handles inbound leads from Property Finder
and inbound WhatsApp messages.

POST /webhooks/property-finder   — new lead from portal
GET  /webhooks/whatsapp          — WhatsApp webhook verification
POST /webhooks/whatsapp          — inbound WhatsApp message
"""
import time
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


# ─── Property Finder Webhook ───────────────────────────────────────────────────

@router.post("/property-finder")
async def property_finder_webhook(request: Request):
    """
    Receives new lead from Property Finder portal.
    Deduplicates, stores lead, triggers AI WhatsApp greeting.
    """
    start_time = time.time()
    sb = get_supabase()
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
        # Standard PF lead webhook format
        lead_data = payload.get("lead", payload)  # handle both wrapped and flat
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
    Routes to AI for qualification, or flags for agent handover.
    """
    sb = get_supabase()
    # Parse provider-specific format
    if settings.WHATSAPP_PROVIDER == "360dialog":
        payload = await request.json()
        messages = parse_360dialog_inbound(payload)
    else:
        form = await request.form()
        messages = [parse_twilio_inbound(dict(form))]

    for msg in messages:
        from_phone = msg.get("from_phone", "")
        message_body = msg.get("message", "")
        message_id = msg.get("message_id", "")

        if not from_phone or not message_body:
            continue

        # Find lead by phone
        clean_phone = from_phone.replace("+", "").replace(" ", "")
        lead_result = (
            sb.table("leads")
            .select("*")
            .ilike("phone", f"%{clean_phone[-9:]}")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        if not lead_result.data:
            logger.warning(f"Inbound WhatsApp from unknown number: {from_phone}")
            continue

        lead = lead_result.data[0]
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
            continue

        # ── AI Qualification Response ──
        ai_reply = await qualify_and_respond(lead, history, message_body)
        await send_whatsapp_message(from_phone, ai_reply)

        sb.table("conversations").insert({
            "lead_id": lead_id,
            "agency_id": lead.get("agency_id"),
            "direction": "outbound",
            "channel": "whatsapp",
            "message_body": ai_reply,
            "sender_type": "ai",
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
