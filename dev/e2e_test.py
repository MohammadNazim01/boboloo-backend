#!/usr/bin/env python3
"""
End-to-end MQTT interaction test.

Runs INSIDE the backend container.  Does NOT use dev-only endpoints —
creates test data directly via the ORM so it works in ENVIRONMENT=production.

Usage (from backend EC2):
  docker cp dev/e2e_test.py boboloo_backend:/tmp/e2e_test.py
  docker exec boboloo_backend python /tmp/e2e_test.py
"""

import asyncio
import hashlib
import json
import secrets
import ssl

from app.database.database import AsyncSessionLocal
from app.database.models import APIKey, Child, InteractionSettings, Parent, Toy, ToyStatus
from app.core.redis import redis_client
from gmqtt import Client as MQTTClient
from sqlalchemy import select

DEVICE_ID  = "TEST-E2E-001"
EMQX_HOST  = "172.31.24.62"
EMQX_PORT  = 8883
CA_CERT    = "/app/emqx-ca.pem"
TOPIC_IN   = f"boboloo/toy/{DEVICE_ID}/audio/in"
TOPIC_OUT  = f"boboloo/toy/{DEVICE_ID}/audio/out"
TIMEOUT_S  = 45

_reply_event = asyncio.Event()
_reply_text  = [None]


# ─────────────────────────────────────────
# DB setup — idempotent test fixture
# ─────────────────────────────────────────

async def setup_test_fixture() -> str:
    """Create test parent/child/toy if needed; return a fresh raw API key."""
    async with AsyncSessionLocal() as db:
        # Parent
        r = await db.execute(
            select(Parent).where(Parent.firebase_uid == "e2e-test-parent")
        )
        parent = r.scalar_one_or_none()
        if not parent:
            parent = Parent(
                firebase_uid="e2e-test-parent",
                email="e2e@boboloo.test",
                name="E2E Test Parent",
            )
            db.add(parent)
            await db.flush()
            print("[SETUP] Created test parent")

        # Child
        r = await db.execute(
            select(Child).where(
                Child.parent_id == parent.id, Child.is_deleted == False
            )
        )
        child = r.scalar_one_or_none()
        if not child:
            child = Child(
                parent_id=parent.id,
                name="E2E Child",
                age=6,
                guardian_name="E2E Test Parent",
                onboarding_completed=True,
            )
            db.add(child)
            await db.flush()
            print("[SETUP] Created test child")

        # InteractionSettings (defaults only — child can't answer without these)
        r = await db.execute(
            select(InteractionSettings).where(
                InteractionSettings.child_id == child.id
            )
        )
        if not r.scalar_one_or_none():
            db.add(InteractionSettings(
                child_id=child.id,
                word_complexity=3,
                speech_speed=2,
                question_frequency="balanced",
            ))
            print("[SETUP] Created interaction settings")

        # Toy
        r = await db.execute(
            select(Toy).where(Toy.factory_device_id == DEVICE_ID)
        )
        toy = r.scalar_one_or_none()
        if not toy:
            toy = Toy(factory_device_id=DEVICE_ID)
            db.add(toy)
            await db.flush()
            print("[SETUP] Created test toy")

        # Activate toy and link to child
        toy.owner_parent_id   = parent.id
        toy.active_child_id   = child.id
        toy.status            = ToyStatus.ACTIVE
        toy.is_active         = True

        # Revoke all old keys, issue a fresh one
        old_keys = (await db.execute(
            select(APIKey).where(APIKey.toy_id == toy.id, APIKey.revoked == False)
        )).scalars().all()
        for k in old_keys:
            k.revoked = True

        raw_key  = secrets.token_hex(32)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        db.add(APIKey(key_hash=key_hash, toy_id=toy.id, revoked=False))

        await db.commit()

        # Prime Redis cache so the gateway auth doesn't need a DB lookup
        await redis_client.set(f"toy_key:{key_hash}", str(toy.id), ex=86400)

        print(f"[SETUP] Toy ready: device={DEVICE_ID}  key={raw_key[:8]}…")
        return raw_key


# ─────────────────────────────────────────
# MQTT callbacks
# ─────────────────────────────────────────

def on_connect(client, flags, rc, props):
    print(f"[TOY]   Connected to EMQX (rc={rc})")
    client.subscribe(TOPIC_OUT, qos=0)
    print(f"[TOY]   Subscribed to {TOPIC_OUT}")


async def on_message(client, topic, payload, qos, props):
    _reply_text[0] = payload.decode("utf-8")
    print(f"\n[TOY]   AI reply received on {topic}:")
    print(f"        {_reply_text[0]}\n")
    _reply_event.set()


# ─────────────────────────────────────────
# MQTT test
# ─────────────────────────────────────────

async def run_mqtt_test(toy_api_key: str):
    client = MQTTClient(DEVICE_ID)
    client.set_auth_credentials(DEVICE_ID, toy_api_key)
    client.on_connect = on_connect
    client.on_message = on_message

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.load_verify_locations(CA_CERT)
    ssl_ctx.check_hostname = False

    print(f"[TOY]   Connecting to EMQX {EMQX_HOST}:{EMQX_PORT} (TLS)…")
    await client.connect(EMQX_HOST, EMQX_PORT, ssl=ssl_ctx, keepalive=30)
    await asyncio.sleep(1)  # wait for SUBACK

    question = json.dumps({"text": "Why is the sky blue?"})
    client.publish(TOPIC_IN, question, qos=1)
    print(f"[TOY]   Published → {TOPIC_IN}")
    print(f"[TOY]   Waiting for AI reply (max {TIMEOUT_S}s)…")

    try:
        await asyncio.wait_for(_reply_event.wait(), timeout=TIMEOUT_S)
        print("[E2E]   ✅  PASSED")
    except asyncio.TimeoutError:
        print("[E2E]   ❌  FAILED — no reply received within timeout")

    await client.disconnect()


# ─────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────

async def main():
    toy_api_key = await setup_test_fixture()
    await run_mqtt_test(toy_api_key)


if __name__ == "__main__":
    asyncio.run(main())
