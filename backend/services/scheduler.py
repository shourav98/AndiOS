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
        viewing = sb.table("viewings").select("*, leads(id, name, phone, last_inbound_at, assigned_agent_id)").eq("id", viewing_id).single().execute()
        v = viewing.data
        if not v or v["status"] in ("cancelled", "completed"):
            return
        lead = v.get("leads", {})
        agency_id = v.get("agency_id") or (lead.get("agency_id") if isinstance(lead, dict) else None)
        if not agency_id:
            logger.warning("[Scheduler] Skipping 24h reminder for viewing %s: missing agency_id", viewing_id)
            return
        lead_id = v.get("lead_id") or (lead.get("id") if isinstance(lead, dict) else None)
        if not lead_id:
            logger.warning("[Scheduler] Skipping 24h reminder for viewing %s: missing lead_id", viewing_id)
            return
        msg = _reminder_24h_msg(
            lead.get("name", "there") if isinstance(lead, dict) else "there",
            v["property_address"],
            datetime.fromisoformat(v["viewing_datetime"]),
        )
        recipient_phone = lead.get("phone", "") if isinstance(lead, dict) else ""
        last_inbound_at = lead.get("last_inbound_at") if isinstance(lead, dict) else None
        agent_id = lead.get("assigned_agent_id") if isinstance(lead, dict) else None
        from services.whatsapp_service import send_whatsapp_smart
        await send_whatsapp_smart(
            agency_id, lead_id, recipient_phone, msg,
            agent_id=agent_id,
            template_name="andios_viewing_reminder",
            last_inbound_at=last_inbound_at,
        )
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
        viewing = sb.table("viewings").select("*, leads(id, name, phone, last_inbound_at, assigned_agent_id)").eq("id", viewing_id).single().execute()
        v = viewing.data
        if not v or v["status"] in ("cancelled", "completed"):
            return
        lead = v.get("leads", {})
        agency_id = v.get("agency_id") or (lead.get("agency_id") if isinstance(lead, dict) else None)
        if not agency_id:
            logger.warning("[Scheduler] Skipping 2h reminder for viewing %s: missing agency_id", viewing_id)
            return
        lead_id = v.get("lead_id") or (lead.get("id") if isinstance(lead, dict) else None)
        if not lead_id:
            logger.warning("[Scheduler] Skipping 2h reminder for viewing %s: missing lead_id", viewing_id)
            return
        msg = _reminder_2h_msg(
            lead.get("name", "there") if isinstance(lead, dict) else "there",
            v["property_address"],
            datetime.fromisoformat(v["viewing_datetime"]),
        )
        recipient_phone = lead.get("phone", "") if isinstance(lead, dict) else ""
        last_inbound_at = lead.get("last_inbound_at") if isinstance(lead, dict) else None
        agent_id = lead.get("assigned_agent_id") if isinstance(lead, dict) else None
        from services.whatsapp_service import send_whatsapp_smart
        await send_whatsapp_smart(
            agency_id, lead_id, recipient_phone, msg,
            agent_id=agent_id,
            template_name="andios_viewing_reminder",
            last_inbound_at=last_inbound_at,
        )
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
        viewing = sb.table("viewings").select("*, leads(id, name, phone, last_inbound_at, assigned_agent_id)").eq("id", viewing_id).single().execute()
        v = viewing.data
        if not v:
            return
        lead = v.get("leads", {})
        agency_id = v.get("agency_id") or (lead.get("agency_id") if isinstance(lead, dict) else None)
        if not agency_id:
            logger.warning("[Scheduler] Skipping post-viewing follow-up for viewing %s: missing agency_id", viewing_id)
            return
        lead_id = v.get("lead_id") or (lead.get("id") if isinstance(lead, dict) else None)
        if not lead_id:
            logger.warning("[Scheduler] Skipping post-viewing follow-up for viewing %s: missing lead_id", viewing_id)
            return
        msg = _post_viewing_msg(lead.get("name", "there") if isinstance(lead, dict) else "there", v["property_address"])
        recipient_phone = lead.get("phone", "") if isinstance(lead, dict) else ""
        last_inbound_at = lead.get("last_inbound_at") if isinstance(lead, dict) else None
        agent_id = lead.get("assigned_agent_id") if isinstance(lead, dict) else None
        from services.whatsapp_service import send_whatsapp_smart
        await send_whatsapp_smart(
            agency_id, lead_id, recipient_phone, msg,
            agent_id=agent_id,
            template_name="andios_viewing_followup",
            last_inbound_at=last_inbound_at,
        )
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
        viewing = sb.table("viewings").select("*, leads(id, name, phone, last_inbound_at, assigned_agent_id)").eq("id", viewing_id).single().execute()
        v = viewing.data
        if not v:
            return
        lead = v.get("leads", {})
        # Only send if no feedback received yet
        if v.get("feedback_received"):
            return
        agency_id = v.get("agency_id") or (lead.get("agency_id") if isinstance(lead, dict) else None)
        if not agency_id:
            logger.warning("[Scheduler] Skipping feedback follow-up for viewing %s: missing agency_id", viewing_id)
            return
        lead_id = v.get("lead_id") or (lead.get("id") if isinstance(lead, dict) else None)
        if not lead_id:
            logger.warning("[Scheduler] Skipping feedback follow-up for viewing %s: missing lead_id", viewing_id)
            return
        msg = _feedback_followup_msg(lead.get("name", "there") if isinstance(lead, dict) else "there")
        recipient_phone = lead.get("phone", "") if isinstance(lead, dict) else ""
        last_inbound_at = lead.get("last_inbound_at") if isinstance(lead, dict) else None
        agent_id = lead.get("assigned_agent_id") if isinstance(lead, dict) else None
        from services.whatsapp_service import send_whatsapp_smart
        await send_whatsapp_smart(
            agency_id, lead_id, recipient_phone, msg,
            agent_id=agent_id,
            template_name="andios_viewing_followup",
            last_inbound_at=last_inbound_at,
        )
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
    now = datetime.now(viewing_datetime.tzinfo) if viewing_datetime.tzinfo else datetime.utcnow()

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
            agency_id = lead.get("agency_id")
            if not agency_id:
                logger.warning("[Scheduler] Skipping weekly re-engagement for lead %s: missing agency_id", lead.get("id"))
                continue
            lead_id = lead.get("id")
            name = lead.get("name", "there").split()[0]
            msg = f"Hi {name}! 👋 Andi here. Are you still looking for a property? Let me know if I can help you find something!"
            from services.whatsapp_service import send_whatsapp_smart
            await send_whatsapp_smart(
                agency_id, lead_id, lead.get("phone", ""), msg,
                agent_id=lead.get("assigned_agent_id"),
                template_name="andios_reengagement",
                last_inbound_at=lead.get("last_inbound_at"),
            )
            # Update updated_at so they aren't spammed
            sb.table("leads").update({"updated_at": datetime.utcnow().isoformat()}).eq("id", lead["id"]).execute()
            logger.info(f"Sent weekly re-engagement to lead {lead['id']}")
    except Exception as e:
        logger.error(f"Error in weekly reengagement job: {e}")


