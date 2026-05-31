import logging

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.routes import router
from app.routes.admin_routes import router as admin_router
from app.routes.ota_routes import router as ota_router

from app.middleware.request_logging import request_logging_middleware
from app.middleware.rate_limiter import rate_limit_middleware

from app.core.app_logging import setup_logging
from app.core.config import settings
from app.core.redis import redis_client
from app.services.analytics_batch_service import run_analytics_batch

# =====================================================
# LOGGING SETUP
# =====================================================

setup_logging()

logger = logging.getLogger("main")

if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        traces_sample_rate=0.2,
    )
    logger.info("Sentry initialized")

scheduler = AsyncIOScheduler()

# =====================================================
# APP INIT
# =====================================================

app = FastAPI(
    title="Boboloo Backend API",
    version="1.0.0",
    docs_url="/docs" if settings.ENVIRONMENT == "development" else None,
    redoc_url="/redoc" if settings.ENVIRONMENT == "development" else None,
    openapi_url="/openapi.json" if settings.ENVIRONMENT == "development" else None,
)

# =====================================================
# MIDDLEWARE
# =====================================================

_origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.middleware("http")(rate_limit_middleware)
app.middleware("http")(request_logging_middleware)

# =====================================================
# ROUTES
# =====================================================

app.include_router(router)
app.include_router(admin_router)
app.include_router(ota_router)

# =====================================================
# BASIC ROUTES
# =====================================================

@app.get("/")
async def root():
    return {"status": "Boboloo Backend Running"}


@app.get("/health")
async def health():
    return {"status": "ok"}


# =====================================================
# STARTUP / SHUTDOWN
# =====================================================

@app.on_event("startup")
async def startup_event():
    logger.info("Starting Boboloo API Server...")

    if settings.ENVIRONMENT == "production" and not settings.MQTT_AUTH_SECRET:
        raise RuntimeError(
            "MQTT_AUTH_SECRET must be set in production. "
            "Without it, EMQX will reject every toy MQTT connection."
        )

    try:
        await redis_client.ping()
        logger.info("Redis connected")
    except Exception as e:
        logger.error(f"Redis connection failed: {e}")

    scheduler.add_job(
        run_analytics_batch,
        CronTrigger(hour=2, minute=0),
        id="daily_analytics",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Analytics scheduler started — runs daily at 02:00")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down API Server...")

    scheduler.shutdown(wait=False)

    try:
        await redis_client.close()
        logger.info("Redis connection closed")
    except Exception as e:
        logger.error(f"Shutdown error: {e}")
