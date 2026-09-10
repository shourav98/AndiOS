"""
Communication Layer — Modular provider abstraction for WhatsApp, Voice, and SMS.

Usage:
    from services.communication import get_whatsapp_provider

    provider = get_whatsapp_provider(communication_account)
    await provider.send_message(to_phone, body)
"""
from services.communication.provider_factory import get_whatsapp_provider

__all__ = ["get_whatsapp_provider"]
