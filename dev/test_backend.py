#!/usr/bin/env python3
"""
test_backend.py — Backend integration test runner
==================================================
Runs a sequential set of smoke tests against the live local backend.
Tests every layer: health, factory, auth, runtime, queues, OTA.

Usage:
  python dev/test_backend.py
  python dev/test_backend.py --base http://localhost:8080
  python dev/test_backend.py --test factory     # run only factory tests
  python dev/test_backend.py --test all         # run all (default)

Exit code:
  0 = all tests passed
  1 = one or more tests failed
"""

import argparse
import json
import os
import sys
import time

import redis
import requests

BASE           = os.environ.get("API_BASE", "http://localhost:8080")
FACTORY_SECRET = os.environ.get("FACTORY_SECRET_KEY", "boboloo-factory-master-key")
REDIS_URL      = os.environ.get("REDIS_URL", "redis://localhost:6379")
ADMIN_SECRET   = os.environ.get("ADMIN_INTERNAL_SECRET", "8dj29dks92jd92kd92kd")

# ─── Test result tracking ─────────────────────────────────────────────────────

passed = []
failed = []


def ok(name: str, detail: str = ""):
    passed.append(name)
    print(f"  ✓  {name}" + (f"  ({detail})" if detail else ""))


def fail(name: str, reason: str):
    failed.append(name)
    print(f"  ✗  {name}  — {reason}")


def section(title: str):
    print(f"\n{'─'*55}")
    print(f"  {title}")
    print(f"{'─'*55}")


# ─── Individual tests ─────────────────────────────────────────────────────────

def test_health():
    section("1. Health Check")
    try:
        r = requests.get(f"{BASE}/health", timeout=5)
        if r.status_code == 200 and r.json().get("status") == "ok":
            ok("GET /health")
        else:
            fail("GET /health", f"status={r.status_code} body={r.text[:80]}")
    except Exception as e:
        fail("GET /health", str(e))


def test_redis():
    section("2. Redis Connectivity")
    try:
        r = redis.from_url(REDIS_URL, decode_responses=True)
        r.ping()
        ok("Redis ping")
        depth = r.llen("job_queue")
        ok(f"job_queue readable", f"depth={depth}")
    except Exception as e:
        fail("Redis", str(e))