async def landlord_weekly_report_job():
    """Job: runs every Friday to send AI-generated reports to landlords with real data."""
    try:
        sb = get_supabase()
        seven_days_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
        now = datetime.utcnow()

        owners = sb.table("owners").select("*").execute()
        for owner in owners.data:
            owner_id = owner["id"]
            agency_id = owner.get("agency_id")
            property_group = owner.get("property_group", "")

            # Skip owners without agency
            if not agency_id:
                logger.warning("[Scheduler] Skipping weekly report for owner %s: missing agency_id", owner_id)
                continue

            # Query real leads for this owner's property group
            leads_result = (
                sb.table("leads")
                .select("id, status, source, created_at")
                .eq("agency_id", agency_id)
                .gte("created_at", seven_days_ago)
                .execute()
            )
            # Filter leads relevant to this owner's property group (by property_ref or location)
            leads = leads_result.data if leads_result.data else []

            # Query real viewings for this period
            viewings_result = (
                sb.table("viewings")
                .select("id, status, property_address, viewing_datetime, feedback_received")
                .eq("agency_id", agency_id)
                .gte("viewing_datetime", seven_days_ago)
                .execute()
            )
            viewings = viewings_result.data if viewings_result.data else []

            # Filter viewings matching owner's property group
            relevant_viewings = []
            for v in viewings:
                addr = (v.get("property_address") or "").lower()
                if property_group and property_group.lower() in addr:
                    relevant_viewings.append(v)
            # If no property_group match, use all viewings (small agency)
            if not relevant_viewings:
                relevant_viewings = viewings

            # Aggregate feedback
            feedback_list = []
            for v in relevant_viewings:
                if v.get("feedback_received"):
                    feedback_list.append(str(v["feedback_received"]))

            completed_viewings = sum(1 for v in relevant_viewings if v.get("status") == "completed")
            cancelled_viewings = sum(1 for v in relevant_viewings if v.get("status") == "cancelled")

            report_data = {
                "period_start": (now - timedelta(days=7)).strftime("%Y-%m-%d"),
                "period_end": now.strftime("%Y-%m-%d"),
                "owner_name": owner.get("name", "Owner"),
                "property_group": property_group,
                "leads_generated": len(leads),
                "leads_by_source": {},
                "viewings_scheduled": len(relevant_viewings),
                "viewings_completed": completed_viewings,
                "viewings_cancelled": cancelled_viewings,
                "feedback": feedback_list[:5],  # limit to 5 most recent
            }

            # Count leads by source
            for lead in leads:
                src = lead.get("source", "unknown")
                report_data["leads_by_source"][src] = report_data["leads_by_source"].get(src, 0) + 1

            # Generate AI report
            report_msg = await generate_owner_report(report_data)
            phone = owner.get("phone", "")
            if phone:
                from services.whatsapp_service import send_whatsapp_for_agency
                await send_whatsapp_for_agency(agency_id, phone, f"📊 *Your Weekly Property Report*\n\n{report_msg}")
                logger.info(f"Sent weekly report to owner {owner_id} ({owner.get('name')})")
    except Exception as e:
        logger.error(f"Error in landlord weekly report job: {e}")


