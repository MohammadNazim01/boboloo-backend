"""MQTT Gateway — the sole process that owns the broker connection.

Responsibilities:
  - Subscribe to boboloo/toy/+/audio/in and boboloo/toy/+/status
  - On inbound message: validate and push to Redis job_queue
  - Drain Redis outbound_queue and publish replies/commands to toys

All other services (API server, AI Worker) talk to this process via Redis.
No database access happens here — auth is enforced at the broker level by EMQX.
"""

import asyncio
import json
import logging
import signal
import ssl as _ssl

from gmqtt import Client as MQTTClient

from app.core.config import settings
from app.core.job_queue import AIInteractionQueue, ToyStatusQueue, OutboundQueue

logger = logging.getLogger("mqtt_gateway")

MAX_QUESTION_LENGTH = 500

_stop_event = asyncio.Event()


# ─────────────────────────────────────────────
# GMQTT CALLBACKS
# ─────────────────────────────────────────────

def on_connect(client: MQTTClient, flags: int, rc: int, properties):
    logger.info(f"Connected to EMQX broker (rc={rc})")
    client.subscribe("boboloo/toy/+/audio/in", qos=1)
    client.subscribe("boboloo/toy/+/status", qos=1)
    logger.info("Subscribed: boboloo/toy/+/audio/in | boboloo/toy/+/status")


def on_disconnect(client: MQTTClient, packet, exc=None):
    if exc:
        logger.error(f"MQTT disconnected with error: {exc}")
    else:
        logger.warning("MQTT disconnected cleanly")


async def on_message(client: MQTTClient, topic: str, payload: bytes, qos: int, properties):
    try:
        parts = topic.split("/")
        # Valid topic shapes:
        #   boboloo/toy/{device_id}/audio/in  (5 parts)
        #   boboloo/toy/{device_id}/status    (4 parts)
        if len(parts) < 4 or parts[0] != "boboloo" or parts[1] != "toy":
            logger.warning(f"Unexpected topic structure: {topic}")
            return

        device_id = parts[2].upper()
        suffix = "/".join(parts[3:])

        if suffix == "audio/in":
            await _handle_audio_in(device_id, payload, topic)
        elif suffix == "status":
            await _handle_status(device_id, payload)
        else:
            logger.debug(f"Unhandled topic suffix '{suffix}' on {topic}")

    except Exception:
        logger.exception(f"Unhandled error processing message on {topic}")


# ─────────────────────────────────────────────
# INBOUND HANDLERS
# ─────────────────────────────────────────────

async def _handle_audio_in(device_id: str, payload: bytes, topic: str):
    """Parse child question and push to AI worker job queue."""
    try:
        raw = payload.decode("utf-8", errors="replace")
        envelope = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.warning(
            f"Non-JSON payload from {device_id} — "
            "firmware must send: {\"text\": \"<question>\"}"
        )
        return

    question = envelope.get("text", "").strip()

    if not question:
        logger.warning(f"Empty 'text' field from device {device_id}")
        return

    if len(question) > MAX_QUESTION_LENGTH:
        logger.warning(
            f"Question too long from {device_id} "
            f"({len(question)} chars, max {MAX_QUESTION_LENGTH})"
        )
        return

    await AIInteractionQueue.push(
        "process_child_interaction",
        {
            "device_id": device_id,
            "question": question,
        },
    )

    logger.info(f"Queued interaction | device={device_id} | q={question[:60]!r}")


async def _handle_status(device_id: str, payload: bytes):
    """Push toy telemetry/heartbeat to status job queue."""
    try:
        raw = payload.decode("utf-8", errors="replace")
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.warning(f"Non-JSON status payload from {device_id}")
        return

    await ToyStatusQueue.push(
        "process_toy_status",
        {
            "device_id": device_id,
            "data": data,
        },
    )


# ─────────────────────────────────────────────
# OUTBOUND DRAIN LOOP
# ─────────────────────────────────────────────

async def _drain_outbound(client: MQTTClient):
    """Continuously drain outbound_queue and publish to toys.

    Uses blpop (blocking) so it consumes zero CPU when the queue is empty.
    Runs until _stop_event is set.
    """
    logger.info("Outbound drain loop started")

    while not _stop_event.is_set():
        try:
            item = await OutboundQueue.pop(timeout=2)
            if item is None:
                continue

            topic: str = item["topic"]
            payload: str = item["payload"]
            qos: int = item.get("qos", 1)

            client.publish(topic, payload, qos=qos)
            logger.debug(f"Published → {topic}")

        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Outbound drain error — continuing")
            await asyncio.sleep(1)

    logger.info("Outbound drain loop stopped")


# ─────────────────────────────────────────────
# SIGNAL HANDLING
# ─────────────────────────────────────────────

def _handle_signal(sig):
    logger.info(f"Received signal {sig.name} — shutting down gateway")
    _stop_event.set()


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

async def run():
    logger.info("Starting MQTT Gateway...")

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal, sig)

    client = MQTTClient(settings.MQTT_GATEWAY_CLIENT_ID)
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    if settings.MQTT_USERNAME and settings.MQTT_PASSWORD:
        client.set_auth_credentials(settings.MQTT_USERNAME, settings.MQTT_PASSWORD)

    ssl_param: bool | _ssl.SSLContext = False
    if settings.MQTT_USE_TLS:
        # Build an explicit SSL context so we can load our self-signed CA cert.
        # Passing ssl=True would use the system CA bundle, which rejects self-signed certs.
        ssl_ctx = _ssl.create_default_context()
        if settings.MQTT_CA_CERT_PATH:
            ssl_ctx.load_verify_locations(settings.MQTT_CA_CERT_PATH)
            # EMQX default cert has CN=Server, not an IP — disable hostname check
            # while still verifying the cert is signed by our CA.
            ssl_ctx.check_hostname = False
        ssl_param = ssl_ctx

    await client.connect(
        settings.MQTT_HOST,
        settings.MQTT_PORT,
        ssl=ssl_param,
        keepalive=60,
    )

    logger.info(
        f"MQTT Gateway running → {settings.MQTT_HOST}:{settings.MQTT_PORT} "
        f"(TLS={'on' if settings.MQTT_USE_TLS else 'off'})"
    )

    drain_task = asyncio.create_task(_drain_outbound(client))

    # Block here until a shutdown signal arrives.
    await _stop_event.wait()

    logger.info("Shutting down MQTT Gateway...")
    drain_task.cancel()
    try:
        await drain_task
    except asyncio.CancelledError:
        pass

    await client.disconnect()
    logger.info("MQTT Gateway stopped")
