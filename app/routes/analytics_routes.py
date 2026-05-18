from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date, timedelta

from app.auth.analytics_guard import analytics_ready_guard
from app.database.database import get_db
from app.database.models import AnalyticsHistory, ChildVocabularyMemory, ChildStreak


router = APIRouter(
    prefix="/api/v1/analytics",
    tags=["Analytics"],
)


# =====================================================
# OVERVIEW
# =====================================================

@router.get("/overview")
async def analytics_overview(
    data: dict = Depends(analytics_ready_guard),
    db: AsyncSession = Depends(get_db),
):
    child = data["child"]
    analytics = data["analytics"]

    breakdown = analytics.breakdown_json or {}
    vocabulary = breakdown.get("vocabulary", {})

    today_total  = vocabulary.get("TotalWordsCount", 0)
    today_unique = vocabulary.get("UniqueWordsCount", 0)
    today_new    = vocabulary.get("NewWordsCount", 0)

    # ----------------------------------------
    # LAST 7 DAYS HISTORY
    # ----------------------------------------
    history_result = await db.execute(
        select(AnalyticsHistory)
        .where(AnalyticsHistory.child_id == child.id)
        .order_by(AnalyticsHistory.analytics_date.asc())
    )
    history_rows = history_result.scalars().all()[-7:]

    # ----------------------------------------
    # LIFETIME VOCABULARY MEMORY
    # ----------------------------------------
    vocab_result = await db.execute(
        select(ChildVocabularyMemory)
        .where(ChildVocabularyMemory.child_id == child.id)
    )
    vocab_memory = vocab_result.scalars().all()

    total_lifetime_words = len(vocab_memory)

    # =========================================
    # SIGNAL 1 — WEEKLY NEW WORDS
    # =========================================
    weekly_new_words = sum(
        (r.breakdown_json or {}).get("vocabulary", {}).get("NewWordsCount", 0)
        for r in history_rows
    )

    # =========================================
    # SIGNAL 2 — TREND (compare last 2 days)
    # =========================================
    trend = "stable"
    prev_new = 0
    if len(history_rows) >= 2:
        latest_new = (history_rows[-1].breakdown_json or {}).get("vocabulary", {}).get("NewWordsCount", 0)
        prev_new   = (history_rows[-2].breakdown_json or {}).get("vocabulary", {}).get("NewWordsCount", 0)
        if latest_new > prev_new:
            trend = "improving"
        elif latest_new < prev_new:
            trend = "declining"

    # =========================================
    # SIGNAL 3 — WORD DIVERSITY
    # unique content words / total words spoken
    # =========================================
    diversity_ratio = (today_unique / today_total) if today_total > 0 else 0

    # =========================================
    # SIGNAL 4 — RETENTION RATE
    # words used on more than one day = retained
    # =========================================
    retained_words = [w for w in vocab_memory if w.usage_count >= 2]
    retention_rate = (
        len(retained_words) / total_lifetime_words
        if total_lifetime_words > 0 else 0
    )

    # =========================================
    # SIGNAL 5 — WORDS TO REVISIT
    # learned words not used in 2+ days
    # =========================================
    today_date = date.today()
    words_to_revisit = [
        w.word for w in vocab_memory
        if w.usage_count == 1 and (today_date - w.last_seen).days >= 2
    ][:5]

    # =========================================
    # FOCUS AREA — priority-ordered decision
    # =========================================

    if diversity_ratio < 0.25 and today_total > 0:
        focus_area = "Word Diversity"
        insight = (
            f"Your child spoke {today_total} words today but only "
            f"{today_unique} were unique content words "
            f"({int(diversity_ratio * 100)}% diversity). "
            f"They may be repeating the same words often. "
            f"Try exploring new topics like animals, colors, or places."
        )
        recommended_action = (
            "Ask open-ended questions about things around the house or outside "
            "to naturally introduce more varied vocabulary"
        )

    elif trend == "declining" and len(history_rows) >= 2:
        focus_area = "Vocabulary Expansion"
        insight = (
            f"Your child learned {today_new} new words today, "
            f"down from {prev_new} yesterday. "
            f"New word learning has slowed — fresh activities and stories "
            f"can help reignite curiosity."
        )
        recommended_action = (
            "Read a new story together or visit a new place — "
            "new experiences naturally introduce new vocabulary"
        )

    elif retention_rate < 0.3 and total_lifetime_words >= 10:
        focus_area = "Word Retention"
        insight = (
            f"Your child knows {total_lifetime_words} words in total but only "
            f"{len(retained_words)} ({int(retention_rate * 100)}%) "
            f"have been used more than once. "
            f"Revisiting past topics helps words move into long-term memory."
        )
        recommended_action = (
            f"Try bringing up past topics again — words like "
            f"{', '.join(words_to_revisit)} haven't come up recently"
            if words_to_revisit else
            "Revisit past conversation topics to strengthen vocabulary memory"
        )

    elif today_new >= 10:
        focus_area = "Strong Growth Day"
        insight = (
            f"Excellent day — your child learned {today_new} new words today "
            f"and now has {total_lifetime_words} words in their vocabulary. "
            f"Keep this momentum going."
        )
        recommended_action = (
            "Build on today's topics tomorrow — "
            "repetition over multiple days locks words into long-term memory"
        )

    else:
        focus_area = "Consistent Learning"
        insight = (
            f"Your child is steadily building vocabulary. "
            f"{today_new} new words learned today, "
            f"{total_lifetime_words} total words known. "
            f"Consistent daily conversation is the most powerful learning tool."
        )
        recommended_action = (
            "Keep daily conversations going — "
            "even 10 minutes of open-ended talk makes a measurable difference"
        )

    # =========================================
    # VOCABULARY GROWTH SUMMARY STRING
    # human-readable, derived from real signals
    # =========================================
    if weekly_new_words == 0 and total_lifetime_words == 0:
        vocabulary_growth = "No vocabulary data yet — start a conversation to begin tracking growth."
    elif trend == "improving":
        vocabulary_growth = (
            f"Your child learned {weekly_new_words} new words this week "
            f"and their learning pace is picking up. "
            f"They now know {total_lifetime_words} words in total."
        )
    elif trend == "declining":
        vocabulary_growth = (
            f"Your child learned {weekly_new_words} new words this week. "
            f"The pace has slowed slightly compared to yesterday — "
            f"fresh topics can help spark new learning. "
            f"Total vocabulary: {total_lifetime_words} words."
        )
    else:
        vocabulary_growth = (
            f"Your child learned {weekly_new_words} new words this week "
            f"and now has {total_lifetime_words} words in their vocabulary. "
            f"Learning is steady and consistent."
        )

    # =========================================
    # RESPONSE — single clean object
    # =========================================
    return {
        "weekly_focus": {
            "focus_area": focus_area,
            "insight": insight,
            "recommended_action": recommended_action,
            "vocabulary_growth": vocabulary_growth,
        },
    }