# Register cron jobs
scheduler.add_job(weekly_reengagement_job, CronTrigger(day_of_week='wed', hour=10), id="weekly_reengagement_job", replace_existing=True)
scheduler.add_job(landlord_weekly_report_job, CronTrigger(day_of_week='fri', hour=17), id="landlord_weekly_report_job", replace_existing=True)


# ─── WhatsApp Number Provisioning Lifecycle Jobs ───────────────────────────────

async def poll_provisioned_whatsapp_senders_job():
    """
    Runs every 15 minutes.
    Checks all agencies in 'provisioned' status to see if Meta/Twilio have approved
    their WhatsApp Sender. Transitions approved senders to 'active'.
    Also fires a stale-approval alert for agencies pending > 48 hours.
    """
    from config import settings
    if not getattr(settings, "ENABLE_TWILIO_PROVISIONING", False):
        return

    try:
        from services.provisioning_service import check_stale_provisioned_agencies, check_twilio_sender_status
        from database.supabase_client import get_supabase

        sb = get_supabase()
        provisioned = sb.table("agencies").select(
            "id, dedicated_whatsapp_number, whatsapp_number_status"
        ).eq("whatsapp_number_status", "provisioned").execute()

        if not provisioned or not provisioned.data:
            return

        for agency in provisioned.data:
            try:
                await check_twilio_sender_status(
                    agency["id"],
                    agency.get("dedicated_whatsapp_number"),
                )
            except Exception as e:
                logger.error(f"[Scheduler] Error polling sender for agency {agency['id']}: {e}")

        # Fire stale alert for any agency > 48h without approval
        await check_stale_provisioned_agencies(hours_threshold=48.0)
    except Exception as e:
        logger.error(f"[Scheduler] Error in poll_provisioned_whatsapp_senders_job: {e}")


