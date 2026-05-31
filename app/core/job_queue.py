import json
from app.core.redis import redis_client


class _BaseQueue:
    QUEUE_NAME: str = ""
    PROCESSING_QUEUE_NAME: str = ""
    FAILED_QUEUE_NAME: str = ""
    MAX_ATTEMPTS: int = 3

    @classmethod
    async def push(cls, job_type: str, payload: dict):
        job = {"type": job_type, "payload": payload, "attempt": 1}
        await redis_client.rpush(cls.QUEUE_NAME, json.dumps(job))

    @classmethod
    async def pop(cls) -> dict | None:
        # Atomically move job to processing queue before returning it.
        # If worker crashes mid-job, the job stays in the processing queue
        # and recover_stuck_jobs() re-queues it on next startup.
        raw = await redis_client.lmove(
            cls.QUEUE_NAME, cls.PROCESSING_QUEUE_NAME, "LEFT", "RIGHT"
        )
        if not raw:
            return None
        job = json.loads(raw)
        job["_raw"] = raw
        return job

    @classmethod
    async def ack(cls, job: dict):
        """Remove job from processing queue after successful handling."""
        raw = job.get("_raw")
        if raw:
            await redis_client.lrem(cls.PROCESSING_QUEUE_NAME, 1, raw)

    @classmethod
    async def retry(cls, job: dict):
        """Ack current attempt and re-queue with incremented attempt counter."""
        await cls.ack(job)
        clean = {k: v for k, v in job.items() if k != "_raw"}
        clean["attempt"] = clean.get("attempt", 1) + 1
        await redis_client.rpush(cls.QUEUE_NAME, json.dumps(clean))

    @classmethod
    async def push_failed(cls, job: dict):
        """Ack and move to dead-letter queue after all retries exhausted."""
        await cls.ack(job)
        clean = {k: v for k, v in job.items() if k != "_raw"}
        await redis_client.rpush(cls.FAILED_QUEUE_NAME, json.dumps(clean))

    @classmethod
    async def recover_stuck_jobs(cls) -> int:
        """Re-queue jobs left in processing queue from a previous crashed worker.
        Called once on worker startup.
        """
        count = 0
        while True:
            raw = await redis_client.lmove(
                cls.PROCESSING_QUEUE_NAME, cls.QUEUE_NAME, "LEFT", "RIGHT"
            )
            if not raw:
                break
            count += 1
        return count


class AIInteractionQueue(_BaseQueue):
    """AI interaction jobs: toy question → OpenAI → DB → MQTT reply."""
    QUEUE_NAME = "ai_interaction_queue"
    PROCESSING_QUEUE_NAME = "ai_interaction_queue:processing"
    FAILED_QUEUE_NAME = "ai_interaction_queue:failed"
    MAX_ATTEMPTS = 3


class ToyStatusQueue(_BaseQueue):
    """Toy telemetry/heartbeat jobs: MQTT status → Redis presence + DB."""
    QUEUE_NAME = "toy_status_queue"
    PROCESSING_QUEUE_NAME = "toy_status_queue:processing"
    FAILED_QUEUE_NAME = "toy_status_queue:failed"
    MAX_ATTEMPTS = 2


class OutboundQueue:
    """Outbound MQTT messages: AI Worker or API Server → MQTT Gateway → toys."""

    QUEUE_NAME = "outbound_queue"

    @staticmethod
    async def push(topic: str, payload: str, qos: int = 1):
        item = {"topic": topic, "payload": payload, "qos": qos}
        await redis_client.rpush(OutboundQueue.QUEUE_NAME, json.dumps(item))

    @staticmethod
    async def pop(timeout: int = 5) -> dict | None:
        result = await redis_client.blpop(OutboundQueue.QUEUE_NAME, timeout=timeout)
        if result:
            _, raw = result
            return json.loads(raw)
        return None
