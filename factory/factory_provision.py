#!/usr/bin/env python3
"""
factory_provision.py — Boboloo production provisioning tool
============================================================
Reads a list of factory_device_id values, registers each toy in the
Boboloo backend, and writes a manifest CSV and JSON audit report.

The backend endpoint is idempotent: running this script twice with the
same IDs is safe. Already-provisioned devices are returned with their
existing toy_uuid and counted as duplicates, not errors.

Requirements
------------
  Python 3.8+
  pip install requests

Credentials
-----------
Set in .env.factory (same directory as this script) or as environment
variables before running:

  BOBOLOO_API_URL      https://api.boboloo.com
  BOBOLOO_FACTORY_SECRET  <key supplied by Boboloo>

Usage
-----
  # Provision all IDs in device_ids.txt:
  python factory_provision.py --batch-id BATCH-2026-001 --firmware 1.2.0 --hw A1

  # Validate input without hitting the API:
  python factory_provision.py --batch-id BATCH-2026-001 --firmware 1.2.0 --hw A1 --dry-run

  # Use a different ID file:
  python factory_provision.py --batch-id BATCH-2026-001 --firmware 1.2.0 --hw A1 --ids device_ids.txt

Exit codes
----------
  0  All devices provisioned (or already existed)
  1  One or more devices failed, or a configuration error occurred
"""

import argparse
import csv
import json
import os
import re
import sys
import time
import datetime
from pathlib import Path

try:
    import requests
    from requests.exceptions import ConnectionError, Timeout, HTTPError
except ImportError:
    print("ERROR: 'requests' is not installed.")
    print("       Run:  pip install requests")
    sys.exit(1)


# ─── Constants ────────────────────────────────────────────────────────────────

VERSION          = "1.0.0"
BATCH_SIZE       = 500          # backend hard limit per call
MAX_RETRIES      = 3
RETRY_BACKOFF_S  = [2, 5, 10]   # wait before each retry attempt
REQUEST_TIMEOUT  = 30           # seconds

# Must match backend pattern: ^[A-Za-z0-9\-]{4,32}$
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9\-]{4,32}$")


# ─── Env loading ──────────────────────────────────────────────────────────────

def load_env(script_dir: Path) -> None:
    """Load .env.factory from the script directory into os.environ."""
    env_path = script_dir / ".env.factory"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key   = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# ─── Validation ───────────────────────────────────────────────────────────────

def validate_id(device_id: str) -> str | None:
    """Return an error string if the ID is invalid, else None."""
    if not device_id:
        return "empty string"
    if len(device_id) < 4:
        return f"too short ({len(device_id)} chars, minimum 4)"
    if len(device_id) > 32:
        return f"too long ({len(device_id)} chars, maximum 32)"
    if not DEVICE_ID_RE.match(device_id):
        return "contains invalid characters (allowed: A-Z, 0-9, hyphen)"
    return None


def load_and_validate_ids(ids_file: Path) -> tuple[list[str], list[tuple[str, str]]]:
    """
    Read IDs from file. Returns (valid_ids, invalid_rows).
    invalid_rows is a list of (raw_line, error_reason).
    Blank lines and lines starting with # are silently skipped.
    IDs are normalised to uppercase.
    Duplicates within the file are collapsed (order-preserving).
    """
    if not ids_file.exists():
        print(f"\nERROR: ID file not found: {ids_file}")
        print(f"       Create a file with one factory_device_id per line.")
        sys.exit(1)

    raw_lines = ids_file.read_text().splitlines()
    seen: dict[str, bool] = {}
    invalid: list[tuple[str, str]] = []

    for raw in raw_lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        normalised = stripped.upper()
        err = validate_id(normalised)
        if err:
            invalid.append((raw, err))
            continue
        seen[normalised] = True

    return list(seen.keys()), invalid


# ─── API client ───────────────────────────────────────────────────────────────

