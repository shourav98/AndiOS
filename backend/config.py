"""
Central config — reads from .env file.
"""
from pydantic_settings import BaseSettings
from functools import lru_cache
import os

from dotenv import load_dotenv

load_dotenv(override=True)

# Allow OAuth over HTTP for localhost ONLY in development
if os.getenv("APP_ENV", "development") == "development":
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"


class Settings(BaseSettings):
    # Supabase
    SUPABASE_URL: str
    SUPABASE_ANON_KEY: str
    SUPABASE_SERVICE_ROLE_KEY: str

    # OpenAI
    OPENAI_API_KEY: str
    OPENAI_MODEL: str = "gpt-4o"

    # WhatsApp
    WHATSAPP_PROVIDER: str = "360dialog"
    WHATSAPP_API_KEY: str = ""
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_VERIFY_TOKEN: str = "andios_verify_token"
    META_APP_SECRET: str = ""  # App Secret for Meta Cloud API X-Hub-Signature-256 verification
    # Shared secret required on INBOUND WhatsApp webhooks (header 'X-Webhook-Token'
    # or '?token=' query param). Mandatory in production — requests are rejected
    # without it (fail closed). Configure the callback URL accordingly in 360dialog.
    WHATSAPP_WEBHOOK_TOKEN: str = ""
    # Twilio — Master account credentials (Centralized Shared Gateway model).
    # All agencies share this single Twilio account. Each agency gets a
    # dedicated phone number purchased from this master account and stored
    # in agencies.dedicated_whatsapp_number / dedicated_voice_number.
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    # Default outbound number used when an agency has no dedicated number yet
    TWILIO_WHATSAPP_NUMBER: str = ""

    # ─── Quota Enforcement ────────────────────────────────────────────────────
    # Set to False in development to bypass all quota checks (allow unlimited usage).
    # Always True in production.
    QUOTA_ENFORCEMENT_ENABLED: bool = True

    # Google Calendar
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/connectors/google-calendar/callback"
    GOOGLE_CALENDAR_MODE: str = "shared"
    GOOGLE_SHARED_CALENDAR_ID: str = ""

    # Property Finder
    PROPERTY_FINDER_WEBHOOK_SECRET: str = ""

    # Portal lead webhooks (Bayut / Dubizzle): shared-secret token required as
    # 'X-Webhook-Token' header or '?token=' query param embedded in the
    # callback URL provisioned with each portal's integration team (these
    # portals do not sign deliveries with HMAC in this integration).
    # Mandatory in production — rejected without them (fail closed).
    BAYUT_WEBHOOK_TOKEN: str = ""
    DUBIZZLE_WEBHOOK_TOKEN: str = ""

    # Multi-tenant webhook routing (set in production when multiple agencies exist)
    DEFAULT_AGENCY_ID: str = ""

    # Vapi AI Calling
    VAPI_API_KEY: str = ""
    VAPI_PHONE_NUMBER_ID: str = ""
    VAPI_ASSISTANT_ID: str = ""
    # Shared secret Vapi sends as the 'x-vapi-secret' header on server webhooks
    # (configure as server.secret in the Vapi dashboard). Mandatory in
    # production — inbound Vapi webhooks are rejected without it (fail closed).
    VAPI_WEBHOOK_SECRET: str = ""
    # BYO SIP Trunk configuration for inbound call bridging.
    # Vapi's required URI structure: sip:{phone_number}@{credential_id}.sip.vapi.ai
    # (or .sip.eu.vapi.ai for EU-hosted orgs).
    # The credential_id must be in the subdomain position so Vapi knows which
    # SIP Trunk / organization account the INVITE belongs to.
    # ⚠️  MUST use BYO SIP Trunk mode — NOT Simple Number Import.
    #     Simple Import bypasses our backend webhook; custom SIP headers
    #     (X-Agent-Id, X-Agency-Id) never reach Vapi and agent resolution fails.
    VAPI_SIP_CREDENTIAL_ID: str = ""
    VAPI_SIP_DOMAIN: str = "sip.vapi.ai"
    VAPI_SIP_URI: str = ""  # optional override if full custom SIP endpoint is specified

    # ─── Voice BYON: Central Inbound DID ─────────────────────────────────────
    # The single platform Twilio phone number all agents configure conditional
    # call forwarding to. AndiOS receives the call, resolves the forwarding
    # agent via SIP Diversion header (ForwardedFrom), and bridges to Vapi AI.
    # Only 1 number needed for the entire platform (~$1.15/mo fixed lease;
    # per-minute usage cost applies separately to actual call duration).
    CENTRAL_INBOUND_DID: str = ""

    # Supabase Storage
    SUPABASE_STORAGE_BUCKET: str = "contracts"

    # ─── Stripe Billing ──────────────────────────────────────────────────────
    STRIPE_SECRET_KEY: str = ""             # sk_test_... or sk_live_...
    STRIPE_WEBHOOK_SECRET: str = ""         # whsec_... from stripe CLI or dashboard
    STRIPE_PORTAL_RETURN_URL: str = "http://localhost:3000/owner-dashboard/plan-billing"

    # Stripe Price IDs — create these once in Stripe Dashboard
    STRIPE_PRICE_BASIC: str = ""            # AED 1,400/mo — Basic plan
    STRIPE_PRICE_GROW: str = ""             # AED 2,800/mo — Grow plan
    STRIPE_PRICE_PRO: str = ""              # AED 5,600/mo — Pro plan

    # Add-on call pack price IDs (recurring)
    STRIPE_PRICE_ADDON_P1000: str = ""      # AED 2,000/mo — +1,000 calls
    STRIPE_PRICE_ADDON_P2000: str = ""      # AED 4,000/mo — +2,000 calls
    STRIPE_PRICE_ADDON_P5000: str = ""      # AED 10,000/mo — +5,000 calls
    STRIPE_PRICE_ADDON_P10000: str = ""     # AED 20,000/mo — +10,000 calls

    # Overage price (metered — AED 2.00 per call)
    STRIPE_PRICE_OVERAGE: str = ""          # Metered price for pay-as-you-go overage

    # App
    APP_ENV: str = "development"
    SECRET_KEY: str = "change-me-in-production"
    FRONTEND_URL: str = "http://localhost:3000"
    API_BASE_URL: str = "http://localhost:8000"

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
