import asyncio
import logging

# Load .env before any app imports so settings are populated in local dev.
from dotenv import load_dotenv
load_dotenv()

from app.core.job_queue import JobQueue
from app.core.redis import redis_client
from app.workers.handlers import handle_interaction, handle_toy_status

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("worker")

HANDLERS = {
    "process_child_interaction": handle_interaction,
    "process_toy_status": handle_toy_status,
}

# How often to refresh the heartbeat key (seconds).
# queue_inspector.py checks this key; the TTL is 30s so writing every 10s
# gives three missed writes before the inspector shows MISSING.
HEARTBEAT_INTERVAL = 10
HEARTBEAT_TTL = 30


async def process_job(job: dict):
    job_type = job["type"]
    payload = job["payload"]
    attempt = job.get("attempt", 1)

    handler = HANDLERS.get(job_type)

    if not handler:
        logger.error(f"Unknown job type '{job_type}' — sending to DLQ")
        await JobQueue.push_failed(job)
        return

    logger.info(f"Processing job: {job_type} (attempt {attempt}/{JobQueue.MAX_ATTEMPTS})")

    try:
        await handler(payload)
        logger.info(f"Job done: {job_type}")

    except Exception as e:
        logger.error(f"Job failed: {job_type} attempt {attempt}: {e}")

        if attempt < JobQueue.MAX_ATTEMPTS:
            await JobQueue.retry(job)
            logger.info(
                f"Job re-queued: {job_type} "
                f"(attempt {attempt + 1}/{JobQueue.MAX_ATTEMPTS})"
            )
        else:
            await JobQueue.push_failed(job)
            logger.error(
                f"Job exhausted retries after {attempt} attempts "
                f"— moved to DLQ: {job_type}"
            )


async def worker_loop():
    logger.info("Worker started...")

    while True:
        job = await JobQueue.pop()

        if job:
            await process_job(job)
        else:
            await asyncio.sleep(1)


async def heartbeat_loop():
    """Write a short-TTL key so queue_inspector.py can confirm the worker is alive."""
    while True:
        try:
            await redis_client.set("worker:heartbeat", "1", ex=HEARTBEAT_TTL)
        except Exception as e:
            logger.warning(f"Heartbeat write failed: {e}")
        await asyncio.sleep(HEARTBEAT_INTERVAL)


async def main():
    await asyncio.gather(worker_loop(), heartbeat_loop())


if __name__ == "__main__":
    asyncio.run(main())
