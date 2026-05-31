import asyncio
import logging
import sys

from dotenv import load_dotenv
load_dotenv()

from app.core.job_queue import ToyStatusQueue
from app.core.redis import redis_client
from app.workers.handlers import handle_toy_status

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("status_worker")

HANDLERS = {
    "process_toy_status": handle_toy_status,
}

HEARTBEAT_INTERVAL = 10
HEARTBEAT_TTL = 30


async def process_job(job: dict):
    job_type = job["type"]
    payload = job["payload"]
    attempt = job.get("attempt", 1)

    handler = HANDLERS.get(job_type)

    if not handler:
        logger.error(f"Unknown job type '{job_type}' — dropping")
        await ToyStatusQueue.ack(job)
        return

    logger.debug(f"Processing status job: {job_type}")

    try:
        await handler(payload)
        await ToyStatusQueue.ack(job)

    except Exception as e:
        logger.error(f"Status job failed: {job_type} attempt {attempt}: {e}")

        if attempt < ToyStatusQueue.MAX_ATTEMPTS:
            await ToyStatusQueue.retry(job)
        else:
            await ToyStatusQueue.push_failed(job)


async def worker_loop():
    recovered = await ToyStatusQueue.recover_stuck_jobs()
    if recovered:
        logger.info(f"Recovered {recovered} stuck job(s) from previous crash")

    logger.info("Status Worker started...")

    while True:
        job = await ToyStatusQueue.pop()

        if job:
            await process_job(job)
        else:
            await asyncio.sleep(0.5)


async def heartbeat_loop():
    while True:
        try:
            await redis_client.set("status_worker:heartbeat", "1", ex=HEARTBEAT_TTL)
        except Exception as e:
            logger.warning(f"Heartbeat write failed: {e}")
        await asyncio.sleep(HEARTBEAT_INTERVAL)


async def main():
    await asyncio.gather(worker_loop(), heartbeat_loop())


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
