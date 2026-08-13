"""
AI Service — OpenAI GPT-4o integration for:
  - Lead qualification
  - WhatsApp response generation  (with calendar slot booking)
  - Handover detection
  - Owner report generation
  - Lead scoring
"""
import json
import logging
from openai import AsyncOpenAI
from config import settings

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

SYSTEM_PROMPT_QUALIFY = """You are Andi, an AI real estate assistant for a Dubai property agency.
Your job is to qualify leads by understanding their requirements through friendly WhatsApp conversation.
Gather: budget (min/max in AED), bedrooms required, preferred location in Dubai, purpose (rent/buy), move-in timeline, and if renting, the number of cheques they prefer.
Be warm, concise, and professional. Use simple language. Never ask more than 1-2 questions at a time.
Always respond in the same language the lead uses (English or Arabic).

IMPORTANT — Viewing Booking Flow:
1. If the user wants to schedule a viewing, call the `check_calendar_slots` function first.
2. After receiving available slots, present the TWO closest options to the user.
3. If the user confirms a specific slot (e.g. "the first one", "Tuesday 10 AM", "yes"), call the `book_viewing` function with the chosen slot.
4. After booking, confirm the viewing details to the user.

Never make up dates or times — always use the function tools."""

SYSTEM_PROMPT_HANDOVER = """You analyze WhatsApp conversations to detect if a lead needs a human agent.
Return a JSON object with:
  - needs_handover: boolean
  - reason: string (why handover needed, or null)
  - confidence: float 0-1

Trigger handover when: legal questions, complex negotiations, complaints, pricing disputes, 
requests to speak with a human, off-topic queries, or abusive language."""

SYSTEM_PROMPT_REPORT = """You generate professional owner update reports for a Dubai real estate agency.
Given structured lead/performance data, write a concise executive summary (3-5 paragraphs) covering:
1. Lead volume and quality this period
2. Key performance metrics (response time, conversion rates)
3. Top performing agents
4. Deals closed and revenue
5. Recommendations for next period
Use professional, data-driven language suitable for a property owner."""

# ─── Tool Definitions ──────────────────────────────────────────────────────────

QUALIFY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "check_calendar_slots",
            "description": "Check available viewing slots in the agent's Google Calendar. Call this when the user asks to schedule or book a viewing.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_viewing",
            "description": "Book a viewing slot that the user has confirmed. Call this ONLY after showing the user available slots and they confirm one.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slot_start": {
                        "type": "string",
                        "description": "ISO 8601 datetime of the chosen slot start time (from the check_calendar_slots result)",
                    },
                    "slot_end": {
                        "type": "string",
                        "description": "ISO 8601 datetime of the chosen slot end time",
                    },
                },
                "required": ["slot_start", "slot_end"],
            },
        },
    },
]


# ─── Tool Execution Helpers ────────────────────────────────────────────────────

async def _execute_check_slots(lead_context: dict) -> str:
    """Fetch available calendar slots for the lead's agency."""
    from database.supabase_client import get_supabase
    from services.calendar_service import get_available_slots
    from datetime import datetime, timedelta
    import pytz

    sb = get_supabase()
    agency_id = lead_context.get("agency_id")

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
        return "No calendar connected. Tell the user you will have an agent contact them to schedule."

    auth_data = connector.data[0]["auth_data"]
    tz = pytz.timezone("Asia/Dubai")
    now = datetime.now(tz)
    date_from = now
    date_to = now + timedelta(days=3)
    calendar_id = settings.GOOGLE_SHARED_CALENDAR_ID or "primary"

    slots = get_available_slots(auth_data, calendar_id, date_from, date_to)
    if not slots:
        return "No slots available in the next 3 days. Ask the user if next week works."

    slot_strs = []
    for s in slots[:6]:
        dt = datetime.fromisoformat(s["start"])
        slot_strs.append(f'{dt.strftime("%A, %b %d at %I:%M %p")} (start={s["start"]}, end={s["end"]})')

    return f"Available slots:\n" + "\n".join(f"- {s}" for s in slot_strs) + "\n\nOffer the first two closest options to the user. When the user confirms a slot, call the book_viewing function with the exact start and end ISO strings."


