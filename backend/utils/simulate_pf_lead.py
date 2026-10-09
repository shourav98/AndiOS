import asyncio
import os
import time
from dotenv import load_dotenv

load_dotenv()
from database.supabase_client import get_supabase
from services.whatsapp_service import send_whatsapp_smart

async def simulate_lead():
    print("=" * 65)
    print("   Property Finder Complete End-to-End Lead Simulation")
    print("=" * 65)
    
    sb = get_supabase()
    agency_id = os.getenv("DEFAULT_AGENCY_ID", "d8798ea7-1b47-40be-ba3e-8e9593871393")
    agent_id = "3b3be5f4-3147-4466-b785-6de51f777bc5"
    
    phone = input("\nEnter recipient WhatsApp number (with country code, e.g. 88017XXXXXXXX): ").strip()
    phone = phone.lstrip("+").replace(" ", "").replace("-", "")
    
    if not phone:
        print("Cancelled.")
        return
        
    name = input("Enter lead name (e.g. Shourav): ").strip() or "Shourav"
    prop_title = input("Enter property title (e.g. 2BR Apartment Downtown): ").strip() or "2BR Apartment Downtown"
    
    ts = int(time.time())
    lead_data = {
        "external_lead_id": f"PF-SIM-{ts}",
        "name": name,
        "phone": f"+{phone}",
        "email": f"lead_{ts}@example.com",
        "source": "property_finder",
        "property_ref": f"PROP-{ts}",
        "property_address": prop_title,
        "bedrooms": 2,
        "budget_max": 180000.0,
        "currency": "AED",
        "location_pref": "Downtown Dubai",
        "status": "new",
        "ai_stage": "greeting",
        "assigned_agent_id": agent_id,
        "is_ai_handling": True,
        "agency_id": agency_id,
        "last_inbound_at": None
    }
    
    print("\n[1/3] Inserting lead into Supabase database...")
    res = sb.table("leads").insert(lead_data).execute()
    lead_id = res.data[0]["id"]
    print(f"       Lead saved! ID: {lead_id}")
    
    print(f"[2/3] Triggering Andi AI WhatsApp first contact via Meta...")
    greeting = f"Hello {name}! 👋 Thank you for your inquiry about {prop_title} on Property Finder."
    
    send_res = await send_whatsapp_smart(
        agency_id=agency_id,
        lead_id=lead_id,
        to_phone=phone,
        body=greeting,
        agent_id=agent_id,
        template_name="andios_lead_first_contact",
        template_params=[name, prop_title],
        last_inbound_at=None
    )
    
    print(f"[3/3] Result: {send_res}")
    
    # Save conversation log
    sb.table("conversations").insert({
        "lead_id": lead_id,
        "agency_id": agency_id,
        "direction": "outbound",
        "channel": "whatsapp",
        "message_body": greeting,
        "sender_type": "ai",
        "delivery_status": "sent" if send_res.get("status") == "sent" else "pending"
    }).execute()
    
    print("\n" + "=" * 65)
    if send_res.get("status") == "sent":
        print(" 🎉 FULL PIPELINE SUCCESSFUL!")
        print(f" Message ID: {send_res.get('sid')}")
        print(" Check your WhatsApp now — Andi AI's greeting has arrived!")
    else:
        print(f" Status: {send_res}")
    print("=" * 65)

if __name__ == "__main__":
    asyncio.run(simulate_lead())
