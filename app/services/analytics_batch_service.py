import asyncio
import math
from datetime import datetime, date

from sqlalchemy import select, func

from app.database.database import AsyncSessionLocal
from app.database.models import (
    Child,
    Conversation,
    Message,
    ChildAnalytics,
    AnalyticsHistory,
    ChildVocabularyMemory,
)

from app.services.analytics_engine.engine import generate_analytics
from app.services.analytics_engine.velocity import classify_velocity


# =====================================================
# AGE CALCULATION
# =====================================================

def calculate_age_years(birth_date):
    if not birth_date:
        return 0

    today = date.today()
    years = today.year - birth_date.year

    if (today.month, today.day) < (birth_date.month, birth_date.day):
        years -= 1

    return years


# =====================================================
# UPDATE VOCABULARY MEMORY
# =====================================================

async def update_vocabulary_memory(db, child_id, words):

    today = date.today()
    unique_words = {w.lower() for w in words}

    result = await db.execute(
        select(ChildVocabularyMemory).where(
            ChildVocabularyMemory.child_id == child_id
        )
    )

    existing_records = {r.word: r for r in result.scalars().all()}

    for word in unique_words:

        if word in existing_records:
            record = existing_records[word]
            record.usage_count += 1
            record.last_seen = today

        else:
            db.add(
                ChildVocabularyMemory(
                    child_id=child_id,
                    word=word,
                    first_seen=today,
                    last_seen=today,
                    usage_count=1,
                )
            )


# =====================================================
# REAL NOVELTY
# =====================================================

async def compute_real_novelty(db, child_id):

    today = date.today()

    result = await db.execute(
        select(ChildVocabularyMemory).where(
            ChildVocabularyMemory.child_id == child_id
        )
    )

    records = result.scalars().all()

    new_words = 0
    reused_words = 0

    for r in records:
        if r.first_seen == today:
            new_words += 1
            if r.usage_count > 1:
                reused_words += 1

    return new_words, reused_words


# =====================================================
# PROCESS CHILD
# =====================================================

async def process_child(child, now):

    today = date.today()

    async with AsyncSessionLocal() as db:

        try:

            # AGE
            age = child.age
            if child.birth_date:
                age = calculate_age_years(child.birth_date)

            # MESSAGE COUNT
            msg_result = await db.execute(
                select(func.count(Message.id))
                .join(Conversation, Message.conversation_id == Conversation.id)
                .where(
                    Conversation.child_id == child.id,
                    Conversation.conversation_date == today,
                )
            )

            total_messages = msg_result.scalar() or 0

            if total_messages < 10:
                print(f"⚠️ Not enough messages for child {child.id}")
                return

            # FETCH MESSAGES
            messages_result = await db.execute(
                select(Message)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .where(
                    Conversation.child_id == child.id,
                    Conversation.conversation_date == today,
                )
                .order_by(Message.created_at)
            )

            raw_messages = messages_result.scalars().all()

            formatted_messages = [
                {"role": m.role, "content": m.content}
                for m in raw_messages
            ]

            # PREVIOUS ANALYTICS
            prev_result = await db.execute(
                select(ChildAnalytics).where(
                    ChildAnalytics.child_id == child.id
                )
            )

            analytics = prev_result.scalars().first()

            previous_scores = None
            previous_gq = None

            if analytics:
                previous_scores = {
                    "fq": analytics.fq,
                    "vq": analytics.vq,
                    "cq": analytics.cq,
                    "mq": analytics.mq,
                    "gq": analytics.gq,
                }
                previous_gq = analytics.gq

            # RUN ENGINE
            result = generate_analytics(
                messages=formatted_messages,
                age=age,
                previous_scores=previous_scores,
            )

            result["signals"]["previous_gq"] = previous_gq

            scores = result["quotients"]
            breakdown = result["breakdown"]
            confidence = result["confidence"]

            # ------------------------------------------
            # VOCAB MEMORY UPDATE
            # ------------------------------------------

            signals = result.get("signals", {})
            content_words = signals.get("content_words_list", [])

            if content_words:
                await update_vocabulary_memory(
                    db,
                    child.id,
                    content_words,
                )

            # ------------------------------------------
            # NOVELTY
            # ------------------------------------------

            new_words, reused_words = await compute_real_novelty(
                db,
                child.id,
            )

            signals["new_words_introduced"] = new_words
            signals["new_words_reused"] = reused_words

            if "vq" in breakdown:
                breakdown["vq"]["new_words_introduced"] = new_words
                breakdown["vq"]["new_words_reused"] = reused_words

            # ------------------------------------------
            # 🔥 FINAL VQ CALCULATION (FIXED)
            # ------------------------------------------

            vq_result = await db.execute(
                select(func.count())
                .select_from(ChildVocabularyMemory)
                .where(ChildVocabularyMemory.child_id == child.id)
            )

            vq_count = vq_result.scalar() or 0

            # LOG SCALE SIZE
            vq_size = min(100, math.log(vq_count + 1) * 20)

            # RETENTION
            if new_words > 0:
                retention = min(100, (reused_words / new_words) * 100)
            else:
                retention = 0

            # FINAL SCORE
            vq_score = round(
                0.7 * vq_size +
                0.3 * retention,
                1
            )

            # PREVENT DROP (optional safety)
            if analytics and analytics.vq:
                vq_score = max(vq_score, analytics.vq)

            scores["vq"] = vq_score

            # ------------------------------------------
            # TREND + VELOCITY
            # ------------------------------------------

            trend_percent = 0.0

            if previous_gq and previous_gq != 0:
                trend_percent = round(
                    ((scores["gq"] - previous_gq) / previous_gq) * 100,
                    2,
                )

            velocity = classify_velocity(previous_gq, scores["gq"])

            # ------------------------------------------
            # SAVE ANALYTICS
            # ------------------------------------------

            if not analytics:
                analytics = ChildAnalytics(child_id=child.id)
                db.add(analytics)

            analytics.fq = scores["fq"]
            analytics.vq = scores["vq"]
            analytics.cq = scores["cq"]
            analytics.mq = scores["mq"]
            analytics.gq = scores["gq"]

            analytics.velocity = velocity
            analytics.confidence = confidence
            analytics.trend_percent = trend_percent

            analytics.breakdown_json = {
                "breakdown": breakdown,
                "signals": result.get("signals", {}),
            }

            analytics.algorithm_version = result["algorithm_version"]
            analytics.updated_at = now

            # HISTORY
            existing = await db.execute(
                select(AnalyticsHistory).where(
                    AnalyticsHistory.child_id == child.id,
                    AnalyticsHistory.analytics_date == today
                )
            )

            existing_row = existing.scalars().first()

            if not existing_row:
                history = AnalyticsHistory(
                    child_id=child.id,
                    analytics_date=today,
                    fq=scores["fq"],
                    vq=scores["vq"],
                    cq=scores["cq"],
                    mq=scores["mq"],
                    gq=scores["gq"],
                )
                db.add(history)

            await db.commit()

            print(f"✅ Analytics processed child {child.id}")

        except Exception as e:
            await db.rollback()
            print(f"❌ Analytics failed child {child.id}", str(e))


# =====================================================
# MAIN
# =====================================================

async def run_analytics_batch():

    now = datetime.utcnow()

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Child).where(Child.is_deleted == False)
        )
        children = result.scalars().all()

    print(f"🧠 Running analytics for {len(children)} children")

    tasks = [process_child(child, now) for child in children]

    await asyncio.gather(*tasks)

    print("✅ Analytics batch completed")


if __name__ == "__main__":
    asyncio.run(run_analytics_batch())