async def _execute_book_viewing(lead_context: dict, slot_start: str, slot_end: str) -> str:
    """Create a viewing record + Google Calendar event for the confirmed slot."""
    from database.supabase_client import get_supabase
    from services.calendar_service import create_viewing_event
    from services.scheduler import schedule_viewing_jobs
    from datetime import datetime
    import pytz

    sb = get_supabase()
    agency_id = lead_context.get("agency_id")
    lead_id = lead_context.get("id")
    lead_name = lead_context.get("name", "Client")
    lead_phone = lead_context.get("phone", "")
    property_ref = lead_context.get("property_ref", "")

    # Get property address from lead or use a generic
    property_address = lead_context.get("property_address") or lead_context.get("property_ref") or "Property Viewing"

    # Parse datetime
    tz = pytz.timezone("Asia/Dubai")
    try:
        viewing_dt = datetime.fromisoformat(slot_start)
        if viewing_dt.tzinfo is None:
            viewing_dt = tz.localize(viewing_dt)
    except Exception:
        return "Invalid slot time. Please ask the user to choose again."

    # Get agent info
    assigned_agent_id = lead_context.get("assigned_agent_id")
    agent_name = None
    if assigned_agent_id:
        agent = sb.table("agents").select("name").eq("id", assigned_agent_id).execute()
        if agent.data:
            agent_name = agent.data[0]["name"]

    # Create Google Calendar event
    google_event_id = None
    google_meet_link = None
    try:
        connector = (
            sb.table("connectors")
            .select("auth_data")
            .eq("name", "google_calendar")
            .eq("agency_id", agency_id)
            .eq("is_connected", True)
            .limit(1)
            .execute()
        )
        if connector.data and connector.data[0].get("auth_data"):
            token_data = connector.data[0]["auth_data"]
            calendar_id = settings.GOOGLE_SHARED_CALENDAR_ID or "primary"
            cal_result = create_viewing_event(
                token_data=token_data,
                calendar_id=calendar_id,
                lead_name=lead_name,
                lead_phone=lead_phone,
                property_address=property_address,
                start_datetime=viewing_dt,
                agent_name=agent_name,
            )
            google_event_id = cal_result.get("event_id")
            google_meet_link = cal_result.get("meet_link")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Calendar event creation failed during AI booking: {e}")

    # Insert viewing record
    insert_data = {
        "lead_id": lead_id,
        "agency_id": agency_id,
        "property_address": property_address,
        "property_ref": property_ref,
        "viewing_datetime": viewing_dt.isoformat(),
        "duration_minutes": 60,
        "status": "scheduled",
        "google_event_id": google_event_id,
        "google_meet_link": google_meet_link,
        "notes": "Booked via Andi AI WhatsApp",
    }
    if assigned_agent_id:
        insert_data["agent_id"] = assigned_agent_id

    result = sb.table("viewings").insert(insert_data).execute()
    viewing = result.data[0] if result.data else {}

    # Schedule automated reminders
    if viewing:
        schedule_viewing_jobs(
            viewing_id=viewing["id"],
            viewing_datetime=viewing_dt,
            lead_id=lead_id,
            agency_id=agency_id,
        )

    # Update lead status
    sb.table("leads").update({"status": "viewing_booked", "ai_stage": "viewing_booked"}).eq("id", lead_id).execute()

    dt_str = viewing_dt.strftime("%A, %d %B at %I:%M %p")
    return f"VIEWING BOOKED SUCCESSFULLY! Details: {property_address} on {dt_str}. Confirm this to the user and tell them they'll receive a reminder 24h before."


# ─── Main AI Functions ─────────────────────────────────────────────────────────

