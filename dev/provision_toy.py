#!/usr/bin/env python3
"""
provision_toy.py — Local provisioning helper
=============================================
Automates the full toy setup flow in one command:
  1. Factory-provision the toy (creates DB record + toy_api_key)
  2. Register the toy's API key (creates APIKey record)
  3. Optionally create a test parent + child
  4. Optionally claim the toy under that parent

This replaces the manual multi-step curl workflow for local dev.

Usage:
  FACTORY_SECRET_KEY=<key> python dev/provision_toy.py TEST001
  FACTORY_SECRET_KEY=<key> python dev/provision_toy.py TEST001 TEST002 TEST003
  FACTORY_SECRET_KEY=<key> python dev/provision_toy.py --batch 20
  FACTORY_SECRET_KEY=<key> python dev/provision_toy.py TEST001 --with-claim
"""

import argparse
import json
import os
import sys
import requests

BASE = "http://localhost:8080"

FACTORY_SECRET = os.environ.get("FACTORY_SECRET_KEY", "")
if not FACTORY_SECRET:
    sys.exit(
        "Error: FACTORY_SECRET_KEY environment variable is not set.\n"
        "Usage: FACTORY_SECRET_KEY=<key> python dev/provision_toy.py <DEVICE_ID>"
    )


def provision(device_id: str) -> dict:
    """Factory-provision a toy and immediately issue a dev API key."""
    # Step 1: create the toy record
    resp = requests.post(
        f"{BASE}/api/v1/factory/provision",
        headers={"factory-secret": FACTORY_SECRET, "Content-Type": "application/json"},
        json={"factory_device_id": device_id},
        timeout=10,
    )
    if resp.status_code not in (200, 201):
        print(f"  ERROR provision {resp.status_code}: {resp.text}")
        return {}

    toy_data = resp.json()

    # Step 2: issue a dev API key (dev-only endpoint, returns toy_api_key)
    key_resp = requests.post(
        f"{BASE}/api/v1/factory/dev-issue-key",
        headers={"factory-secret": FACTORY_SECRET},
        params={"factory_device_id": device_id},
        timeout=10,
    )
    if key_resp.status_code == 200:
        toy_data.update(key_resp.json())
    else:
        print(f"  WARN dev-issue-key {key_resp.status_code}: {key_resp.text[:80]}")

    return toy_data


def print_toy(device_id: str, result: dict):
    print(f"\n{'─'*55}")
    print(f"  Device ID   : {device_id}")
    print(f"  Toy UUID    : {result.get('toy_uuid', 'N/A')}")
    print(f"  Claim Token : {result.get('claim_token', 'N/A')}")
    key = result.get("toy_api_key") or result.get("api_key", "")
    print(f"  API Key     : {key}")
    print(f"  Status      : {result.get('status', 'N/A')}")
    print(f"{'─'*55}")
    if key:
        print(f"\n  Simulator command:")
        print(f"  python dev/toy_simulator.py \\")
        print(f"    --device {device_id} \\")
        print(f"    --key {key} \\")
        print(f"    --auto --interval 5")
    print()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("devices", nargs="*", help="Device IDs to provision")
    p.add_argument("--batch",  type=int, default=0, help="Provision N toys as SIM001…SIMN")
    p.add_argument("--prefix", default="SIM", help="Prefix for batch IDs")
    args = p.parse_args()

    if args.batch > 0:
        device_ids = [f"{args.prefix}{i:03d}" for i in range(1, args.batch + 1)]
    elif args.devices:
        device_ids = [d.upper() for d in args.devices]
    else:
        print("Usage: FACTORY_SECRET_KEY=<key> python dev/provision_toy.py TEST001 [TEST002 ...]")
        print("       FACTORY_SECRET_KEY=<key> python dev/provision_toy.py --batch 10")
        sys.exit(1)

    print(f"\nProvisioning {len(device_ids)} toy(s)...\n")

    results = []
    for device_id in device_ids:
        print(f"  Provisioning {device_id}...", end=" ", flush=True)
        result = provision(device_id)
        if result:
            print("OK")
            print_toy(device_id, result)
            results.append({"device_id": device_id, **result})
        else:
            print("FAILED")

    # Write results to file for use by simulator
    out_path = "dev/provisioned_toys.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Saved to {out_path}")

    if len(results) > 1:
        print(f"\nSimulate all {len(results)} toys at once:")
        print(f"  python dev/multi_toy_runner.py")


if __name__ == "__main__":
    main()