class BobolooFactoryClient:
    def __init__(self, api_url: str, factory_secret: str):
        self.base    = api_url.rstrip("/")
        self.headers = {
            "factory-secret": factory_secret,
            "Content-Type":   "application/json",
        }

    def health_check(self) -> bool:
        try:
            resp = requests.get(
                f"{self.base}/health",
                timeout=10,
            )
            return resp.status_code == 200 and resp.json().get("status") == "ok"
        except Exception:
            return False

    def provision_batch(
        self,
        device_ids: list[str],
        batch_id: str,
        firmware_version: str,
        hardware_revision: str,
    ) -> dict:
        """
        Call /provision-batch. Raises on non-retryable errors.
        Returns the parsed response body on success.
        """
        payload = {
            "batch_id":          batch_id,
            "device_ids":        device_ids,
            "firmware_version":  firmware_version,
            "hardware_revision": hardware_revision,
        }
        last_exc = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = requests.post(
                    f"{self.base}/api/v1/factory/provision-batch",
                    headers=self.headers,
                    json=payload,
                    timeout=REQUEST_TIMEOUT,
                )

                # 403 — wrong secret; no point retrying
                if resp.status_code == 403:
                    print(f"\nERROR: Factory secret rejected (HTTP 403).")
                    print(f"       Check BOBOLOO_FACTORY_SECRET in .env.factory.")
                    sys.exit(1)

                # 422 — validation error; no point retrying
                if resp.status_code == 422:
                    detail = resp.json().get("detail", resp.text)
                    raise ValueError(f"Validation error from backend: {detail}")

                resp.raise_for_status()
                return resp.json()

            except (ConnectionError, Timeout) as exc:
                last_exc = exc
                if attempt < MAX_RETRIES - 1:
                    wait = RETRY_BACKOFF_S[attempt]
                    print(f"  (connection error, retrying in {wait}s — attempt {attempt+1}/{MAX_RETRIES})")
                    time.sleep(wait)

            except HTTPError as exc:
                status = exc.response.status_code if exc.response else "?"
                # 5xx are transient; 4xx (except 403/422 handled above) are not
                if exc.response is not None and 500 <= exc.response.status_code < 600:
                    last_exc = exc
                    if attempt < MAX_RETRIES - 1:
                        wait = RETRY_BACKOFF_S[attempt]
                        print(f"  (server error {status}, retrying in {wait}s — attempt {attempt+1}/{MAX_RETRIES})")
                        time.sleep(wait)
                else:
                    raise

        raise RuntimeError(
            f"Failed after {MAX_RETRIES} attempts. Last error: {last_exc}"
        )


# ─── Output writers ───────────────────────────────────────────────────────────

def write_manifest(
    path: Path,
    toys: list[dict],
    batch_id: str,
    firmware_version: str,
    hardware_revision: str,
    timestamp: str,
) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["device_id", "toy_uuid", "batch_id", "firmware_version", "hardware_revision", "provisioned_at"])
        for t in toys:
            w.writerow([
                t["device_id"],
                t["toy_uuid"],
                batch_id,
                firmware_version,
                hardware_revision,
                timestamp,
            ])


