from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.analytics_guard import analytics_ready_guard
from app.database.database import get_db
from app.database.models import AnalyticsHistory

# Presenters
from app.services.analytics_engine.presenter.gq_presenter import build_gq_ui
from app.services.analytics_engine.presenter.fq_presenter import build_fq_ui
from app.services.analytics_engine.presenter.vq_presenter import build_vq_ui
from app.services.analytics_engine.presenter.cq_presenter import build_cq_ui
from app.services.analytics_engine.presenter.mq_presenter import build_mq_ui


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
):

    analytics = data["analytics"]

    breakdown = analytics.breakdown_json or {}
    signals = breakdown.get("signals", {})

    # -----------------------------
    # Find weakest metric
    # -----------------------------

    scores = {
        "fq": analytics.fq,
        "vq": analytics.vq,
        "cq": analytics.cq,
        "mq": analytics.mq,
    }

    lowest = min(scores, key=scores.get)

    mapping = {
        "fq": ("PRONUNCIATION", "Unlock 'Sound Explorer' Game"),
        "vq": ("PREPOSITIONS", "Unlock 'Over & Under' Game"),
        "cq": ("QUESTION ASKING", "Unlock 'Curious Cat' Game"),
        "mq": ("MEMORY RECALL", "Unlock 'Story Builder' Game"),
    }

    focus_area, action = mapping.get(
        lowest,
        ("GENERAL DEVELOPMENT", "Play Learning Game"),
    )

    # -----------------------------
    # BOBOLOOP signals
    # -----------------------------

    unique_words = signals.get("unique_words", 0)

    play_quality_change = analytics.trend_percent or 0

    # prevent negative UI values
    play_quality_change = round(play_quality_change, 1)

    # -----------------------------
    # Response
    # -----------------------------

    return {

        "boboloop": {
            "new_words_this_week": unique_words,
            "play_quality_change_percent": play_quality_change,
        },

        "weekly_focus": {
            "focus_area": focus_area,
            "recommended_action": action,
        },

        "velocity": analytics.velocity,
    }

# =====================================================
# GQ DETAIL
# =====================================================

@router.get("/gq")
async def gq_detail(
    data: dict = Depends(analytics_ready_guard),
    db: AsyncSession = Depends(get_db),
    period: str = "week",
):

    child = data["child"]
    analytics = data["analytics"]

    signals = (analytics.breakdown_json or {}).get("signals", {}).copy()

    # -----------------------------
    # Detect report period for insight
    # -----------------------------

    if period == "day":
        signals["report_period"] = "daily"

    elif period == "week":
        signals["report_period"] = "weekly"

    elif period in ["2weeks", "3weeks"]:
        signals["report_period"] = "last_week"

    elif period == "month":
        signals["report_period"] = "monthly"

    else:
        signals["report_period"] = "weekly"

    # --------------------------------
    # Fetch analytics history
    # --------------------------------

    result = await db.execute(
        select(AnalyticsHistory)
        .where(AnalyticsHistory.child_id == child.id)
        .order_by(AnalyticsHistory.created_at.asc())
    )

    rows = result.scalars().all()

    history = []
    gq_values = []

    for r in rows:

        fq = r.fq or 0
        vq = r.vq or 0
        cq = r.cq or 0
        mq = r.mq or 0

        whole_child_map = {
            "logic": round(mq, 1),
            "language": round((fq + vq) / 2, 1),
            "creativity": round(cq * 0.85, 1),
            "empathy": round(cq * 0.65, 1),
            "focus": round(mq * 1.05, 1),
        }

        history.append({
            "date": r.created_at.isoformat(),
            "whole_child_map": whole_child_map
        })

        if r.gq is not None:
            gq_values.append(r.gq)

    # --------------------------------
    # Calculate previous GQ for trend insights
    # --------------------------------

    previous_gq = None

    if len(gq_values) > 1:
        previous_gq = sum(gq_values[:-1]) / len(gq_values[:-1])

    # --------------------------------
    # Build UI response
    # --------------------------------

    response = build_gq_ui(
        quotients={
            "fq": analytics.fq or 0,
            "vq": analytics.vq or 0,
            "cq": analytics.cq or 0,
            "mq": analytics.mq or 0,
            "gq": analytics.gq or 0,
        },
        signals=signals,
        age=child.age,
        history=history,
        previous_gq=previous_gq,
    )

    response["period"] = period

    return response



