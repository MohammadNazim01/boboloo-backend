from pydantic import BaseModel
from typing import Dict, Optional
from datetime import datetime


class AnalyticsOverviewResponse(BaseModel):

    cards: Dict[str, float]

    velocity: Optional[str]
    confidence: Optional[float]
    trend_percent: Optional[float]

    updated_at: datetime

class GQDetailResponse(BaseModel):

    gq_score: float
    whole_child_map: Dict[str, float]
    development: Dict
    velocity: Dict
    insight: str

class FQDetailResponse(BaseModel):

    fq_score: float
    stage: str
    fluency_map: Dict
    weekly_action: str

class VQDetailResponse(BaseModel):

    vq_score: float
    stage: str
    vocabulary_map: Dict
    weekly_action: str

class CQDetailResponse(BaseModel):

    cq_score: float
    stage: str
    communication_map: Dict
    weekly_action: str

class MQDetailResponse(BaseModel):

    mq_score: float
    stage: str
    memory_map: Dict
    weekly_action: str