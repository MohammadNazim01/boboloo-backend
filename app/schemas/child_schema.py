from pydantic import BaseModel
from typing import List, Optional
from uuid import UUID
# -------------------------
# Create Child (Onboarding Step 1)
# -------------------------
class ChildCreate(BaseModel):
    name: str
    age: int
    guardian_name: str
    interests: List[str]


# -------------------------
# Update Child (Profile Edit)
# -------------------------
class ChildUpdate(BaseModel):
    name: Optional[str] = None
    age: Optional[int] = None
    guardian_name: Optional[str] = None
    interests: Optional[List[str]] = None
    keywords_filter: Optional[List[str]] = None
    focus_topics: Optional[List[str]] = None


# -------------------------
# Response Model
# -------------------------
class ChildResponse(BaseModel):
    id: UUID
    name: str
    age: int
    guardian_name: Optional[str]

    interests: List[str] = []
    keywords_filter: List[str] = []
    focus_topics: List[str] = []

    onboarding_completed: bool

    class Config:
        from_attributes = True
