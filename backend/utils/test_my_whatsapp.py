import asyncio
import os
import sys
from dotenv import load_dotenv

load_dotenv()
from services.whatsapp_service import send_whatsapp_smart

async def main():
    print("=" * 60)
    print("   AndiOS Real WhatsApp Delivery Test")
    print("=" * 60)
    
    agency_id = os.getenv("DEFAULT_AGENCY_ID", "d8798ea7-1b47-40be-ba3e-8e9593871393")
    agent_id = "3b3be5f4-3147-4466-b785-6de51f777bc5"
    
    print("\nSender Number: +880 1570-283967 (Your Connected Meta Phone)")
    print("NOTE: Do NOT enter the sender number itself (Meta rejects self-messaging).")
    
    user_phone = input("\nEnter YOUR WhatsApp number with country code (e.g. 88017XXXXXXXX or 9715XXXXXXXX): ").strip()
    user_phone = user_phone.lstrip("+").replace(" ", "").replace("-", "")
    
    if not user_phone:
        print("No phone number entered. Exiting.")
        return
        
    client_name = input("Enter your name (e.g. Shourav): ").strip() or "Shourav"
    property_title = input("Enter property name (or press Enter for default 'Luxury 2BR Dubai Marina'): ").strip() or "Luxury 2BR Dubai Marina"
    
    print(f"\nSending approved template 'andios_lead_first_contact' to +{user_phone}...")
    
    dummy_body = f"Hello {client_name}! Thank you for your inquiry about {property_title} on Property Finder."
    
    result = await send_whatsapp_smart(
        agency_id=agency_id,
        lead_id="test-real-verification",
        to_phone=user_phone,
        body=dummy_body,
        agent_id=agent_id,
        template_name="andios_lead_first_contact",
        template_params=[client_name, property_title],
        last_inbound_at=None  # Forces Meta utility template
    )
    
    print("\n" + "-" * 60)
    if result.get("status") == "sent":
        print(f" SUCCESS! Message sent to your WhatsApp! (ID: {result.get('sid')})")
        print(" Check your phone now — you will see the message from Andi AI!")
    else:
        print(f" Delivery status: {result}")
    print("-" * 60)

if __name__ == "__main__":
    asyncio.run(main())
