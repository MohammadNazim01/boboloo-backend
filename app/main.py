import logging

from fastapi import FastAPI

from app.routes import router
from app.routes.admin_routes import router as admin_router
from app.routes.ota_routes import router as ota_router

from app.middleware.request_logging import request_logging_middleware
from app.middleware.rate_limiter import rate_limit_middleware

from app.core.app_logging import setup_logging
from app.core.config import settings
from app.core.redis import redis_client

# =====================================================
# LOGGING SETUP
# =====================================================

setup_logging()

logger = logging.getLogger("main")

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

    try:
        await redis_client.ping()
        logger.info("Redis connected")
    except Exception as e:
        logger.error(f"Redis connection failed: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down API Server...")

    try:
        await redis_client.close()
        logger.info("Redis connection closed")
    except Exception as e:
        logger.error(f"Shutdown error: {e}")
