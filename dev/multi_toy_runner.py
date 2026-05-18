#!/usr/bin/env python3
"""
multi_toy_runner.py — Run all provisioned toys from provisioned_toys.json
=========================================================================
Reads dev/provisioned_toys.json (created by provision_toy.py) and launches
a separate toy simulator task for each toy.

Usage:
  python dev/multi_toy_runner.py
  python dev/multi_toy_runner.py --interval 3 --drop-rate 0.05
  python dev/multi_toy_runner.py --stress     # 1 second interval, max concurrency
"""

import argparse
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dev.toy_simulator import ToyState, run_toy, stats_loop


def load_toys(path: str, broker: str, port: int, tls: bool, drop_rate: float):
    with open(path) as f:
        data = json.load(f)
    toys = []
    for entry in data:
        device_id = entry["device_id"]
        api_key   = entry.get("toy_api_key") or entry.get("api_key", "")
        if not api_key:
            print(f"  SKIP {device_id}: no api_key in provisioned_toys.json")
            continue
        toys.append(ToyState(
            device_id=device_id,
            api_key=api_key,
            broker=broker,
            port=port,
            use_tls=tls,
            drop_rate=drop_rate,
        ))
    return toys


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--broker",     default="localhost")
    p.add_argument("--port",       type=int, default=1883)
    p.add_argument("--tls",        action="store_true")
    p.add_argument("--interval",   type=float, default=5.0)
    p.add_argument("--drop-rate",  type=float, default=0.0)
    p.add_argument("--stress",     action="store_true", help="1s interval, no drops")
    p.add_argument("--file",       default="dev/provisioned_toys.json")
    args = p.parse_args()

    if args.stress:
        args.interval  = 1.0
        args.drop_rate = 0.0

    if not os.path.exists(args.file):
        print(f"ERROR: {args.file} not found.")
        print("Run: python dev/provision_toy.py --batch 5  first")
        sys.exit(1)

    toys = load_toys(args.file, args.broker, args.port, args.tls, args.drop_rate)
    if not toys:
        print("No toys loaded.")
        sys.exit(1)

    print(f"Starting {len(toys)} toy(s) — interval={args.interval}s "
          f"drop_rate={args.drop_rate}")

    tasks = [run_toy(t, auto=True, interval=args.interval) for t in toys]
    tasks.append(stats_loop(toys, interval=15.0))

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
