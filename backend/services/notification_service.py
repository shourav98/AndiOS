"""
Notification Service — Dispatches status notifications to agencies and administrators
for phone number provisioning and WhatsApp approval lifecycle.
"""
import logging
from typing import Optional, Dict, Any
from database.supabase_client import get_supabase

logger = logging.getLogger(__name__)

STATUS_MESSAGES = {
    "provisioned": "Your number is being set up, WhatsApp approval usually takes 24–48 hours.",
    "active": "Your WhatsApp number is now live and ready to use.",
    "failed": "There was an issue setting up your number, our team has been notified.",
}


async def notify_number_status_change(
    agency_id: str,
    status: str,
    number: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Sends/logs status transition notifications for an agency.
    Updates in-app banner text and logs to notification audit trail.
    """
    message = STATUS_MESSAGES.get(status, f"WhatsApp status updated to: {status}")
    logger.info(
        f"[Notification] Agency {agency_id} WhatsApp status transition -> '{status}'. Message: \"{message}\" (Number: {number})"
    )

    sb = get_supabase()
    notification_record = {
        "agency_id": agency_id,
        "type": "whatsapp_number_status",
        "status": status,
        "phone_number": number,
        "message": message,
        "metadata": metadata or {},
    }

    try:
        # Attempt recording to notifications table if available
        sb.table("notifications").insert(notification_record).execute()
    except Exception as e:
        logger.debug(f"[Notification] Note: notifications table insert skipped or unavailable: {e}")

    return {
        "agency_id": agency_id,
        "status": status,
        "number": number,
        "message": message,
    }


async def notify_admin_stale_approval(
    agency_id: str,
    agency_name: str,
    number: str,
    hours_pending: float,
) -> Dict[str, Any]:
    """
    Sends an urgent alert to administrators when a provisioned WhatsApp number
    has been waiting for Meta approval for more than 48 hours.
    """
    alert_msg = (
        f"Agency {agency_name} ({agency_id}) WhatsApp number {number} "
        f"approval pending in Meta for > {int(hours_pending)} hours."
    )
    logger.warning(f"[Admin Alert] {alert_msg}")

    sb = get_supabase()
    try:
        sb.table("notifications").insert({
            "agency_id": agency_id,
            "type": "admin_stale_meta_approval",
            "message": alert_msg,
            "metadata": {
                "hours_pending": hours_pending,
                "phone_number": number,
                "agency_name": agency_name,
            },
        }).execute()
    except Exception as e:
        logger.debug(f"[Notification] Note: admin notification insert: {e}")

    return {
        "alert": True,
        "message": alert_msg,
        "hours_pending": hours_pending,
    }


async def notify_admin_provisioning_failure(
    agency_id: str,
    agency_name: str,
    error_detail: str,
    country_code: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Alerts admins when number provisioning fails (e.g. Twilio inventory empty or API error).
    """
    alert_msg = (
        f"Automated number provisioning failed for agency {agency_name} ({agency_id}) "
        f"in market '{country_code}': {error_detail}"
    )
    logger.error(f"[Admin Alert] {alert_msg}")

    sb = get_supabase()
    try:
        sb.table("notifications").insert({
            "agency_id": agency_id,
            "type": "admin_provisioning_failure",
            "message": alert_msg,
            "metadata": {
                "agency_id": agency_id,
                "country_code": country_code,
                "error": error_detail,
            },
        }).execute()
    except Exception as e:
        logger.debug(f"[Notification] Note: admin notification insert: {e}")

    return {
        "alert": True,
        "message": alert_msg,
        "error": error_detail,
    }
