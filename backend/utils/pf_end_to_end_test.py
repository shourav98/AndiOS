"""
Property Finder End-to-End Pipeline Diagnostic Test
=====================================================
Tests the complete flow:
  1. Login to AndiOS (get JWT token)
  2. Send test webhook payload to /webhooks/property-finder
  3. Diagnose Meta WhatsApp API access
  4. Test direct WhatsApp message sending

Run: python utils/pf_end_to_end_test.py
"""

import os
import json
import hmac as hmac_mod
import hashlib
import requests
from dotenv import load_dotenv

load_dotenv()

# --- Config ---
BASE_URL    = "https://andreearizan.dmango.online"
PF_SECRET   = os.getenv("PROPERTY_FINDER_WEBHOOK_SECRET", "")
PHONE_ID    = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WA_TOKEN    = os.getenv("WHATSAPP_API_KEY", "")

SEP = "-" * 60

def section(title):
    print(f"\n{SEP}\n  {title}\n{SEP}")

def ok(msg):   print(f"  [OK]   {msg}")
def fail(msg): print(f"  [FAIL] {msg}")
def info(msg): print(f"  [INFO] {msg}")


# --- Step 1: Login ---
section("Step 1: AndiOS Login")

EMAIL    = os.getenv("ANDIOS_TEST_EMAIL", "")
PASSWORD = os.getenv("ANDIOS_TEST_PASSWORD", "")

if not EMAIL or not PASSWORD:
    EMAIL    = input("  AndiOS Admin Email: ").strip()
    PASSWORD = input("  AndiOS Admin Password: ").strip()

login_resp = requests.post(f"{BASE_URL}/auth/login", json={"email": EMAIL, "password": PASSWORD})
if login_resp.status_code == 200:
    rdata = login_resp.json()
    token = (rdata.get("data") or {}).get("access_token") or rdata.get("access_token", "")
    if token:
        ok(f"Login successful. Token: {token[:30]}...")
    else:
        fail("Login OK but no token found!")
        print("  Response:", login_resp.text[:300])
        exit(1)
else:
    fail(f"Login failed: {login_resp.status_code}")
    print("  Response:", login_resp.text[:300])
    exit(1)

AUTH_HEADERS = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# --- Step 2: Property Finder Webhook Test ---
section("Step 2: Property Finder Webhook Pipeline Test")

TEST_PAYLOAD = {
    "lead": {
        "id": "PF-DIAG-001",
        "name": "Ahmed Diagnostic Lead",
        "phone": "+971501234599",
        "email": "ahmed.diag@example.com",
        "property_ref": "PF-DIAG-PROP-001",
        "property_title": "2BR Apartment Dubai Marina",
        "bedrooms": 2,
        "budget": 120000,
        "community": "Dubai Marina",
        "agent_email": EMAIL,
    }
}

payload_bytes = json.dumps(TEST_PAYLOAD, separators=(",", ":")).encode("utf-8")