def write_report(
    path: Path,
    report: dict,
) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    script_dir = Path(__file__).parent.resolve()
    load_env(script_dir)

    parser = argparse.ArgumentParser(
        description="Boboloo factory provisioning tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--batch-id",  required=True, help="Batch identifier e.g. BATCH-2026-001")
    parser.add_argument("--firmware",  required=True, help="Firmware version e.g. 1.2.0")
    parser.add_argument("--hw",        required=True, help="Hardware revision e.g. A1")
    parser.add_argument("--ids",       default="device_ids.txt", help="Path to device ID file (default: device_ids.txt)")
    parser.add_argument("--dry-run",   action="store_true", help="Validate input and print plan without hitting the API")
    args = parser.parse_args()

    print(f"\nBoboloo Factory Provisioning Tool v{VERSION}")
    print("=" * 55)

    # ── Credentials ────────────────────────────────────────────
    api_url         = os.environ.get("BOBOLOO_API_URL", "").strip()
    factory_secret  = os.environ.get("BOBOLOO_FACTORY_SECRET", "").strip()

    if not api_url:
        print("\nERROR: BOBOLOO_API_URL is not set.")
        print("       Add it to .env.factory or set it as an environment variable.")
        sys.exit(1)
    if not factory_secret:
        print("\nERROR: BOBOLOO_FACTORY_SECRET is not set.")
        print("       Add it to .env.factory or set it as an environment variable.")
        sys.exit(1)

    # ── Load and validate IDs ───────────────────────────────────
    ids_file = Path(args.ids)
    if not ids_file.is_absolute():
        ids_file = Path.cwd() / ids_file

    device_ids, invalid = load_and_validate_ids(ids_file)

    print(f"\n  Batch ID    : {args.batch_id}")
    print(f"  Firmware    : {args.firmware}")
    print(f"  Hardware    : {args.hw}")
    print(f"  ID file     : {ids_file}")
    print(f"  Device IDs  : {len(device_ids)} valid")

    if invalid:
        print(f"\n  WARNING: {len(invalid)} line(s) in the ID file failed validation and will be skipped:")
        for raw, reason in invalid[:10]:
            print(f"    \"{raw}\" — {reason}")
        if len(invalid) > 10:
            print(f"    ... and {len(invalid) - 10} more")
        print()
        answer = input("  Continue with the valid IDs only? [y/N]: ").strip().lower()
        if answer != "y":
            print("\n  Aborted. Fix the ID file and re-run.")
            sys.exit(1)

    if not device_ids:
        print("\nERROR: No valid device IDs found in the file.")
        sys.exit(1)

    if args.dry_run:
        print(f"\n  DRY RUN — would provision {len(device_ids)} device(s) in "
              f"{(len(device_ids) - 1) // BATCH_SIZE + 1} chunk(s). No API calls made.")
        print("\n  First 5 IDs:")
        for did in device_ids[:5]:
            print(f"    {did}")
        if len(device_ids) > 5:
            print(f"    ... and {len(device_ids) - 5} more")
        print("\n  Dry run complete. Re-run without --dry-run to provision.")
        sys.exit(0)

    # ── Pre-flight: health check ────────────────────────────────
    client = BobolooFactoryClient(api_url, factory_secret)
    print(f"\n  Connecting to {api_url} ...", end=" ", flush=True)
    if not client.health_check():
        print("FAILED")
        print(f"\nERROR: Could not reach the Boboloo backend at {api_url}")
        print("       Check your internet connection and BOBOLOO_API_URL.")
        sys.exit(1)
    print("OK")

    # ── Provision in chunks ─────────────────────────────────────
    timestamp  = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    all_toys:  list[dict]  = []
    failed_ids: list[str]  = []
    total_created    = 0
    total_duplicates = 0
    chunks = [device_ids[i:i+BATCH_SIZE] for i in range(0, len(device_ids), BATCH_SIZE)]

    print(f"\n  Provisioning {len(device_ids)} device(s) in {len(chunks)} chunk(s)...\n")

    for idx, chunk in enumerate(chunks, 1):
        label = f"  Chunk {idx}/{len(chunks)} ({len(chunk)} devices)"
        print(f"{label}...", end=" ", flush=True)
        try:
            data = client.provision_batch(
                chunk,
                args.batch_id,
                args.firmware,
                args.hw,
            )
            created    = data.get("created", 0)
            duplicates = data.get("duplicates", 0)
            toys       = data.get("toys", [])

            all_toys.extend(toys)
            total_created    += created
            total_duplicates += duplicates

            if duplicates:
                print(f"OK  ({created} new, {duplicates} already existed)")
            else:
                print(f"OK  ({created} new)")

        except ValueError as exc:
            # Validation error — non-retryable, log and skip chunk
            print(f"FAILED (validation)")
            print(f"         {exc}")
            failed_ids.extend(chunk)

        except Exception as exc:
            print(f"FAILED")
            print(f"         {exc}")
            failed_ids.extend(chunk)

    # ── Write outputs ───────────────────────────────────────────
    out_dir = Path.cwd()
    csv_name  = f"manifest_{args.batch_id}_{timestamp}.csv"
    json_name = f"report_{args.batch_id}_{timestamp}.json"
    csv_path  = out_dir / csv_name
    json_path = out_dir / json_name

    report = {
        "tool_version":     VERSION,
        "batch_id":         args.batch_id,
        "firmware_version": args.firmware,
        "hardware_revision": args.hw,
        "timestamp_utc":    timestamp,
        "api_url":          api_url,
        "total_requested":  len(device_ids),
        "total_created":    total_created,
        "total_duplicates": total_duplicates,
        "total_failed":     len(failed_ids),
        "provisioned":      all_toys,
        "failed_ids":       failed_ids,
    }

    write_manifest(csv_path, all_toys, args.batch_id, args.firmware, args.hw, timestamp)
    write_report(json_path, report)

    # ── Summary ─────────────────────────────────────────────────
    print()
    print("=" * 55)
    print(f"  Total requested : {len(device_ids)}")
    print(f"  New             : {total_created}")
    print(f"  Already existed : {total_duplicates}")
    print(f"  Failed          : {len(failed_ids)}")
    print(f"  Manifest CSV    : {csv_path}")
    print(f"  Audit report    : {json_path}")
    print("=" * 55)

    if failed_ids:
        print(f"\n  FAILED IDs ({len(failed_ids)}):")
        for fid in failed_ids:
            print(f"    {fid}")
        print("\n  ACTION REQUIRED — see failure-runbook.md for next steps.")
        print("  Email the JSON report to your Boboloo contact before continuing.")
        sys.exit(1)

    print(f"\n  All devices provisioned successfully.")
    print(f"  Email the CSV and JSON report to your Boboloo contact now.")
    sys.exit(0)


if __name__ == "__main__":
    main()