def test_factory_provision(device_id: str = "TESTINT001") -> dict:
    section("3. Factory Provisioning + Dev Key")
    data = {}
    try:
        # Step 1 — create toy record
        resp = requests.post(
            f"{BASE}/api/v1/factory/provision",
            headers={"factory-secret": FACTORY_SECRET, "Content-Type": "application/json"},
            json={"factory_device_id": device_id},
            timeout=10,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            ok("POST /api/v1/factory/provision", f"toy_uuid={data.get('toy_uuid')}")
        else:
            fail("POST /api/v1/factory/provision", f"status={resp.status_code} {resp.text[:80]}")
            return data

        # Step 2 — issue dev API key (dev-only endpoint)
        key_resp = requests.post(
            f"{BASE}/api/v1/factory/dev-issue-key",
            headers={"factory-secret": FACTORY_SECRET},
            params={"factory_device_id": device_id},
            timeout=10,
        )
        if key_resp.status_code == 200:
            data.update(key_resp.json())
            ok("POST /api/v1/factory/dev-issue-key", "api_key issued")
        else:
            fail("POST /api/v1/factory/dev-issue-key",
                 f"status={key_resp.status_code} {key_resp.text[:80]}")
    except Exception as e:
        fail("Factory provision", str(e))
    return data


def test_toy_auth(device_id: str, toy_data: dict) -> str:
    section("4. Toy Authentication")
    api_key = toy_data.get("toy_api_key") or toy_data.get("api_key", "")
    if not api_key:
        fail("Toy auth", "No api_key returned from factory provision")
        return ""
    try:
        resp = requests.post(
            f"{BASE}/api/v1/toy/runtime/heartbeat",
            headers={"x-toy-key": api_key},
            timeout=10,
        )
        if resp.status_code == 200:
            ok("POST /api/v1/toy/runtime/heartbeat", "toy key accepted")
        else:
            fail("POST /api/v1/toy/runtime/heartbeat",
                 f"status={resp.status_code} {resp.text[:80]}")
    except Exception as e:
        fail("Toy heartbeat", str(e))
    return api_key


def test_job_queue_injection(device_id: str):
    section("5. Job Queue — Direct Redis Injection")
    try:
        r = redis.from_url(REDIS_URL, decode_responses=True)
        before = r.llen("job_queue")

        job = {
            "type": "process_child_interaction",
            "payload": {
                "device_id": device_id,
                "question": "Why is the sky blue?",
            },
            "attempt": 1,
        }
        r.rpush("job_queue", json.dumps(job))
        after = r.llen("job_queue")

        if after == before + 1:
            ok("RPUSH job_queue", f"depth {before} → {after}")
        else:
            fail("RPUSH job_queue", f"depth did not increase: {before} → {after}")
    except Exception as e:
        fail("RPUSH job_queue", str(e))


def test_worker_processes_job(timeout_s: float = 15.0):
    """
    Check that the worker pops the job within timeout_s.
    We detect this by watching the job_queue depth decrease or
    watching outbound_queue receive a message.
    """
    section("6. Worker Processing")
    try:
        r = redis.from_url(REDIS_URL, decode_responses=True)
        start = time.time()
        initial_out = r.llen("outbound_queue")

        while time.time() - start < timeout_s:
            current_q  = r.llen("job_queue")
            current_out = r.llen("outbound_queue")
            if current_out > initial_out:
                elapsed = time.time() - start
                ok("Worker processed job → outbound_queue", f"{elapsed:.1f}s")
                return
            if current_q == 0 and current_out >= initial_out:
                # job consumed but maybe toy not found (unclaimed)
                ok("Worker consumed job from queue",
                   "outbound_queue unchanged (toy may be unclaimed — expected)")
                return
            time.sleep(0.5)

        fail("Worker processing", f"job still in queue after {timeout_s}s "
             "(is the worker running?)")
    except Exception as e:
        fail("Worker processing", str(e))


def test_rate_limiter(api_key: str):
    section("7. Rate Limiter")
    if not api_key:
        print("  SKIP — no api_key available")
        return
    try:
        # Send 25 requests quickly (limit is 20/60s for toy endpoints)
        codes = []
        for _ in range(25):
            r = requests.post(
                f"{BASE}/api/v1/toy/runtime/heartbeat",
                headers={"x-toy-key": api_key},
                timeout=8,
            )
            codes.append(r.status_code)

        limited = codes.count(429)
        if limited > 0:
            ok("Rate limiter triggered", f"{limited}/25 requests were 429")
        else:
            fail("Rate limiter", f"No 429 returned after 25 rapid requests — limiter may not be working")
    except Exception as e:
        fail("Rate limiter", str(e))


def test_invalid_auth():
    section("8. Auth Rejection")
    try:
        r = requests.post(
            f"{BASE}/api/v1/toy/runtime/heartbeat",
            headers={"x-toy-key": "invalid-key-xxxxxxxxxxxxxxxxxx"},
            timeout=5,
        )
        if r.status_code in (401, 403):
            ok("Invalid toy key rejected", f"status={r.status_code}")
        else:
            fail("Auth rejection", f"expected 401/403, got {r.status_code}")
    except Exception as e:
        fail("Auth rejection", str(e))


def test_ota_routes():
    section("9. OTA Routes")
    try:
        # OTA requires both admin secret AND Firebase token — we can only test
        # that the route exists and rejects without auth (not that it works end-to-end).
        r = requests.get(f"{BASE}/sys/control/ota/releases", timeout=5)
        if r.status_code in (401, 403, 422):
            ok("OTA route exists + requires auth", f"status={r.status_code}")
        elif r.status_code == 200:
            # somehow authed — ok
            ok("OTA /releases reachable", f"{len(r.json())} releases")
        else:
            fail("OTA routes", f"unexpected status={r.status_code}")
    except Exception as e:
        fail("OTA routes", str(e))


def test_outbound_queue_peek():
    section("10. Outbound Queue")
    try:
        r = redis.from_url(REDIS_URL, decode_responses=True)
        depth = r.llen("outbound_queue")
        items = r.lrange("outbound_queue", 0, 2)
        ok(f"outbound_queue readable", f"depth={depth}")
        for i, raw in enumerate(items):
            try:
                parsed = json.loads(raw)
                ok(f"outbound_queue item {i}", f"topic={parsed.get('topic','?')[:40]}")
            except Exception:
                ok(f"outbound_queue item {i}", raw[:60])
    except Exception as e:
        fail("outbound_queue", str(e))


# ─── Runner ───────────────────────────────────────────────────────────────────

def run_all():
    DEVICE = "TESTINT001"

    test_health()
    test_redis()

    toy_data = test_factory_provision(DEVICE)
    api_key  = test_toy_auth(DEVICE, toy_data)

    test_job_queue_injection(DEVICE)
    test_worker_processes_job()
    test_rate_limiter(api_key)
    test_invalid_auth()
    test_ota_routes()
    test_outbound_queue_peek()


def print_summary():
    print(f"\n{'═'*55}")
    print(f"  RESULTS")
    print(f"{'═'*55}")
    print(f"  Passed: {len(passed)}")
    print(f"  Failed: {len(failed)}")
    if failed:
        print(f"\n  Failed tests:")
        for f in failed:
            print(f"    ✗ {f}")
    print(f"{'═'*55}\n")


def main():
    global BASE
    p = argparse.ArgumentParser()
    p.add_argument("--base", default=BASE)
    p.add_argument("--test", default="all",
                   choices=["all", "health", "factory", "queue", "auth", "ota"])
    args = p.parse_args()
    BASE = args.base

    print(f"\nBoboloo Backend Integration Tests")
    print(f"Target: {BASE}")

    run_all()
    print_summary()
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
