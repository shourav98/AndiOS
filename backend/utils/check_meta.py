import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
token = os.getenv("WHATSAPP_API_KEY")
waba_id = os.getenv("WhatsApp_Business_account_ID")

print("=== META WHATSAPP CONFIGURATION CHECK ===")
print("Phone ID:", phone_id)
print("WABA ID :", waba_id)

headers = {"Authorization": f"Bearer {token}"}

# 1. Phone number status
url = f"https://graph.facebook.com/v19.0/{phone_id}?fields=id,display_phone_number,verified_name,quality_rating,code_verification_status,status,account_mode"
r = requests.get(url, headers=headers)
print("\n[1] Phone Number Details:")
print("HTTP Status:", r.status_code)
try:
    phone_data = r.json()
    print(json.dumps(phone_data, indent=2))
except Exception as e:
    print(r.text)

# 2. Token debug / App info
debug_url = f"https://graph.facebook.com/debug_token?input_token={token}&access_token={token}"
dr = requests.get(debug_url)
print("\n[2] Token Debug Info:")
print("HTTP Status:", dr.status_code)
try:
    debug_data = dr.json()
    print(json.dumps(debug_data, indent=2))
except Exception as e:
    print(dr.text)

# 3. Message Templates
if waba_id:
    t_url = f"https://graph.facebook.com/v19.0/{waba_id}/message_templates?limit=20"
    tr = requests.get(t_url, headers=headers)
    print("\n[3] Message Templates:")
    print("HTTP Status:", tr.status_code)
    try:
        t_json = tr.json()
        templates = t_json.get("data", [])
        print(f"Total templates found: {len(templates)}")
        for t in templates:
            print(f" - Name: {t.get('name')}, Status: {t.get('status')}, Category: {t.get('category')}, Lang: {t.get('language')}")
        if not templates and "error" in t_json:
            print("Error fetching templates:", t_json["error"])
    except Exception as e:
        print(tr.text)