async def recover_stuck_provisioning_job():
    """
    Runs every 15 minutes.
    Crash-recovery sweep: finds agencies stuck in 'provisioning' state > 15 minutes
    without a purchased number (indicates a crashed backend mid-Twilio call)
    and marks them 'failed' so admins can retry.
    """
    from config import settings
    if not getattr(settings, "ENABLE_TWILIO_PROVISIONING", False):
        return

    try:
        from services.provisioning_service import recover_stuck_provisioning_agencies
        await recover_stuck_provisioning_agencies(minutes_threshold=15.0)
    except Exception as e:
        logger.error(f"[Scheduler] Error in recover_stuck_provisioning_job: {e}")


async def retry_waba_webhook_subscriptions_job():
    """
    Runs every 15 minutes.
    Sweeps communication_accounts where provider='meta' and
    meta_onboarding_state='webhook_subscription_failed'.
    Attempts to re-subscribe the app to the WABA's webhooks.
    Transitions meta_onboarding_state to 'embedded_signup' upon success.
    """
    from config import settings
    if not getattr(settings, "ENABLE_META_WHATSAPP", True):
        return

    try:
        from utils.crypto import decrypt_token
        from routers.connectors import _subscribe_app_to_waba
        import uuid

        sb = get_supabase()
        res = (
            sb.table("communication_accounts")
            .select("id, external_account_id, access_token_enc")
            .eq("provider", "meta")
            .eq("meta_onboarding_state", "webhook_subscription_failed")
            .neq("status", "disconnected")
            .execute()
        )
        accounts = res.data or []
        for acc in accounts:
            account_id = acc.get("id")
            waba_id = acc.get("external_account_id")
            raw_token = acc.get("access_token_enc") or acc.get("access_token")
            if not waba_id or not raw_token:
                continue
            try:
                token = decrypt_token(raw_token, account_id=account_id)
                corr_id = f"sched-{uuid.uuid4().hex[:8]}"
                sub_ok = await _subscribe_app_to_waba(waba_id, token, corr_id)
                if sub_ok:
                    sb.table("communication_accounts").update({
                        "meta_onboarding_state": "embedded_signup"
                    }).eq("id", account_id).execute()
                    logger.info("[Scheduler] Successfully recovered webhook subscription for account %s (WABA %s)", account_id, waba_id)
            except Exception as sub_err:
                logger.warning("[Scheduler] Webhook subscription retry failed for account %s: %s", account_id, sub_err)
    except Exception as e:
        logger.error(f"[Scheduler] Error in retry_waba_webhook_subscriptions_job: {e}")


async def purge_old_whatsapp_processed_messages_job():
    """
    Periodic maintenance job: Purge idempotency records older than 72 hours
    from whatsapp_processed_messages to keep the table compact.
    """
    try:
        from datetime import timezone
        sb = get_supabase()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
        res = (
            sb.table("whatsapp_processed_messages")
            .delete()
            .lt("processed_at", cutoff)
            .execute()
        )
        count = len(res.data) if res and res.data else 0
        logger.info("[Scheduler] Purged %d old whatsapp_processed_messages records (older than 72h)", count)
    except Exception as e:
        logger.error(f"[Scheduler] Error purging old whatsapp_processed_messages: {e}")


# Register provisioning lifecycle jobs
scheduler.add_job(
    poll_provisioned_whatsapp_senders_job,
    CronTrigger(minute="*/15"),
    id="poll_provisioned_whatsapp_senders_job",
    replace_existing=True,
)
scheduler.add_job(
    recover_stuck_provisioning_job,
    CronTrigger(minute="*/15"),
    id="recover_stuck_provisioning_job",
    replace_existing=True,
)
scheduler.add_job(
    retry_waba_webhook_subscriptions_job,
    CronTrigger(minute="*/15"),
    id="retry_waba_webhook_subscriptions_job",
    replace_existing=True,
)
scheduler.add_job(
    purge_old_whatsapp_processed_messages_job,
    CronTrigger(hour=3, minute=0),
    id="purge_old_whatsapp_processed_messages_job",
    replace_existing=True,
)

