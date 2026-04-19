from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.machine_auth import verify_toy
from app.database.database import get_db
from app.database.models import Toy
from app.schemas.toy_schema import ToyAskRequest, ToyAskResponse
from app.services.toy_runtime_service import ToyRuntimeService

router = APIRouter(
    prefix="/api/v1/toy/runtime",
    tags=["Toy Runtime"],
)


@router.post("/ask", response_model=ToyAskResponse)
async def ask_question(
    data: ToyAskRequest,
    toy: Toy = Depends(verify_toy),
    db: AsyncSession = Depends(get_db),
):
    return await ToyRuntimeService.handle_question(
        db=db,
        toy=toy,
        question=data.question,
        battery_level=data.battery_level,
        wifi_signal=data.wifi_signal,
    )


@router.post("/heartbeat")
async def heartbeat(
    toy: Toy = Depends(verify_toy),
    db: AsyncSession = Depends(get_db),
):
    return await ToyRuntimeService.heartbeat(
        db=db,  
        toy=toy,
    )
