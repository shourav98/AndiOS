"""
AI Service — OpenAI GPT-4o integration for:
  - Lead qualification
  - WhatsApp response generation
  - Handover detection
  - Owner report generation
"""
from openai import AsyncOpenAI
from config import settings
import json

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

SYSTEM_PROMPT_QUALIFY = """You are Andi, an AI real estate assistant for a Dubai property agency.
Your job is to qualify leads by understanding their requirements through friendly WhatsApp conversation.
Gather: budget (min/max in AED), bedrooms required, preferred location in Dubai, purpose (rent/buy), move-in timeline.
Be warm, concise, and professional. Use simple language. Never ask more than 1-2 questions at a time.
Always respond in the same language the lead uses (English or Arabic)."""

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


async def qualify_and_respond(
    lead_context: dict,
    conversation_history: list[dict],
    new_message: str,
) -> str:
    """Generate AI WhatsApp response for lead qualification."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT_QUALIFY}]

    # Add lead context as first assistant context
    context_msg = (
        f"Lead info: Name={lead_context.get('name')}, "
        f"Property interest={lead_context.get('property_ref', 'unknown')}, "
        f"Source={lead_context.get('source')}, "
        f"Current stage={lead_context.get('ai_stage', 'greeting')}"
    )
    messages.append({"role": "system", "content": context_msg})

    # Add conversation history
    for msg in conversation_history[-10:]:  # Last 10 messages for context
        role = "assistant" if msg["sender_type"] in ("ai", "agent") else "user"
        messages.append({"role": role, "content": msg["message_body"]})

    # Add the new inbound message
    messages.append({"role": "user", "content": new_message})

    response = await client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=messages,
        max_tokens=300,
        temperature=0.7,
    )
    return response.choices[0].message.content.strip()


async def detect_handover(
    conversation_history: list[dict],
    new_message: str,
) -> dict:
    """Detect if a lead message requires human handover. Returns {needs_handover, reason, confidence}."""
    history_text = "\n".join(
        [f"{m['sender_type'].upper()}: {m['message_body']}" for m in conversation_history[-6:]]
    )
    prompt = f"Conversation history:\n{history_text}\n\nNew message from lead: {new_message}"

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
    try:
        return json.loads(response.choices[0].message.content)
    except Exception:
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
{{"bedrooms": null, "budget_min": null, "budget_max": null, "location_pref": null, "purpose": null, "move_in_timeline": null}}

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