# =====================================================
# FQ DETAIL
# =====================================================

@router.get("/fq")
async def fq_detail(
    data: dict = Depends(analytics_ready_guard),
):

    child = data["child"]
    analytics = data["analytics"]

    breakdown = analytics.breakdown_json or {}

    signals = (breakdown.get("signals") or {})

    return build_fq_ui(
        {"fq": analytics.fq},
        breakdown,
        signals,
        child.age,
    )

# =====================================================
# VQ DETAIL
# =====================================================

@router.get("/vq")
async def vq_detail(
    data: dict = Depends(analytics_ready_guard),
    db: AsyncSession = Depends(get_db),
):

    child = data["child"]
    analytics = data["analytics"]

    data_json = analytics.breakdown_json or {}

    breakdown = data_json.get("breakdown", {})
    signals = data_json.get("signals", {}).copy()

    # --------------------------------
    # FETCH HISTORY (LAST 7 DAYS)
    # --------------------------------

    result = await db.execute(
        select(AnalyticsHistory)
        .where(AnalyticsHistory.child_id == child.id)
        .order_by(AnalyticsHistory.analytics_date.asc())
    )

    rows = result.scalars().all()
    rows = rows[-7:]  # default = week

    # --------------------------------
    # BUILD GRAPH
    # --------------------------------

    graph = [
        {
            "date": r.analytics_date.isoformat(),
            "vq": r.vq or 0,
        }
        for r in rows
    ] or []

    # --------------------------------
    # % CHANGE CALCULATION (REAL)
    # --------------------------------

    percent = 0
    daily_changes = []

    if len(graph) >= 2:

        for i in range(1, len(graph)):
            prev = graph[i - 1]["vq"]
            curr = graph[i]["vq"]

            if prev > 0:
                change = ((curr - prev) / prev) * 100
                daily_changes.append(change)

    if daily_changes:
        percent = round(sum(daily_changes) / len(daily_changes), 1)

    percent = max(0, min(percent, 100))

    # --------------------------------
    # TEXT INSIGHT
    # --------------------------------

    if percent > 0:
        text = f"Vocabulary growing at +{percent}%"
    else:
        text = "Vocabulary stable"

    # --------------------------------
    # INJECT INTO SIGNALS (VERY IMPORTANT)
    # --------------------------------

    signals["vq_graph"] = graph
    signals["vq_percent_change"] = percent
    signals["vq_insight_text"] = text

    # --------------------------------
    # FINAL RESPONSE
    # --------------------------------

    return build_vq_ui(
        {"vq": analytics.vq},
        breakdown.get("vq", {}),
        signals,
    )


# =====================================================
# CQ DETAIL
# =====================================================

@router.get("/cq")
async def cq_detail(
    data: dict = Depends(analytics_ready_guard),
):

    analytics = data["analytics"]

    breakdown = analytics.breakdown_json or {}
    signals = breakdown.get("signals", {})

    return build_cq_ui(
        {"cq": analytics.cq},
        breakdown,
        signals,
    )


# =====================================================
# MQ DETAIL
# =====================================================

@router.get("/mq")
async def mq_detail(
    data: dict = Depends(analytics_ready_guard),
):

    analytics = data["analytics"]

    breakdown = analytics.breakdown_json or {}
    signals = breakdown.get("signals", {})

    return build_mq_ui(
        {"mq": analytics.mq},
        breakdown,
        signals,
    )