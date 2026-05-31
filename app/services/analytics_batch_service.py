import asyncio
import logging
from datetime import datetime, date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import AsyncSessionLocal

from app.database.models import (
    Child,
    Conversation,
    Message,
    ChildAnalytics,
    AnalyticsHistory,
    ChildVocabularyMemory,
    ChildStreak,
)

from app.services.analytics_engine.engine import (
    generate_analytics,
)

logger = logging.getLogger(__name__)


# =====================================================
# UPDATE VOCABULARY MEMORY
# =====================================================

async def update_vocabulary_memory(
    db: AsyncSession,
    child_id,
    words,
):

    today = date.today()

    unique_words = {
        w.lower()
        for w in words
    }

    # -----------------------------------------
    # FETCH EXISTING
    # -----------------------------------------

    result = await db.execute(
        select(ChildVocabularyMemory).where(
            ChildVocabularyMemory.child_id == child_id
        )
    )

    existing_records = {
        r.word: r
        for r in result.scalars().all()
    }

    # -----------------------------------------
    # UPSERT MEMORY
    # -----------------------------------------

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
# UPDATE CONVERSATION STREAK
# =====================================================

async def update_conversation_streak(
    db: AsyncSession,
    child_id,
    talked_today: bool,
    today: date,
):
    """Update the child's daily conversation streak.

    Called for every child on every batch run, before the message-count
    gate, so even a single message keeps the streak alive.

    Only writes to DB when the child talked today — children who were
    silent get no write at all. The API computes "broken" at read time
    from last_conversation_date, so no nightly reset writes are needed.
    """

    if not talked_today:
        return

    yesterday = today - timedelta(days=1)

    result = await db.execute(
        select(ChildStreak).where(ChildStreak.child_id == child_id)
    )
    streak = result.scalar_one_or_none()

    if streak is None:
        db.add(
            ChildStreak(
                child_id=child_id,
                current_streak=1,
                longest_streak=1,
                last_conversation_date=today,
                streak_started_at=today,
            )
        )
        return

    # Already processed today — idempotent, nothing to do.
    if streak.last_conversation_date == today:
        return

    if streak.last_conversation_date == yesterday:
        # Consecutive day — extend.
        streak.current_streak += 1
    else:
        # Gap — reset and start a new run.
        streak.current_streak = 1
        streak.streak_started_at = today

    streak.longest_streak = max(streak.longest_streak, streak.current_streak)
    streak.last_conversation_date = today


# =====================================================
# PROCESS SINGLE CHILD
# =====================================================

