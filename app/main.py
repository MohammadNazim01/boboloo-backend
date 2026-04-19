from fastapi import FastAPI
from app.routes import router
from app.middleware.request_logging import request_logging_middleware
from app.routes.admin_routes import router as admin_router
from app.core.app_logging import setup_logging
from app.core.config import settings
from app.core.redis import check_redis

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

setup_logging()

app = FastAPI(
    title="Boboloo Backend API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

app.middleware("http")(request_logging_middleware)

app.include_router(router)
app.include_router(admin_router)


@app.get("/")
async def root():
    return {"status": "Boboloo Backend Running"}


@app.get("/health")
async def health():
    return {"status": "ok"}