# =====================================================
# VOCABULARY DETAIL
# =====================================================

@router.get("/vocabulary")
async def vocabulary_detail(
    data: dict = Depends(analytics_ready_guard),
    db: AsyncSession = Depends(get_db),
):
    child = data["child"]
    analytics = data["analytics"]

    today = date.today()
    yesterday = today - timedelta(days=1)
    month_start = today - timedelta(days=29)   # 30-day rolling window
    week_start = today - timedelta(days=6)     # 7-day rolling window

    # ------------------------------------------------
    # FETCH LAST 30 DAYS IN ONE QUERY
    # Covers weekly graph, monthly graph, and
    # yesterday comparison simultaneously.
    # ------------------------------------------------
    result = await db.execute(
        select(AnalyticsHistory)
        .where(
            AnalyticsHistory.child_id == child.id,
            AnalyticsHistory.analytics_date >= month_start,
            AnalyticsHistory.analytics_date <= today,
        )
        .order_by(AnalyticsHistory.analytics_date.asc())
    )
    all_history = result.scalars().all()

    # O(1) date lookup used for yesterday comparison
    by_date = {r.analytics_date: r for r in all_history}

    # ------------------------------------------------
    # HELPER — extract vocabulary counts from a row
    # ------------------------------------------------
    def _vocab(row):
        if row is None:
            return {"total_words": 0, "unique_words": 0, "new_words": 0}
        v = (row.breakdown_json or {}).get("vocabulary", {})
        return {
            "total_words":  v.get("TotalWordsCount", 0),
            "unique_words": v.get("UniqueWordsCount", 0),
            "new_words":    v.get("NewWordsCount", 0),
        }

    # ------------------------------------------------
    # SUMMARY — today's data from ChildAnalytics
    # (written by the nightly batch, same source
    # the app has always used)
    # ------------------------------------------------
    breakdown = analytics.breakdown_json or {}
    vocabulary = breakdown.get("vocabulary", {})

    today_total  = vocabulary.get("TotalWordsCount", 0)
    today_unique = vocabulary.get("UniqueWordsCount", 0)
    today_new    = vocabulary.get("NewWordsCount", 0)

    # ------------------------------------------------
    # TREND — derived from last 2 days in history
    # ------------------------------------------------
    trend = "stable"
    last_7 = [r for r in all_history if r.analytics_date >= week_start]
    if len(last_7) >= 2:
        latest_new  = (last_7[-1].breakdown_json or {}).get("vocabulary", {}).get("NewWordsCount", 0)
        previous_new = (last_7[-2].breakdown_json or {}).get("vocabulary", {}).get("NewWordsCount", 0)
        if latest_new > previous_new:
            trend = "improving"
        elif latest_new < previous_new:
            trend = "declining"

    # ------------------------------------------------
    # COMPARISON — today vs yesterday
    # ------------------------------------------------
    today_data     = {"total_words": today_total, "unique_words": today_unique, "new_words": today_new}
    yesterday_data = _vocab(by_date.get(yesterday))

    def _delta(a: int, b: int) -> str:
        diff = a - b
        if diff > 0:
            return f"+{diff}"
        if diff < 0:
            return str(diff)
        return "0"

    comparison = {
        "today": today_data,
        "yesterday": yesterday_data,
        "today_vs_yesterday": {
            "total_words":  _delta(today_data["total_words"],  yesterday_data["total_words"]),
            "unique_words": _delta(today_data["unique_words"], yesterday_data["unique_words"]),
            "new_words":    _delta(today_data["new_words"],    yesterday_data["new_words"]),
        },
    }

    # ------------------------------------------------
    # WEEKLY GRAPH — one data point per day (7 days)
    # All 7 days always present; missing days get zeros.
    # ------------------------------------------------
    weekly_graph = [
        {
            "date":         (week_start + timedelta(days=i)).isoformat(),
            **_vocab(by_date.get(week_start + timedelta(days=i))),
        }
        for i in range(7)
    ]

    # ------------------------------------------------
    # MONTHLY GRAPH — 4 weekly buckets (oldest → newest)
    #
    # Bucket assignment (days_ago from today):
    #   week 1 → days 22–29   (oldest)
    #   week 2 → days 15–21
    #   week 3 → days 8–14
    #   week 4 → days 0–7     (most recent)
    #
    # total_words and new_words are additive across days.
    # unique_words is summed per-day (an approximation for
    # graph display — cross-day deduplication would require
    # re-running NLP, which we avoid at request time).
    # ------------------------------------------------
    buckets: dict = {1: [], 2: [], 3: [], 4: []}
    for r in all_history:
        days_ago = (today - r.analytics_date).days
        if days_ago <= 7:
            buckets[4].append(r)
        elif days_ago <= 14:
            buckets[3].append(r)
        elif days_ago <= 21:
            buckets[2].append(r)
        else:
            buckets[1].append(r)

    monthly_graph = [
        {
            "week":         wk,
            "total_words":  sum((r.breakdown_json or {}).get("vocabulary", {}).get("TotalWordsCount", 0) for r in rows),
            "unique_words": sum((r.breakdown_json or {}).get("vocabulary", {}).get("UniqueWordsCount", 0) for r in rows),
            "new_words":    sum((r.breakdown_json or {}).get("vocabulary", {}).get("NewWordsCount", 0) for r in rows),
        }
        for wk, rows in sorted(buckets.items())
    ]

    # ------------------------------------------------
    # CONVERSATION STREAK
    # ------------------------------------------------
    streak_result = await db.execute(
        select(ChildStreak).where(ChildStreak.child_id == child.id)
    )
    streak = streak_result.scalar_one_or_none()

    if streak is None or streak.last_conversation_date is None:
        conversation_streak = {
            "current_streak": 0,
            "longest_streak": 0,
            "last_conversation_date": None,
            "streak_started_at": None,
            "status": "none",
        }
    else:
        lcd = streak.last_conversation_date
        if lcd == today:
            status, current = "active", streak.current_streak
        elif lcd == yesterday:
            status, current = "at_risk", streak.current_streak
        else:
            status, current = "broken", 0

        conversation_streak = {
            "current_streak": current,
            "longest_streak": streak.longest_streak,
            "last_conversation_date": lcd.isoformat(),
            "streak_started_at": streak.streak_started_at.isoformat() if streak.streak_started_at else None,
            "status": status,
        }

    # ------------------------------------------------
    # RESPONSE
    # ------------------------------------------------
    return {
        "summary": {
            "total_words":  today_total,
            "unique_words": today_unique,
            "new_words":    today_new,
            "trend":        trend,
        },
        "comparison":          comparison,
        "weekly_graph":        weekly_graph,
        "monthly_graph":       monthly_graph,
        "conversation_streak": conversation_streak,
    }
