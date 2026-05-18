import json
from app.core.redis import redis_client


class JobQueue:
    """Inbound work queue: MQTT Gateway or HTTP routes → AI Worker."""

    QUEUE_NAME = "job_queue"
    FAILED_QUEUE_NAME = "job_queue_failed"
    MAX_ATTEMPTS = 3

    @staticmethod
    async def push(job_type: str, payload: dict):
        job = {
            "type": job_type,
            "payload": payload,
            "attempt": 1,
        }
        await redis_client.rpush(JobQueue.QUEUE_NAME, json.dumps(job))

    @staticmethod
    async def pop():
        job = await redis_client.lpop(JobQueue.QUEUE_NAME)
        if job:
            return json.loads(job)
        return None

    @staticmethod
    async def retry(job: dict):
        next_attempt = job.get("attempt", 1) + 1
        job["attempt"] = next_attempt
        await redis_client.rpush(JobQueue.QUEUE_NAME, json.dumps(job))

    @staticmethod
    async def push_failed(job: dict):
        await redis_client.rpush(JobQueue.FAILED_QUEUE_NAME, json.dumps(job))


class OutboundQueue:
    """Outbound MQTT messages: AI Worker or API Server → MQTT Gateway → toys.

    The MQTT Gateway is the sole process that reads from this queue and
    publishes to the broker.  All other processes write to it.
    """

    QUEUE_NAME = "outbound_queue"

    @staticmethod
    async def push(topic: str, payload: str, qos: int = 1):
        item = {
            "topic": topic,
            "payload": payload,
            "qos": qos,
        }
        await redis_client.rpush(OutboundQueue.QUEUE_NAME, json.dumps(item))

    @staticmethod
    async def pop(timeout: int = 5) -> dict | None:
        """Blocking pop with timeout (seconds). Returns None on timeout."""
        result = await redis_client.blpop(OutboundQueue.QUEUE_NAME, timeout=timeout)
        if result:
            _, raw = result
            return json.loads(raw)
        return None
