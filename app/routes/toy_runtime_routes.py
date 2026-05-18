from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.machine_auth import verify_toy
from app.database.database import get_db
from app.database.models import Toy
from app.schemas.toy_schema import ToyAskRequest, ToyAskResponse
from app.services.toy_runtime_service import ToyRuntimeService
from app.services.toy_response_service import ToyResponseService

router = APIRouter(
    prefix="/api/v1/toy/runtime",
    tags=["Toy Runtime"],
)

@router.get("/latest-answer/{conversation_id}")
async def get_latest_answer(
    conversation_id: str,
    toy: Toy = Depends(verify_toy),
    db: AsyncSession = Depends(get_db),
):

    return await ToyResponseService.get_latest_answer(
        db=db,
        toy=toy,
        conversation_id=conversation_id,
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
        toy_id=str(toy.factory_device_id),
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
