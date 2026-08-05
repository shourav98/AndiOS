"""
AndiOS Backend — FastAPI Application Entry Point

Phase 1: AI Lead Management, WhatsApp Automation,
         Google Calendar Integration, Supabase Database & Dashboard API

Run with: uvicorn main:app --reload
"""
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
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 AndiOS Backend starting up...")
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.FRONTEND_URL,
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
    ],
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
        content=api_error("Internal server error", 500, data=str(exc) if settings.DEBUG else None),
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
    return JSONResponse(
        status_code=422,
        content=api_error("Validation error", 422, data=exc.errors()),
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
)

app.include_router(auth.router)
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
        db_status = f"error: {str(e)}"

    return api_success(
        data={
            "status": "ok" if db_status == "connected" else "degraded",
            "database": db_status,
            "scheduler": "running" if scheduler.running else "stopped",
            "whatsapp_provider": settings.WHATSAPP_PROVIDER,
            "ai_model": settings.OPENAI_MODEL,
        },
        message="Health check completed"
    )
