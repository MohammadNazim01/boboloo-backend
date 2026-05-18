from fastapi import APIRouter

from .parent_routes import router as parent_router
from .toy_claim_routes import router as claim_router
from .toy_runtime_routes import router as runtime_router
from .analytics_routes import router as analytics_router
from app.routes.factory_routes import router as factory_router
from .internal_routes import router as internal_router
from .mqtt_auth_routes import router as mqtt_auth_router

router = APIRouter()

router.include_router(parent_router)
router.include_router(claim_router)
router.include_router(runtime_router)
router.include_router(analytics_router)
router.include_router(factory_router)
router.include_router(internal_router)
router.include_router(mqtt_auth_router)