async def process_child(
    child,
    now,
):

    today = date.today()

    async with AsyncSessionLocal() as db:

        try:

            # =====================================
            # CHECK TODAY'S CONVERSATION EXISTS
            # =====================================

            conv_result = await db.execute(
                select(Conversation).where(
                    Conversation.child_id == child.id,
                    Conversation.conversation_date == today,
                )
            )
            conversation = conv_result.scalar_one_or_none()

            # =====================================
            # UPDATE CONVERSATION STREAK
            # Runs before the message-count gate so
            # even 1 message keeps the streak alive.
            # =====================================

            await update_conversation_streak(
                db=db,
                child_id=child.id,
                talked_today=conversation is not None,
                today=today,
            )

            # =====================================
            # FETCH TODAY MESSAGES
            # =====================================

            messages_result = await db.execute(

                select(Message)

                .join(
                    Conversation,
                    Message.conversation_id
                    == Conversation.id
                )

                .where(
                    Conversation.child_id == child.id,

                    Conversation.conversation_date
                    == today,
                )

                .order_by(Message.created_at)
            )

            raw_messages = (
                messages_result.scalars().all()
            )

            # =====================================
            # USER TEXT ONLY
            # =====================================

            text_list = [

                m.content

                for m in raw_messages

                if str(m.role).lower().endswith("user")
            ]

            # =====================================
            # MINIMUM DATA CHECK (user msgs only)
            # Streak is already committed above —
            # only vocabulary analytics needs 3+ msgs.
            # =====================================

            if len(text_list) < 3:

                await db.commit()
                logger.debug(f"Analytics skipped — not enough messages for child {child.id}")

                return

            # =====================================
            # FETCH EXISTING VOCAB
            # =====================================

            vocab_result = await db.execute(

                select(
                    ChildVocabularyMemory.word
                )

                .where(
                    ChildVocabularyMemory.child_id
                    == child.id
                )
            )

            existing_words = [

                row[0]

                for row in vocab_result.all()
            ]

            # =====================================
            # RUN ANALYTICS ENGINE
            # =====================================

            result = generate_analytics(
                text_list=text_list,
                existing_words=existing_words,
            )

            vocabulary = result.get(
                "vocabulary",
                {}
            )

            # =====================================
            # UPDATE VOCAB MEMORY
            # =====================================

            unique_words = vocabulary.get(
                "UniqueWordsList",
                []
            )

            if unique_words:

                await update_vocabulary_memory(
                    db=db,
                    child_id=child.id,
                    words=unique_words,
                )

            # =====================================
            # FETCH / CREATE ANALYTICS
            # =====================================

            analytics_result = await db.execute(

                select(ChildAnalytics)

                .where(
                    ChildAnalytics.child_id
                    == child.id
                )
            )

            analytics = (
                analytics_result
                .scalars()
                .first()
            )

            if not analytics:

                analytics = ChildAnalytics(
                    child_id=child.id,
                    updated_at=now,
                )

                db.add(analytics)

            # =====================================
            # SAVE ANALYTICS JSON
            # =====================================

            # vocabulary_service returns Python sets — convert to lists for JSONB
            vocabulary_json = {
                "TotalWordsCount": vocabulary.get("TotalWordsCount", 0),
                "UniqueWordsCount": vocabulary.get("UniqueWordsCount", 0),
                "NewWordsCount": vocabulary.get("NewWordsCount", 0),
                "UniqueWordsList": list(vocabulary.get("UniqueWordsList", [])),
                "NewWordsList": list(vocabulary.get("NewWordsList", [])),
            }

            analytics.breakdown_json = {
                "vocabulary": vocabulary_json
            }

            analytics.updated_at = now

            # =====================================
            # HISTORY SNAPSHOT
            # =====================================

            history_result = await db.execute(

                select(AnalyticsHistory)

                .where(
                    AnalyticsHistory.child_id
                    == child.id,

                    AnalyticsHistory.analytics_date
                    == today,
                )
            )

            history = (
                history_result
                .scalars()
                .first()
            )

            if not history:

                history = AnalyticsHistory(
                    child_id=child.id,
                    analytics_date=today,
                )

                db.add(history)

            history.breakdown_json = {
                "vocabulary": vocabulary_json
            }

            # =====================================
            # COMMIT
            # =====================================

            await db.commit()

            logger.info(f"Analytics processed child {child.id}")

        except Exception as e:

            await db.rollback()

            logger.error(f"Analytics failed for child {child.id}: {e}", exc_info=True)


# =====================================================
# MAIN BATCH
# =====================================================

async def run_analytics_batch():

    now = datetime.utcnow()

    async with AsyncSessionLocal() as db:

        result = await db.execute(

            select(Child)

            .where(
                Child.is_deleted == False
            )
        )

        children = (
            result.scalars().all()
        )

    logger.info(f"Analytics batch started — {len(children)} children")

    # =====================================
    # CONTROLLED CONCURRENCY
    # max 10 children at once — prevents
    # DB connection exhaustion at scale
    # =====================================

    semaphore = asyncio.Semaphore(10)

    async def bounded(child):
        async with semaphore:
            await process_child(child, now)

    tasks = [bounded(child) for child in children]

    await asyncio.gather(*tasks)

    logger.info("Analytics batch completed")


# =====================================================
# ENTRYPOINT
# =====================================================

if __name__ == "__main__":

    asyncio.run(
        run_analytics_batch()
    )