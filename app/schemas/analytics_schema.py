from pydantic import BaseModel
from typing import List


class VocabularySummary(BaseModel):
    total_words: int
    unique_words: int
    new_words: int
    trend: str


class PeriodStats(BaseModel):
    total_words: int
    unique_words: int
    new_words: int


class DeltaStats(BaseModel):
    total_words: str
    unique_words: str
    new_words: str


class ComparisonBlock(BaseModel):
    today: PeriodStats
    yesterday: PeriodStats
    today_vs_yesterday: DeltaStats


class WeeklyGraphPoint(BaseModel):
    date: str
    total_words: int
    unique_words: int
    new_words: int


class MonthlyGraphPoint(BaseModel):
    week: int
    total_words: int
    unique_words: int
    new_words: int


class VocabularyDetailResponse(BaseModel):
    summary: VocabularySummary
    comparison: ComparisonBlock
    weekly_graph: List[WeeklyGraphPoint]
    monthly_graph: List[MonthlyGraphPoint]
