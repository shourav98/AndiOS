"""
Property Finder Enterprise API 2.0 - Webhook Registration (CORRECTED)
======================================================================
API response told us: field name is 'eventId' (not 'eventType')
"""

import base64
import json
import httpx

API_KEY = "oYgqX.1lxQ5OIJZCQ2vbwAul5zQcKmiN1nSc0rLB"
API_SECRET = "ZOTnuAyUuSkQ9uyC86fLxUgsjQh785Zd"
BACKEND_WEBHOOK_URL = "https://andreearizan.dmango.online/webhooks/property-finder"
PF_AUTH_URL = "https://auth.propertyfinder.com/auth/oauth/v1/token"
PF_API_BASE = "https://atlas.propertyfinder.com"


def get_token():
    credentials = f"{API_KEY}:{API_SECRET}"
    encoded = base64.b64encode(credentials.encode()).decode()
    r = httpx.post(
        PF_AUTH_URL,
        headers={"Authorization": f"Basic {encoded}", "Content-Type": "application/json"},
        json={"grant_type": "client_credentials", "scope": "openid"},
        timeout=15.0,
    )
    token = r.json()["access_token"]
    print("Token obtained OK.")
    return token


def discover_event_ids(token):
    """Try to find valid eventId values by checking events/schema endpoint."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    print("\n" + "="*55)
    print("STEP A: Discovering valid eventId values...")
    print("="*55)

    # Try to get list of available events
    event_endpoints = [
        f"{PF_API_BASE}/v1/webhooks/events",
        f"{PF_API_BASE}/v1/events",
        f"{PF_API_BASE}/v1/webhook-events",
        f"{PF_API_BASE}/v1/leads",
        f"{PF_API_BASE}/v1/leads/events",
    ]
    with httpx.Client(timeout=15.0) as client:
        for ep in event_endpoints:
            r = client.get(ep, headers=headers)
            if r.status_code != 404:
                print(f"GET {ep} -> {r.status_code}: {r.text[:300]}")


def register_webhook_corrected(token):
    """Register webhook using correct field name 'eventId'."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    print("\n" + "="*55)
    print("STEP B: Registering Webhook with correct 'eventId' field...")
    print("="*55)

    # Try different possible eventId values for "new lead" events
    event_ids_to_try = [
        "leads.create",
        "leads.created",
        "lead.created",
        "lead.create",
        "lead.new",
        "new_lead",
        "inbound_lead",
        "leads",
    ]

    with httpx.Client(timeout=15.0) as client:
        for event_id in event_ids_to_try:
            payload = {
                "eventId": event_id,
                "callbackUrl": BACKEND_WEBHOOK_URL,
            }
            r = client.post(
                f"{PF_API_BASE}/v1/webhooks",
                headers=headers,
                json=payload,
            )
            print(f"\n  POST /v1/webhooks with eventId='{event_id}'")
            print(f"  Status: {r.status_code}")
            print(f"  Body:   {r.text[:300]}")
            if r.status_code in (200, 201):
                print("  >>> WEBHOOK REGISTERED SUCCESSFULLY! <<<")
                return True
            elif r.status_code == 409:
                print("  >>> ALREADY REGISTERED (409) - webhook is live! <<<")
                return True
            elif r.status_code == 400:
                # Keep trying - the eventId value might be wrong
                pass

    return False


def list_current_webhooks(token):
    """List all currently registered webhooks."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    print("\n" + "="*55)
    print("STEP C: Current registered webhooks...")
    print("="*55)
    with httpx.Client(timeout=15.0) as client:
        r = client.get(f"{PF_API_BASE}/v1/webhooks", headers=headers)
        data = r.json()
        webhooks = data.get("data", data)
        if isinstance(webhooks, list) and len(webhooks) == 0:
            print("  No webhooks registered yet.")
        else:
            print(f"  Registered webhooks: {json.dumps(webhooks, indent=2)}")


if __name__ == "__main__":
    print("Property Finder Enterprise API 2.0 - Webhook Registration")
    token = get_token()
    list_current_webhooks(token)
    discover_event_ids(token)
    success = register_webhook_corrected(token)
    print("\n")
    list_current_webhooks(token)
    if success:
        print("\nDone! Webhook is now registered and live.")
    else:
        print("\nWebhook not registered yet - eventId value not found.")
        print("Ask PF support: 'What is the eventId for new lead notifications?'")