async def qualify_and_respond(
    lead_context: dict,
    conversation_history: list[dict],
    new_message: str,
) -> str:
    """Generate AI WhatsApp response for lead qualification with tool use (slots + booking)."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT_QUALIFY}]

    # Add lead context as first assistant context
    context_msg = (
        f"Lead info: Name={lead_context.get('name')}, "
        f"Property interest={lead_context.get('property_ref', 'unknown')}, "
        f"Source={lead_context.get('source')}, "
        f"Current stage={lead_context.get('ai_stage', 'greeting')}, "
        f"Lead ID={lead_context.get('id')}"
    )
    messages.append({"role": "system", "content": context_msg})

    # Add conversation history
    for msg in conversation_history[-10:]:  # Last 10 messages for context
        role = "assistant" if msg["sender_type"] in ("ai", "agent") else "user"
        messages.append({"role": role, "content": msg["message_body"]})

    # Add the new inbound message
    messages.append({"role": "user", "content": new_message})

    try:
        # First API call — may trigger tool use
        response = await client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=messages,
            tools=QUALIFY_TOOLS,
            max_tokens=400,
            temperature=0.7,
        )

        response_message = response.choices[0].message

        # Handle tool calls (may be multiple)
        max_tool_rounds = 3
        round_count = 0

        while response_message.tool_calls and round_count < max_tool_rounds:
            round_count += 1
            messages.append(response_message)

            for tool_call in response_message.tool_calls:
                fn_name = tool_call.function.name
                fn_args = json.loads(tool_call.function.arguments) if tool_call.function.arguments else {}

                if fn_name == "check_calendar_slots":
                    tool_result = await _execute_check_slots(lead_context)
                elif fn_name == "book_viewing":
                    tool_result = await _execute_book_viewing(
                        lead_context,
                        slot_start=fn_args.get("slot_start", ""),
                        slot_end=fn_args.get("slot_end", ""),
                    )
                else:
                    tool_result = f"Unknown function: {fn_name}"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": fn_name,
                    "content": tool_result,
                })

            # Call again with tool results
            response = await client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                messages=messages,
                tools=QUALIFY_TOOLS,
                max_tokens=400,
                temperature=0.7,
            )
            response_message = response.choices[0].message

        return response_message.content.strip() if response_message.content else "I'll have an agent follow up with you shortly."
    except Exception as e:
        logger.error(f"OpenAI error in qualify_and_respond: {e}")
        return "Thank you for your message! Our team will get back to you shortly."


async def detect_handover(
    conversation_history: list[dict],
    new_message: str,
) -> dict:
    """Detect if a lead message requires human handover. Returns {needs_handover, reason, confidence}."""
    history_text = "\n".join(
        [f"{m['sender_type'].upper()}: {m['message_body']}" for m in conversation_history[-6:]]
    )
    prompt = f"Conversation history:\n{history_text}\n\nNew message from lead: {new_message}"

    try:
        response = await client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT_HANDOVER},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            max_tokens=150,
            temperature=0.1,
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        logger.error(f"OpenAI error in detect_handover: {e}")
        return {"needs_handover": False, "reason": None, "confidence": 0.0}


async def score_lead(lead_context: dict, conversation_history: list[dict]) -> int:
    """Score a lead 0-100 based on qualification data gathered."""
    conversation_text = "\n".join(
        [f"{m['sender_type']}: {m['message_body']}" for m in conversation_history[-15:]]
    )
    prompt = f"""Lead data: {json.dumps(lead_context)}
Conversation: {conversation_text}

Score this lead 0-100 based on:
- Budget clarity (0-25 pts)
- Specific requirements / bedrooms (0-25 pts)  
- Timeline urgency (0-25 pts)
- Responsiveness / engagement (0-25 pts)

Return ONLY a JSON: {{"score": <int>, "reason": "<brief reason>"}}"""

    response = await client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        max_tokens=100,
        temperature=0.1,
    )
    try:
        result = json.loads(response.choices[0].message.content)
        return max(0, min(100, int(result.get("score", 50))))
    except Exception:
        return 50


async def generate_owner_report(report_data: dict) -> str:
    """Generate AI narrative for owner report from structured data."""
    prompt = f"Generate an owner update report for the period {report_data['period_start']} to {report_data['period_end']}.\n\nData:\n{json.dumps(report_data, indent=2)}"

    response = await client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT_REPORT},
            {"role": "user", "content": prompt},
        ],
        max_tokens=800,
        temperature=0.4,
    )
    return response.choices[0].message.content.strip()


async def extract_lead_qualifications(conversation_history: list[dict]) -> dict:
    """Extract structured qualification data from a conversation."""
    conversation_text = "\n".join(
        [f"{m['sender_type']}: {m['message_body']}" for m in conversation_history]
    )
    prompt = f"""From this WhatsApp conversation, extract lead qualification data.
Return JSON with these fields (null if not mentioned):
{{"bedrooms": null, "budget_min": null, "budget_max": null, "location_pref": null, "purpose": null, "move_in_timeline": null, "number_of_cheques": null}}

Conversation:
{conversation_text}"""

    response = await client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        max_tokens=200,
        temperature=0.1,
    )
    try:
        return json.loads(response.choices[0].message.content)
    except Exception:
        return {}
