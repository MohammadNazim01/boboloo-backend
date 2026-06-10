#!/usr/bin/env python3
"""
End-to-end MQTT interaction test.

Runs INSIDE the backend container (has gmqtt, emqx-ca.pem, and can reach EMQX
over the VPC internal IP).

Usage (from backend EC2):
  FACTORY_KEY=$(grep '^FACTORY_SECRET_KEY=' .env | cut -d= -f2)
  docker cp dev/e2e_test.py boboloo_backend:/tmp/e2e_test.py
  docker exec -e FACTORY_SECRET_KEY=$FACTORY_KEY boboloo_backend python /tmp/e2e_test.py
"""

import asyncio
import json
import os
import ssl
import urllib.error
import urllib.request

from gmqtt import Client as MQTTClient

DEVICE_ID   = "TEST-E2E-001"
FACTORY_KEY = os.environ["FACTORY_SECRET_KEY"]
EMQX_HOST   = "172.31.24.62"
EMQX_PORT   = 8883
CA_CERT     = "/app/emqx-ca.pem"
API_BASE    = "http://localhost:8080"
TOPIC_IN    = f"boboloo/toy/{DEVICE_ID}/audio/in"
TOPIC_OUT   = f"boboloo/toy/{DEVICE_ID}/audio/out"
TIMEOUT_S   = 45

_reply_event = asyncio.Event()
_reply_text  = [None]


# ─────────────────────────────────────────
# HTTP helpers
# ─────────────────────────────────────────

def _post(path, body=None, extra_headers=None):
    headers = {"Content-Type": "application/json", **(extra_headers or {})}
    data    = json.dumps(body).encode() if body else b""
    req     = urllib.request.Request(
        f"{API_BASE}{path}", data=data, headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def provision_and_get_key() -> str:
    h = {"X-Factory-Secret": FACTORY_KEY}

    print(f"[SETUP] Provisioning {DEVICE_ID}…")
    try:
        r = _post("/api/v1/factory/provision",
                  body={"device_id": DEVICE_ID}, extra_headers=h)
        print(f"        status={r.get('status')}")
    except urllib.error.HTTPError as e:
        # 409 = already provisioned — fine, continue
        if e.code == 409:
            print("        already provisioned — continuing")
        else:
            raise

    print(f"[SETUP] Issuing dev API key…")
    r = _post(f"/api/v1/factory/dev-issue-key/{DEVICE_ID}", extra_headers=h)
    print(f"        toy_uuid={r['toy_uuid']}  key={r['toy_api_key'][:8]}…")
    return r["toy_api_key"]


# ─────────────────────────────────────────
# MQTT callbacks
# ─────────────────────────────────────────

def on_connect(client, flags, rc, props):
    print(f"[TOY]   Connected to EMQX (rc={rc})")
    client.subscribe(TOPIC_OUT, qos=0)
    print(f"[TOY]   Subscribed to {TOPIC_OUT}")


async def on_message(client, topic, payload, qos, props):
    _reply_text[0] = payload.decode("utf-8")
    print(f"\n[TOY]   Reply on {topic}:")
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
    await asyncio.sleep(1)  # let SUBACK arrive

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

if __name__ == "__main__":
    toy_api_key = provision_and_get_key()
    asyncio.run(run_mqtt_test(toy_api_key))
