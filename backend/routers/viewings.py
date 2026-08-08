"""
Viewings Router — Calendar page
GET    /viewings                    — list all viewings
GET    /viewings/available-slots    — get free agent calendar slots
POST   /viewings                    — create viewing + Google Calendar event
PATCH  /viewings/{id}               — update/cancel viewing
GET    /viewings/{id}               — single viewing detail
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional
from uuid import UUID
from datetime import datetime
from database.supabase_client import get_supabase
from models.viewing import ViewingCreate, ViewingUpdate, ViewingResponse, AvailableSlotsRequest, TimeSlot
from services.calendar_service import (
    get_available_slots,
    create_viewing_event,
    cancel_viewing_event,
)
from services.scheduler import schedule_viewing_jobs, cancel_viewing_jobs
from middleware.auth_middleware import verify_token
from utils.response import api_success, ApiResponse
from utils.tenant import (
    apply_viewing_scope,
    verify_viewing_access,
    verify_lead_access,
    require_agency_id,
    is_management_role,
    require_agent_id,
)
import json, logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/viewings", tags=["Viewings"])


def _get_calendar_token(sb, agency_id: str) -> tuple[str, dict]:
    """Get Google Calendar token and calendar ID from connectors table."""
    connector = (
        sb.table("connectors")
        .select("auth_data")
        .eq("name", "google_calendar")
        .eq("agency_id", agency_id)
        .eq("is_connected", True)
        .limit(1)
        .execute()
    )
    if not connector.data or not connector.data[0].get("auth_data"):
        # Fallback: legacy global connector (no agency_id)
        connector = (
            sb.table("connectors")
            .select("auth_data")
            .eq("name", "google_calendar")
            .eq("is_connected", True)
            .limit(1)
            .execute()
        )
    if not connector.data or not connector.data[0].get("auth_data"):
        raise HTTPException(status_code=400, detail="Google Calendar not connected. Go to Connectors to connect.")
    auth_data = connector.data[0]["auth_data"]
    from config import settings
    calendar_id = settings.GOOGLE_SHARED_CALENDAR_ID or "primary"
    return calendar_id, auth_data


@router.get("", response_model=ApiResponse[list[dict]])
async def list_viewings(
    agent_id: Optional[UUID] = Query(None),
    status: Optional[str] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    limit: int = Query(100, le=500),
    current_user: dict = Depends(verify_token),
):
    """List all viewings for the Calendar dashboard page."""
    sb = get_supabase()
    query = sb.table("viewings").select("*, leads(name, source, bedrooms), agents(name)").order("viewing_datetime", desc=False)
    query = apply_viewing_scope(query, current_user)

    if agent_id:
        query = query.eq("agent_id", str(agent_id))
    if status:
        query = query.eq("status", status)
    if date_from:
        query = query.gte("viewing_datetime", date_from.isoformat())
    if date_to:
        query = query.lte("viewing_datetime", date_to.isoformat())

    result = query.limit(limit).execute()
    
    formatted_viewings = []
    for row in result.data:
        lead_data = row.get("leads") or {}
        agent_data = row.get("agents") or {}
        
        prop_type = "Apartment"
        if row.get("property_address") and "villa" in row["property_address"].lower():
            prop_type = "Villa"
            
        formatted_viewings.append({
            "id": row["id"],
            "lead_id": row["lead_id"],
            "clientName": lead_data.get("name", "Unknown Client"),
            "propertyDetails": f"{lead_data.get('bedrooms', 1)}BR - {row.get('property_address', '')}",
            "agentName": agent_data.get("name", "Unassigned"),
            "source": lead_data.get("source", "Unknown"),
            "viewing_datetime": row["viewing_datetime"],
            "duration_minutes": row["duration_minutes"],
            "status": row["status"],
            "google_event_id": row.get("google_event_id"),
            "google_meet_link": row.get("google_meet_link"),
        })

    return api_success(data=formatted_viewings, message="Viewings retrieved successfully")


@router.get("/available-slots")
async def available_slots(
    date_from: datetime = Query(...),
    date_to: datetime = Query(...),
    agent_id: Optional[UUID] = Query(None),
    duration_minutes: int = Query(60),
    current_user: dict = Depends(verify_token),
):
    """Get free viewing slots from Google Calendar. Used by AI to offer slots to leads."""
    sb = get_supabase()
    agency_id = require_agency_id(current_user)
    try:
        calendar_id, token_data = _get_calendar_token(sb, agency_id)

        # Per-agent mode: use agent's calendar
        if agent_id:
            agent = sb.table("agents").select("calendar_id, name").eq("id", str(agent_id)).single().execute()
            if agent.data and agent.data.get("calendar_id"):
                calendar_id = agent.data["calendar_id"]

        slots = get_available_slots(token_data, calendar_id, date_from, date_to, duration_minutes)
        return api_success(data={"slots": slots, "calendar_id": calendar_id}, message="Available slots retrieved")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting slots: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("", response_model=ApiResponse[ViewingResponse])
async def create_viewing(body: ViewingCreate, current_user: dict = Depends(verify_token)):
    """
    Book a property viewing:
    1. Creates record in Supabase
    2. Creates Google Calendar event
    3. Schedules automated reminders
    4. Updates lead status to viewing_booked
    """
    sb = get_supabase()
    agency_id = require_agency_id(current_user)

    # Verify lead belongs to this agency
    lead_data = await verify_lead_access(str(body.lead_id), current_user)

    # Get agent info (if specified)
    agent_name = None
    assigned_agent_id = body.agent_id or current_user.get("agent_id")
    if assigned_agent_id:
        agent = sb.table("agents").select("name").eq("id", str(assigned_agent_id)).eq("agency_id", agency_id).single().execute()
        if agent.data:
            agent_name = agent.data["name"]

    # Create Google Calendar event
    google_event_id = None
    google_meet_link = None
    try:
        calendar_id, token_data = _get_calendar_token(sb, agency_id)
        cal_result = create_viewing_event(
            token_data=token_data,
            calendar_id=calendar_id,
            lead_name=lead_data["name"],
            lead_phone=lead_data["phone"],
            property_address=body.property_address,
            start_datetime=body.viewing_datetime,
            duration_minutes=body.duration_minutes,
            agent_name=agent_name,
        )
        google_event_id = cal_result.get("event_id")
        google_meet_link = cal_result.get("meet_link")
    except HTTPException:
        logger.warning("Google Calendar not connected — creating viewing without calendar event")
    except Exception as e:
        logger.error(f"Calendar event creation failed: {e}")

    # Insert viewing record
    insert_data = {
        "lead_id": str(body.lead_id),
        "agency_id": agency_id,
        "property_address": body.property_address,
        "property_ref": body.property_ref,
        "viewing_datetime": body.viewing_datetime.isoformat(),
        "duration_minutes": body.duration_minutes,
        "status": body.status if body.status else "scheduled",
        "notes": body.notes,
        "google_event_id": google_event_id,
        "google_meet_link": google_meet_link,
    }
    if assigned_agent_id:
        insert_data["agent_id"] = str(assigned_agent_id)
    if body.feedback_received:
        insert_data["feedback_received"] = body.feedback_received
    if google_event_id:
        insert_data["google_event_id"] = google_event_id
    if google_meet_link:
        insert_data["google_meet_link"] = google_meet_link

    result = sb.table("viewings").insert(insert_data).execute()
    viewing = result.data[0]

    # Schedule automated reminders
    schedule_viewing_jobs(
        viewing_id=viewing["id"],
        viewing_datetime=body.viewing_datetime,
        lead_id=str(body.lead_id),
        agency_id=agency_id,
    )

    # Update lead status
    sb.table("leads").update({"status": "viewing_booked"}).eq("id", str(body.lead_id)).execute()

    # Send confirmation WhatsApp to lead
    from services.whatsapp_service import send_whatsapp_message
    dt_str = body.viewing_datetime.strftime("%A, %d %B at %I:%M %p")
    confirm_msg = (
        f"✅ Your viewing is confirmed!\n\n"
        f"📍 {body.property_address}\n"
        f"📅 {dt_str}\n"
        f"{'👤 Agent: ' + agent_name if agent_name else ''}\n"
        f"{'🎥 Google Meet: ' + google_meet_link if google_meet_link else ''}\n\n"
        f"We'll send you a reminder 24 hours before. See you there! 🏠"
    )
    await send_whatsapp_message(lead_data["phone"], confirm_msg)
    sb.table("conversations").insert({
        "lead_id": str(body.lead_id),
        "agency_id": agency_id,
        "direction": "outbound",
        "channel": "whatsapp",
        "message_body": confirm_msg,
        "sender_type": "ai",
    }).execute()

    logger.info(f"Viewing created: {viewing['id']} for lead {body.lead_id}")
    return api_success(data=viewing, message="Viewing created successfully", status_code=201)


@router.get("/{viewing_id}", response_model=ApiResponse[ViewingResponse])
async def get_viewing(viewing_id: UUID, current_user: dict = Depends(verify_token)):
    viewing = await verify_viewing_access(str(viewing_id), current_user)
    return api_success(data=viewing, message="Viewing retrieved successfully")


@router.patch("/{viewing_id}", response_model=ApiResponse[ViewingResponse])
async def update_viewing(viewing_id: UUID, body: ViewingUpdate, current_user: dict = Depends(verify_token)):
    """Update or cancel a viewing. Cancels Google Calendar event if status=cancelled."""
    sb = get_supabase()
    agency_id = require_agency_id(current_user)
    existing_data = await verify_viewing_access(str(viewing_id), current_user)

    update_data = body.model_dump(exclude_none=True)
    if "viewing_datetime" in update_data:
        update_data["viewing_datetime"] = update_data["viewing_datetime"].isoformat()
    if "agent_id" in update_data:
        update_data["agent_id"] = str(update_data["agent_id"])

    # Handle cancellation
    if body.status and body.status.value == "cancelled":
        event_id = existing_data.get("google_event_id")
        if event_id:
            try:
                calendar_id, token_data = _get_calendar_token(sb, agency_id)
                cancel_viewing_event(token_data, calendar_id, event_id)
            except Exception as e:
                logger.error(f"Failed to cancel calendar event: {e}")
        cancel_viewing_jobs(str(viewing_id))

        # Notify lead
        lead_id = existing_data["lead_id"]
        lead = sb.table("leads").select("phone, name").eq("id", lead_id).single().execute()
        if lead.data:
            from services.whatsapp_service import send_whatsapp_message
            cancel_msg = (
                f"Hi {lead.data['name'].split()[0]}, your viewing has been cancelled. "
                f"Please contact us to reschedule. 📅"
            )
            await send_whatsapp_message(lead.data["phone"], cancel_msg)

    result = sb.table("viewings").update(update_data).eq("id", str(viewing_id)).execute()
    return api_success(data=result.data[0], message="Viewing updated successfully")
