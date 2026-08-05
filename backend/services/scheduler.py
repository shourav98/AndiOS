"""
Scheduler Service — APScheduler background jobs for:
  - 24h viewing reminders
  - 2h viewing reminders
  - Post-viewing follow-up (24h after)
  - Feedback collection (48h after)
  - Re-engagement sequences
"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timedelta
from database.supabase_client import get_supabase
from services.whatsapp_service import send_whatsapp_message
from services.ai_service import generate_owner_report
import logging

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Asia/Dubai")


# ─── Reminder Messages ─────────────────────────────────────────────────────────

def _reminder_24h_msg(lead_name: str, property_address: str, dt: datetime) -> str:
    time_str = dt.strftime("%I:%M %p")
    date_str = dt.strftime("%A, %d %B")
    return (
        f"Hi {lead_name}! 👋\n\n"
        f"Just a reminder that your property viewing is scheduled for tomorrow:\n\n"
        f"📍 {property_address}\n"
        f"📅 {date_str} at {time_str}\n\n"
        f"Please reply CONFIRM to confirm or CANCEL if you need to reschedule.\n\n"
        f"Looking forward to showing you the property! 🏠"
    )


def _reminder_2h_msg(lead_name: str, property_address: str, dt: datetime) -> str:
    time_str = dt.strftime("%I:%M %p")
    return (
        f"Hi {lead_name}! 🏠\n\n"
        f"Your viewing is in 2 hours at {time_str}.\n"
        f"📍 {property_address}\n\n"
        f"Our agent will meet you at the property entrance. See you soon!"
    )


def _post_viewing_msg(lead_name: str, property_address: str) -> str:
    return (
        f"Hi {lead_name}, thank you for viewing {property_address} today! 🙏\n\n"
        f"We'd love to know what you thought.\n"
        f"👍 Reply YES if you're interested\n"
        f"👎 Reply NO if it wasn't the right fit\n"
        f"🤔 Reply MAYBE if you'd like more options\n\n"
        f"We're here to help you find your perfect home!"
    )


def _feedback_followup_msg(lead_name: str) -> str:
    return (
        f"Hi {lead_name}! 😊\n\n"
        f"Have you had a chance to think it over? We have some exciting new listings "
        f"that might be an even better match for you.\n\n"
        f"Would you like me to send you some options? Just reply YES and I'll get those across to you right away!"
    )


# ─── Job Functions ─────────────────────────────────────────────────────────────

async def send_viewing_reminder_24h(viewing_id: str):
    """Job: send 24h before viewing reminder."""
    try:
        sb = get_supabase()
        viewing = sb.table("viewings").select("*, leads(name, phone)").eq("id", viewing_id).single().execute()
        v = viewing.data
        if not v or v["status"] in ("cancelled", "completed"):
            return
        lead = v.get("leads", {})
        msg = _reminder_24h_msg(
            lead.get("name", "there"),
            v["property_address"],
            datetime.fromisoformat(v["viewing_datetime"]),
        )
        await send_whatsapp_message(lead.get("phone", ""), msg)
        # Mark reminder sent
        sb.table("viewings").update({"reminder_24h_sent": True}).eq("id", viewing_id).execute()
        # Log to follow_ups
        sb.table("follow_ups").update({"status": "sent", "sent_at": datetime.utcnow().isoformat()}).eq(
            "viewing_id", viewing_id
        ).eq("type", "reminder_24h").execute()
        logger.info(f"24h reminder sent for viewing {viewing_id}")
    except Exception as e:
        logger.error(f"Error sending 24h reminder for viewing {viewing_id}: {e}")


async def send_viewing_reminder_2h(viewing_id: str):
    """Job: send 2h before viewing reminder."""
    try:
        sb = get_supabase()
        viewing = sb.table("viewings").select("*, leads(name, phone)").eq("id", viewing_id).single().execute()
        v = viewing.data
        if not v or v["status"] in ("cancelled", "completed"):
            return
        lead = v.get("leads", {})
        msg = _reminder_2h_msg(
            lead.get("name", "there"),
            v["property_address"],
            datetime.fromisoformat(v["viewing_datetime"]),
        )
        await send_whatsapp_message(lead.get("phone", ""), msg)
        sb.table("viewings").update({"reminder_2h_sent": True}).eq("id", viewing_id).execute()
        sb.table("follow_ups").update({"status": "sent", "sent_at": datetime.utcnow().isoformat()}).eq(
            "viewing_id", viewing_id
        ).eq("type", "reminder_2h").execute()
        logger.info(f"2h reminder sent for viewing {viewing_id}")
    except Exception as e:
        logger.error(f"Error sending 2h reminder for viewing {viewing_id}: {e}")


async def send_post_viewing_followup(viewing_id: str):
    """Job: send follow-up 24h after viewing."""
    try:
        sb = get_supabase()
        viewing = sb.table("viewings").select("*, leads(name, phone)").eq("id", viewing_id).single().execute()
        v = viewing.data
        if not v:
            return
        lead = v.get("leads", {})
        msg = _post_viewing_msg(lead.get("name", "there"), v["property_address"])
        await send_whatsapp_message(lead.get("phone", ""), msg)
        sb.table("viewings").update({"feedback_requested": True}).eq("id", viewing_id).execute()
        sb.table("follow_ups").update({"status": "sent", "sent_at": datetime.utcnow().isoformat()}).eq(
            "viewing_id", viewing_id
        ).eq("type", "post_viewing").execute()
        logger.info(f"Post-viewing follow-up sent for viewing {viewing_id}")
    except Exception as e:
        logger.error(f"Error sending post-viewing follow-up for viewing {viewing_id}: {e}")


async def send_feedback_followup(viewing_id: str):
    """Job: send re-engagement 48h after viewing."""
    try:
        sb = get_supabase()
        viewing = sb.table("viewings").select("*, leads(name, phone)").eq("id", viewing_id).single().execute()
        v = viewing.data
        if not v:
            return
        lead = v.get("leads", {})
        # Only send if no feedback received yet
        if v.get("feedback_received"):
            return
        msg = _feedback_followup_msg(lead.get("name", "there"))
        await send_whatsapp_message(lead.get("phone", ""), msg)
        sb.table("follow_ups").update({"status": "sent", "sent_at": datetime.utcnow().isoformat()}).eq(
            "viewing_id", viewing_id
        ).eq("type", "feedback").execute()
        logger.info(f"Feedback follow-up sent for viewing {viewing_id}")
    except Exception as e:
        logger.error(f"Error sending feedback follow-up for viewing {viewing_id}: {e}")


# ─── Schedule Jobs for a New Viewing ──────────────────────────────────────────

def schedule_viewing_jobs(
    viewing_id: str,
    viewing_datetime: datetime,
    lead_id: str,
    agency_id: str | None = None,
):
    """
    Schedule all automated messages for a newly booked viewing.
    Call this immediately after creating a viewing.
    """
    sb = get_supabase()
    now = datetime.utcnow()

    # Resolve agency_id from lead if not provided
    if not agency_id:
        lead = sb.table("leads").select("agency_id").eq("id", lead_id).single().execute()
        agency_id = lead.data.get("agency_id") if lead.data else None

    # 24h reminder
    remind_24h = viewing_datetime - timedelta(hours=24)
    if remind_24h > now:
        scheduler.add_job(
            send_viewing_reminder_24h,
            trigger=DateTrigger(run_date=remind_24h),
            args=[viewing_id],
            id=f"remind_24h_{viewing_id}",
            replace_existing=True,
        )
        sb.table("follow_ups").insert({
            "lead_id": lead_id,
            "viewing_id": viewing_id,
            "agency_id": agency_id,
            "type": "reminder_24h",
            "scheduled_at": remind_24h.isoformat(),
            "status": "pending",
        }).execute()

    # 2h reminder
    remind_2h = viewing_datetime - timedelta(hours=2)
    if remind_2h > now:
        scheduler.add_job(
            send_viewing_reminder_2h,
            trigger=DateTrigger(run_date=remind_2h),
            args=[viewing_id],
            id=f"remind_2h_{viewing_id}",
            replace_existing=True,
        )
        sb.table("follow_ups").insert({
            "lead_id": lead_id,
            "viewing_id": viewing_id,
            "agency_id": agency_id,
            "type": "reminder_2h",
            "scheduled_at": remind_2h.isoformat(),
            "status": "pending",
        }).execute()

    # Post-viewing follow-up (24h after)
    post_viewing = viewing_datetime + timedelta(hours=24)
    scheduler.add_job(
        send_post_viewing_followup,
        trigger=DateTrigger(run_date=post_viewing),
        args=[viewing_id],
        id=f"post_viewing_{viewing_id}",
        replace_existing=True,
    )
    sb.table("follow_ups").insert({
        "lead_id": lead_id,
        "viewing_id": viewing_id,
        "agency_id": agency_id,
        "type": "post_viewing",
        "scheduled_at": post_viewing.isoformat(),
        "status": "pending",
    }).execute()

    # Feedback follow-up (48h after)
    feedback = viewing_datetime + timedelta(hours=48)
    scheduler.add_job(
        send_feedback_followup,
        trigger=DateTrigger(run_date=feedback),
        args=[viewing_id],
        id=f"feedback_{viewing_id}",
        replace_existing=True,
    )
    sb.table("follow_ups").insert({
        "lead_id": lead_id,
        "viewing_id": viewing_id,
        "agency_id": agency_id,
        "type": "feedback",
        "scheduled_at": feedback.isoformat(),
        "status": "pending",
    }).execute()

    logger.info(f"Scheduled all jobs for viewing {viewing_id} at {viewing_datetime}")


def cancel_viewing_jobs(viewing_id: str):
    """Remove all scheduled jobs for a cancelled viewing."""
    for prefix in ["remind_24h", "remind_2h", "post_viewing", "feedback"]:
        job_id = f"{prefix}_{viewing_id}"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
    logger.info(f"Cancelled all scheduled jobs for viewing {viewing_id}")

# ─── Recurring Cron Jobs ───────────────────────────────────────────────────────

async def weekly_reengagement_job():
    """Job: runs weekly to re-engage unresponsive leads."""
    try:
        sb = get_supabase()
        seven_days_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
        leads = sb.table("leads").select("*").in_("status", ["new", "qualifying"]).lt("updated_at", seven_days_ago).execute()
        for lead in leads.data:
            name = lead.get("name", "there").split()[0]
            msg = f"Hi {name}! 👋 Andi here. Are you still looking for a property? Let me know if I can help you find something!"
            await send_whatsapp_message(lead.get("phone", ""), msg)
            # Update updated_at so they aren't spammed
            sb.table("leads").update({"updated_at": datetime.utcnow().isoformat()}).eq("id", lead["id"]).execute()
            logger.info(f"Sent weekly re-engagement to lead {lead['id']}")
    except Exception as e:
        logger.error(f"Error in weekly reengagement job: {e}")


async def landlord_weekly_report_job():
    """Job: runs every Friday to send AI-generated reports to landlords."""
    try:
        sb = get_supabase()
        owners = sb.table("owners").select("*").execute()
        for owner in owners.data:
            # Mock data for owner report - in a real app, query viewings and leads for the owner's properties
            report_data = {
                "period_start": (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d"),
                "period_end": datetime.utcnow().strftime("%Y-%m-%d"),
                "leads_generated": 15,
                "viewings_conducted": 4,
                "feedback": ["Great location", "Price slightly above budget"],
            }
            report_msg = await generate_owner_report(report_data)
            await send_whatsapp_message(owner.get("phone", ""), f"📊 *Your Weekly Property Report*\n\n{report_msg}")
            logger.info(f"Sent weekly report to owner {owner['id']}")
    except Exception as e:
        logger.error(f"Error in landlord weekly report job: {e}")


# Register cron jobs
scheduler.add_job(weekly_reengagement_job, CronTrigger(day_of_week='wed', hour=10), id="weekly_reengagement_job", replace_existing=True)
scheduler.add_job(landlord_weekly_report_job, CronTrigger(day_of_week='fri', hour=17), id="landlord_weekly_report_job", replace_existing=True)
