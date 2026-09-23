"""
Central config — reads from .env file.
"""
from pydantic_settings import BaseSettings
from functools import lru_cache
import os

from dotenv import load_dotenv

# Do not load real .env during test runs to ensure complete test isolation
if os.getenv("APP_ENV") != "test" and not os.getenv("PYTEST_CURRENT_TEST"):
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
    WHATSAPP_PROVIDER: str = "meta"  # Default provider: meta | twilio | 360dialog
    WHATSAPP_API_KEY: str = ""
    # Meta Graph API version — single configurable version.
    # Latest stable version per official changelog: v26.0 (released July 29, 2026).
    META_GRAPH_API_VERSION: str = "v26.0"
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_VERIFY_TOKEN: str = "andios_verify_token"
    META_APP_SECRET: str = ""  # App Secret for Meta Cloud API X-Hub-Signature-256 verification
    META_APP_ID: str = ""      # Meta App ID — required for Embedded Signup token exchange
    ENABLE_META_WHATSAPP: bool = True  # Master switch for Meta WhatsApp Embedded Signup and background sync
    # Shared secret required on INBOUND WhatsApp webhooks (header 'X-Webhook-Token'
    # or '?token=' query param). Mandatory in production — requests are rejected
    # without it (fail closed). Configure the callback URL accordingly in 360dialog.
    WHATSAPP_WEBHOOK_TOKEN: str = ""
    # Twilio — Master account credentials (Centralized Shared Gateway model).
    # DEPRECATED: New agencies use Meta Embedded Signup (BYON). Existing agencies
    # provisioned with Twilio continue to work. Guarded by ENABLE_TWILIO_PROVISIONING.
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    # Default outbound number used when an agency has no dedicated number yet
    TWILIO_WHATSAPP_NUMBER: str = ""
    # Feature flag: set True ONLY to allow legacy Twilio auto-provisioning for existing agencies.
    # New agencies must use Embedded Signup. Default: False (disabled).
    ENABLE_TWILIO_PROVISIONING: bool = False

    # When agent_id is passed to get_whatsapp_provider_for_agency but the agent has no
    # active communication_accounts row, allow falling back to the agency-default row.
    # Default: False — fail explicitly so misconfigured agents are caught early.
    ALLOW_AGENCY_NUMBER_FALLBACK_FOR_AGENTS: bool = False

    # Allow the platform-default Meta/env credentials to be used when an agency has no
    # active communication_accounts row AND no legacy dedicated Twilio number.
    # Only safe for the single-agency / default-agency deployment. Default: False.
    ALLOW_PLATFORM_DEFAULT_FALLBACK: bool = False

    # Feature flag: Voice BYON via Vapi SIP.
    # Default False (disabled) until UAE TDRA regulations (Federal Law No. 3, Cabinet Res. 56/2024)
    # and licensed domestic carrier (du/Etisalat e&) SIP trunking are verified.
    ENABLE_VOICE_BYON: bool = False

    # Dedicated Token Encryption Key (MultiFernet).
    # NEVER derives from SECRET_KEY. Supports comma-separated keys for zero-downtime rotation.
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    TOKEN_ENCRYPTION_KEY: str = ""
    ALLOW_LEGACY_PLAINTEXT_TOKENS: bool = True

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

    def __repr__(self) -> str:
        sensitive_keywords = (
            "KEY", "SECRET", "TOKEN", "PASSWORD", "AUTH", "CREDENTIAL",
            "PRIVATE", "SID", "URL", "URI"
        )
        fields = []
        field_names = getattr(self.__class__, "model_fields", None)
        if field_names is not None:
            keys = list(field_names.keys())
        elif hasattr(self, "__fields__"):
            keys = list(self.__fields__.keys())
        else:
            keys = list(self.__dict__.keys())

        for k in keys:
            v = getattr(self, k, None)
            if any(kw in k.upper() for kw in sensitive_keywords):
                fields.append(f"{k}='***REDACTED***'")
            else:
                fields.append(f"{k}={repr(v)}")
        return f"Settings({', '.join(fields)})"

    def __str__(self) -> str:
        return self.__repr__()

    class Config:
        env_file = None if (os.getenv("APP_ENV") == "test" or os.getenv("PYTEST_CURRENT_TEST")) else ".env"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