if PF_SECRET:
    sig = "sha256=" + hmac_mod.new(PF_SECRET.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
    wh_headers = {"X-Hub-Signature-256": sig, "Content-Type": "application/json"}
    info(f"HMAC signature: {sig[:40]}...")
else:
    wh_headers = {"Content-Type": "application/json"}
    info("No PF secret — sending without HMAC (dev mode)")

webhook_resp = requests.post(
    f"{BASE_URL}/webhooks/property-finder",
    data=payload_bytes,
    headers=wh_headers,
)

print(f"\n  HTTP Status: {webhook_resp.status_code}")
try:
    wh_json = webhook_resp.json()
    print(f"  Response:\n{json.dumps(wh_json, indent=4)}")
    if webhook_resp.status_code == 200 and wh_json.get("success"):
        lead_id = (wh_json.get("data") or {}).get("lead_id", "")
        ok(f"Webhook pipeline OK! Lead created: {lead_id}")
        ok("Lead saved to DB + AI greeting triggered via WhatsApp!")
    elif (wh_json.get("data") or {}).get("status") == "duplicate":
        info("Duplicate lead — already processed before. This is normal.")
        info("Change phone number in TEST_PAYLOAD to test again.")
    else:
        fail(f"Webhook error: {wh_json.get('message', 'unknown error')}")
except Exception as exc:
    fail(f"Could not parse response: {exc}")
    print("  Raw:", webhook_resp.text[:500])


# --- Step 3: Meta WhatsApp API Check ---
section("Step 3: Meta WhatsApp Business API Diagnosis")

if not PHONE_ID or not WA_TOKEN:
    fail("WHATSAPP_PHONE_NUMBER_ID or WHATSAPP_API_KEY missing from .env")
else:
    meta_url = f"https://graph.facebook.com/v19.0/{PHONE_ID}?fields=id,display_phone_number,verified_name,quality_rating,code_verification_status,status"
    meta_resp = requests.get(meta_url, headers={"Authorization": f"Bearer {WA_TOKEN}"})
    print(f"\n  Meta Graph API Status: {meta_resp.status_code}")
    if meta_resp.status_code == 200:
        d = meta_resp.json()
        ok(f"Phone    : {d.get('display_phone_number', 'N/A')}")
        ok(f"Name     : {d.get('verified_name', 'N/A')}")
        ok(f"Quality  : {d.get('quality_rating', 'N/A')}")
        ok(f"Verified : {d.get('code_verification_status', 'N/A')}")
        status = d.get("status", "")
        if status == "CONNECTED":
            ok("Status   : CONNECTED (Active)")
        elif status:
            fail(f"Status   : {status} (Expected: CONNECTED)")
        else:
            info("Status field not in response — likely still active")
    else:
        fail(f"Meta API error: {meta_resp.status_code}")
        try:
            err = meta_resp.json().get("error", {})
            fail(f"Code: {err.get('code')} — {err.get('message', '')}")
            code = err.get("code", 0)
            if code in (190, 401):
                fail(">>> ACTION NEEDED: Token expired! Renew WHATSAPP_API_KEY in .env from Meta Business Manager")
            elif code == 100:
                fail(">>> Invalid Phone Number ID. Check WHATSAPP_PHONE_NUMBER_ID in .env")
        except Exception:
            print("  Raw:", meta_resp.text[:300])


# --- Step 4: Direct WhatsApp Send Test ---
section("Step 4: Direct WhatsApp Message Send Test")

test_number = input("\n  Enter YOUR WhatsApp number (with country code, no +, e.g. 8801711XXXXXX): ").strip()

if test_number and PHONE_ID and WA_TOKEN:
    wa_url = f"https://graph.facebook.com/v19.0/{PHONE_ID}/messages"
    wa_body = {
        "messaging_product": "whatsapp",
        "to": test_number.lstrip("+"),
        "type": "text",
        "text": {
            "body": (
                "AndiOS Test Message\n\n"
                "This confirms WhatsApp integration is working. "
                "When a Property Finder lead arrives, Andi AI will send a greeting like this automatically!"
            )
        }
    }
    wa_resp = requests.post(
        wa_url,
        headers={"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"},
        json=wa_body,
    )
    print(f"\n  WhatsApp Send Status: {wa_resp.status_code}")
    if wa_resp.status_code == 200:
        msg_id = (wa_resp.json().get("messages") or [{}])[0].get("id", "")
        ok(f"Message sent! ID: {msg_id}")
        ok("Check your WhatsApp now!")
    else:
        try:
            err = wa_resp.json().get("error", {})
            code = err.get("code", 0)
            fail(f"Send failed — Error {code}: {err.get('message', '')}")
            fbtitle = err.get("error_data", {}).get("details", "")
            if fbtitle:
                fail(f"Details: {fbtitle}")

            # Diagnose common errors
            if code == 131030:
                fail(">>> Number not in allowed recipient list (test mode restriction)")
                fail(">>> Fix: Add the number in Meta Business Manager > WhatsApp > Test Numbers")
            elif code == 131047:
                fail(">>> 24-hour messaging window closed (recipient never messaged you first)")
                fail(">>> Fix: Use approved Template message instead of free-form text")
                fail(">>> The AI greeting uses 'andios_lead_first_contact' template — ensure it's APPROVED in Meta")
            elif code == 190:
                fail(">>> Token expired! Update WHATSAPP_API_KEY in .env")
            elif code == 368:
                fail(">>> Account restricted/blocked. Check Meta Business Manager for violations")
            elif code == 100:
                fail(">>> Invalid parameter — check phone number format")
        except Exception:
            print("  Raw:", wa_resp.text[:500])
else:
    info("Skipped (no number entered or missing credentials)")


# --- Summary ---
section("SUMMARY")
print("""
  Interpretation Guide:
  --------------------
  Step 2 OK   -> Webhook pipeline works! Leads will be captured from PF.
  Step 3 OK   -> Meta API token is valid. WhatsApp account is connected.
  Step 4 OK   -> Full end-to-end works! AI greetings will be sent.
  
  Step 4 FAIL -> Check the error code:
    131030  = Add test number to Meta allowed list (sandbox limitation)
    131047  = Need APPROVED WhatsApp Template (for new contacts)
    190     = API token expired -> renew in Meta Business Manager
    368     = Account blocked -> check Meta Business Manager
""")
