"""
Central config — reads from .env file.
"""
from pydantic_settings import BaseSettings
from functools import lru_cache
import os

from dotenv import load_dotenv

load_dotenv(override=True)

# Allow OAuth over HTTP for localhost
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
    # Twilio (optional)
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_WHATSAPP_NUMBER: str = ""

    # Google Calendar
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/connectors/google-calendar/callback"
    GOOGLE_CALENDAR_MODE: str = "shared"
    GOOGLE_SHARED_CALENDAR_ID: str = ""

    # Property Finder
    PROPERTY_FINDER_WEBHOOK_SECRET: str = ""

    # Multi-tenant webhook routing (set in production when multiple agencies exist)
    DEFAULT_AGENCY_ID: str = ""

    # Vapi AI Calling
    VAPI_API_KEY: str = ""
    VAPI_PHONE_NUMBER_ID: str = ""
    VAPI_ASSISTANT_ID: str = ""

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
