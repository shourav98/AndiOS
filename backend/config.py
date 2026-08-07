"""
Central config — reads from .env file.
"""
from pydantic_settings import BaseSettings
from functools import lru_cache
import os

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
