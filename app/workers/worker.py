import asyncio
import logging
import sys

from dotenv import load_dotenv
load_dotenv()

from app.core.job_queue import AIInteractionQueue
from app.core.redis import redis_client
from app.workers.handlers import handle_interaction

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai_worker")

HANDLERS = {
    "process_child_interaction": handle_interaction,
}

HEARTBEAT_INTERVAL = 10
HEARTBEAT_TTL = 30


async def process_job(job: dict):
    job_type = job["type"]
    payload = job["payload"]
    attempt = job.get("attempt", 1)

    handler = HANDLERS.get(job_type)

    if not handler:
        logger.error(f"Unknown job type '{job_type}' — sending to DLQ")
        await AIInteractionQueue.push_failed(job)
        return

    logger.info(f"Processing job: {job_type} (attempt {attempt}/{AIInteractionQueue.MAX_ATTEMPTS})")

    try:
        await handler(payload)
        await AIInteractionQueue.ack(job)
        logger.info(f"Job done: {job_type}")

    except Exception as e:
        logger.error(f"Job failed: {job_type} attempt {attempt}: {e}")

        if attempt < AIInteractionQueue.MAX_ATTEMPTS:
            await AIInteractionQueue.retry(job)
            logger.info(
                f"Job re-queued: {job_type} "
                f"(attempt {attempt + 1}/{AIInteractionQueue.MAX_ATTEMPTS})"
            )
        else:
            await AIInteractionQueue.push_failed(job)
            logger.error(f"Job exhausted retries — moved to DLQ: {job_type}")


async def worker_loop():
    recovered = await AIInteractionQueue.recover_stuck_jobs()
    if recovered:
        logger.info(f"Recovered {recovered} stuck job(s) from previous crash")

    logger.info("AI Worker started...")

    while True:
        job = await AIInteractionQueue.pop()

        if job:
            await process_job(job)
        else:
            await asyncio.sleep(1)


async def heartbeat_loop():
    while True:
        try:
            await redis_client.set("ai_worker:heartbeat", "1", ex=HEARTBEAT_TTL)
        except Exception as e:
            logger.warning(f"Heartbeat write failed: {e}")
        await asyncio.sleep(HEARTBEAT_INTERVAL)


async def main():
    await asyncio.gather(worker_loop(), heartbeat_loop())


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
