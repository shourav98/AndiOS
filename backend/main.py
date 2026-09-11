"""
AndiOS Backend — FastAPI Application Entry Point

Phase 1: AI Lead Management, WhatsApp Automation,
         Google Calendar Integration, Supabase Database & Dashboard API

Run with: uvicorn main:app --reload
"""
from dotenv import load_dotenv

load_dotenv(override=True)

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from services.scheduler import scheduler
from config import settings
from utils.response import api_success
import logging

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("andios")


# ─── Startup / Shutdown ───────────────────────────────────────────────────────

def _validate_production_config() -> None:
    """Loudly report production configuration gaps at boot (does not block)."""
    if settings.APP_ENV == "development":
        return
    required = {
        "SECRET_KEY": settings.SECRET_KEY,
        "FRONTEND_URL": settings.FRONTEND_URL,
        "API_BASE_URL": settings.API_BASE_URL,
        "WHATSAPP_VERIFY_TOKEN": settings.WHATSAPP_VERIFY_TOKEN,
        "STRIPE_SECRET_KEY": settings.STRIPE_SECRET_KEY,
        "STRIPE_WEBHOOK_SECRET": settings.STRIPE_WEBHOOK_SECRET,
        "PROPERTY_FINDER_WEBHOOK_SECRET": settings.PROPERTY_FINDER_WEBHOOK_SECRET,
        "WHATSAPP_WEBHOOK_TOKEN": settings.WHATSAPP_WEBHOOK_TOKEN,
        "VAPI_WEBHOOK_SECRET": settings.VAPI_WEBHOOK_SECRET,
        "BAYUT_WEBHOOK_TOKEN": settings.BAYUT_WEBHOOK_TOKEN,
        "DUBIZZLE_WEBHOOK_TOKEN": settings.DUBIZZLE_WEBHOOK_TOKEN,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        logger.critical(
            "PRODUCTION CONFIGURATION INCOMPLETE — missing: %s. "
            "Related endpoints will reject requests (fail closed).",
            ", ".join(missing),
        )
    if settings.SECRET_KEY in ("change-me-in-production", "andios-dev-secret-key-change-in-production", ""):
        logger.critical("INSECURE SECRET_KEY — replace default development secret key in production .env immediately.")
    if not str(settings.FRONTEND_URL or "").startswith("https://"):
        logger.critical("FRONTEND_URL should use HTTPS in production (CORS/cookies depend on it).")
    if not str(settings.API_BASE_URL or "").startswith("https://"):
        logger.critical("API_BASE_URL should use HTTPS in production (webhook callback URLs depend on it).")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 AndiOS Backend starting up...")
    _validate_production_config()
    scheduler.start()
    logger.info("⏰ Scheduler started")
    yield
    logger.info("🛑 AndiOS Backend shutting down...")
    scheduler.shutdown(wait=False)


# ─── App Init ─────────────────────────────────────────────────────────────────
app = FastAPI(
    title="AndiOS API",
    description=(
        "AI-powered real estate lead management system for Dubai property agencies. "
        "Phase 1: Lead capture, WhatsApp AI, Google Calendar, Supabase."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ─── CORS ─────────────────────────────────────────────────────────────────────
# Production: only the configured FRONTEND_URL is allowed.
# Development: localhost dev servers are also permitted.

def _cors_allow_origins() -> list[str]:
    origins = []
    if settings.FRONTEND_URL:
        fe = settings.FRONTEND_URL.rstrip("/")
        origins.append(fe)
    api = (settings.API_BASE_URL or "").rstrip("/")
    if api and api.startswith("http"):
        # same-origin API calls don't need CORS, but harmless to allow
        pass
    if settings.APP_ENV == "development":
        origins += [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
        ]
    return list(dict.fromkeys(o for o in origins if o))


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Global Error Handlers ────────────────────────────────────────────────────
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from utils.response import api_error

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content=api_error("Internal server error", 500, data=str(exc) if settings.APP_ENV == "development" else None),
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content=api_error(exc.detail, exc.status_code),
        headers=getattr(exc, "headers", None)
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    cleaned_errors = []
    for err in exc.errors():
        err_copy = dict(err)
        if isinstance(err_copy.get("input"), bytes):
            err_copy["input"] = err_copy["input"].decode("utf-8", errors="ignore")
        cleaned_errors.append(err_copy)
    return JSONResponse(
        status_code=422,
        content=api_error("Validation error", 422, data=cleaned_errors),
    )


# ─── Routers ──────────────────────────────────────────────────────────────────
from routers import (
    agents,
    leads,
    viewings,
    conversations,
    webhooks,
    auth,
    reports,
    documents,
    contracts,
    cheques,
    connectors,
    dashboard,
    owners,
    call_campaigns,
    calls,
    admin,
    subscription,
    branches,
    agent_phone_settings,
)

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(agents.router)
app.include_router(branches.router)
app.include_router(webhooks.router)
app.include_router(leads.router)
app.include_router(conversations.router)
app.include_router(viewings.router)
app.include_router(reports.router)
app.include_router(documents.router)
app.include_router(contracts.router)
app.include_router(cheques.router)
app.include_router(connectors.router)
app.include_router(dashboard.router)
app.include_router(owners.router)
app.include_router(call_campaigns.router)
app.include_router(calls.router)
app.include_router(subscription.router)
app.include_router(agent_phone_settings.router)


# ─── Health Check ─────────────────────────────────────────────────────────────
@app.get("/", tags=["Health"])
async def root():
    return api_success(
        data={
            "service": "AndiOS API",
            "version": "1.0.0",
            "status": "operational",
            "phase": "Phase 1 — AI Lead Management",
            "docs": "/docs",
        },
        message="API is running"
    )


@app.get("/health", tags=["Health"])
async def health():
    """Health check for deployment monitoring."""
    try:
        from database.supabase_client import get_supabase
        sb = get_supabase()
        sb.table("leads").select("id").limit(1).execute()
        db_status = "connected"
    except Exception as e:
        logger.error(f"Health check DB error: {e}")
        db_status = "error"

    return api_success(
        data={
            "status": "ok" if db_status == "connected" else "degraded",
            "database": db_status,
            "scheduler": "running" if scheduler.running else "stopped",
        },
        message="Health check completed"
    )
