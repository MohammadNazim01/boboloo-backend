from fastapi import APIRouter, Depends
import logging

from app.auth.internal_auth import verify_internal
from app.services.analytics_batch_service import run_analytics_batch


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal",
    tags=["Internal"],
    dependencies=[Depends(verify_internal)],
)

@router.post("/run-analytics")
async def trigger_analytics():

    await run_analytics_batch()

    logger.info("Analytics batch executed")

    return {"status": "analytics batch executed"}