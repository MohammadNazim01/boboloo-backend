from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession


from app.auth.firebase_auth import get_current_parent
from app.database.database import get_db
from app.database.models import Parent
from app.schemas.child_schema import (
    ChildCreate,
    ChildUpdate,
    ChildResponse,
)
from app.services.child_service import (
    create_child,
    get_child,
    update_child,
)

router = APIRouter(
    prefix="/api/v1/parent",
    tags=["Parent"],
)


# ---------------------------------------------------------
# Create Child (Onboarding)
# ---------------------------------------------------------
@router.post("/child", response_model=ChildResponse)
async def add_child(
    child_data: ChildCreate,
    parent: Parent = Depends(get_current_parent),
    db: AsyncSession = Depends(get_db),
):
    return await create_child(db, parent, child_data)


# ---------------------------------------------------------
# Get Child (Profile View)
# ---------------------------------------------------------
@router.get("/child", response_model=ChildResponse | None)
async def fetch_child(
    parent: Parent = Depends(get_current_parent),
    db: AsyncSession = Depends(get_db),
):
    return await get_child(db, parent)


# ---------------------------------------------------------
# Update Child (Profile Edit)
# ---------------------------------------------------------
@router.put("/child", response_model=ChildResponse)
async def update_profile(
    data: ChildUpdate,
    parent: Parent = Depends(get_current_parent),
    db: AsyncSession = Depends(get_db),
):
    return await update_child(db, parent, data)


