from pydantic import BaseModel
from uuid import UUID
from datetime import datetime
from typing import Dict, List, Optional


# =====================================================
# PARENT
# =====================================================
class AdminParentResponse(BaseModel):
    id: UUID
    email: Optional[str]
    name: Optional[str]

    class Config:
        from_attributes = True


# =====================================================
# CHILD
# =====================================================
class AdminChildResponse(BaseModel):
    id: UUID
    name: str
    age: int
    onboarding_completed: bool

    class Config:
        from_attributes = True


# =====================================================
# TOY
# =====================================================
class AdminToyResponse(BaseModel):
    id: UUID
    toy_uuid: UUID
    status: str
    last_seen: Optional[datetime]

    class Config:
        from_attributes = True


# =====================================================
# MESSAGE
# =====================================================
class AdminMessage(BaseModel):
    role: str
    content: str
    created_at: datetime

    class Config:
        from_attributes = True


# =====================================================
# CONVERSATION
# =====================================================
class AdminConversationResponse(BaseModel):
    conversation_id: UUID
    messages: List[AdminMessage]


# =====================================================
# ANALYTICS SNAPSHOT
# =====================================================
class AdminAnalyticsResponse(BaseModel):

    child_id: UUID

    breakdown_json: Optional[Dict]

    updated_at: datetime

    class Config:
        from_attributes = True


# =====================================================
# DASHBOARD
# =====================================================
class AdminDashboardResponse(BaseModel):
    parents: int
    children: int
    toys: int
    active_toys: int