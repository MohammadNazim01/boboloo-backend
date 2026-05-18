#!/usr/bin/env python3
"""
queue_inspector.py — Live Redis queue monitor
=============================================
Shows real-time counts and content of all Boboloo Redis queues.
Replaces raw redis-cli for queue debugging.

Usage:
  python dev/queue_inspector.py           # live monitor, refresh every 2s
  python dev/queue_inspector.py --once    # print once and exit
  python dev/queue_inspector.py --drain   # drain and print every item in all queues
  python dev/queue_inspector.py --inject  # inject a test job into job_queue
"""

import argparse
import json
import os
import sys
import time

import redis

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")

QUEUES = {
    "job_queue":      "Inbound (AI Worker)",
    "outbound_queue": "Outbound (MQTT Gateway)",
    "job_queue_failed": "Dead Letter Queue",
}


def connect():
    r = redis.from_url(REDIS_URL, decode_responses=True)
    r.ping()
    return r


def print_queues(r: redis.Redis):
    os.system("clear" if sys.platform != "win32" else "cls")
    print(f"{'═'*60}")
    print(f"  BOBOLOO QUEUE INSPECTOR   {time.strftime('%H:%M:%S')}")
    print(f"{'═'*60}")

    for q, label in QUEUES.items():
        depth = r.llen(q)
        indicator = "⚠️ " if q == "job_queue_failed" and depth > 0 else "  "
        print(f"\n{indicator}{label}")
        print(f"  Key: {q}  |  Depth: {depth}")

        if depth > 0:
            # Show up to 3 items (peek, don't consume)
            items = r.lrange(q, 0, 2)
            for i, raw in enumerate(items):
                try:
                    parsed = json.loads(raw)
                    # Truncate long values for display
                    summary = json.dumps(parsed, indent=None)[:120]
                    print(f"    [{i}] {summary}{'...' if len(summary) >= 120 else ''}")
                except Exception:
                    print(f"    [{i}] {raw[:100]}")

    # Status keys — worker writes toy:status:{device_id} hashes (120s TTL).
    print(f"\n  Online toys (Redis presence hashes):")
    toy_keys = r.keys("toy:status:*")
    if toy_keys:
        for k in sorted(toy_keys)[:10]:
            data = r.hgetall(k)
            device_id = k.replace("toy:status:", "")
            online = data.get("online", "?")
            fw     = data.get("firmware_version", "?")
            bat    = data.get("battery_level", "?")
            ota    = data.get("ota_status", "")
            ota_str = f"  OTA={ota}" if ota else ""
            print(f"    {device_id}  online={online}  fw={fw}  bat={bat}%{ota_str}")
    else:
        print("    (none)")

    # Worker heartbeat
    hb = r.get("worker:heartbeat")
    print(f"\n  Worker heartbeat: {'ALIVE' if hb else 'MISSING ⚠️'}")
    print(f"{'─'*60}")


def drain_queues(r: redis.Redis):
    print("Draining all queues (items are REMOVED):\n")
    for q, label in QUEUES.items():
        depth = r.llen(q)
        if depth == 0:
            print(f"  {q}: empty")
            continue
        print(f"\n  {q} ({depth} items):")
        for _ in range(depth):
            raw = r.lpop(q)
            if raw:
                try:
                    print(f"    {json.dumps(json.loads(raw), indent=None)}")
                except Exception:
                    print(f"    {raw}")


def inject_test_job(r: redis.Redis, device_id: str = "TEST001"):
    job = {
        "type": "process_child_interaction",
        "payload": {
            "device_id": device_id,
            "question": "Why is the sky blue?",
        },
        "attempt": 1,
    }
    r.rpush("job_queue", json.dumps(job))
    print(f"Injected test job for device {device_id} into job_queue")
    print(f"job_queue depth: {r.llen('job_queue')}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--once",    action="store_true", help="Print once and exit")
    p.add_argument("--drain",   action="store_true", help="Drain all queues")
    p.add_argument("--inject",  action="store_true", help="Inject test job")
    p.add_argument("--device",  default="TEST001",   help="Device ID for inject")
    p.add_argument("--refresh", type=float, default=2.0, help="Refresh interval")
    args = p.parse_args()

    try:
        r = connect()
    except Exception as e:
        print(f"Cannot connect to Redis at {REDIS_URL}: {e}")
        sys.exit(1)

    if args.drain:
        drain_queues(r)
        return

    if args.inject:
        inject_test_job(r, args.device)
        return

    if args.once:
        print_queues(r)
        return

    print(f"Monitoring Redis queues (refresh={args.refresh}s) — Ctrl+C to stop")
    try:
        while True:
            print_queues(r)
            time.sleep(args.refresh)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
