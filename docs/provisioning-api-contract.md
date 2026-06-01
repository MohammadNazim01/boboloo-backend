# Boboloo Provisioning API Contract

**Audience:** Manufacturer factory operations team  
**Version:** 1.0 | **Date:** 2026-06-01  
**Status:** AUTHORITATIVE — verified against production backend

This document defines the three factory API endpoints. These are the only backend
endpoints the manufacturer interacts with. All other endpoints require different
authentication and are out of scope for manufacturing.

---

## Base URL and Authentication

| Parameter | Value |
|-----------|-------|
| Base URL | Provided separately by Boboloo (staging and production URLs differ) |
| Authentication | HTTP header: `factory-secret: <FACTORY_SECRET_KEY>` |
| Content-Type | `application/json` for all requests |
| TLS | HTTPS only |

The `FACTORY_SECRET_KEY` is provided by Boboloo. It must be kept confidential.
It is the only credential required to call all three endpoints below.
Use the staging key for testing. Use the production key only for real batches.

---

## Endpoints

### POST `/api/v1/factory/provision` — Single toy provision

Creates one toy record in the backend database.

**Idempotent:** Calling this endpoint twice with the same `factory_device_id` is safe.
The second call returns the existing record without creating a duplicate.

#### Request

```
POST /api/v1/factory/provision
factory-secret: <FACTORY_SECRET_KEY>
Content-Type: application/json
```

```json
{
  "factory_device_id": "BBL-0042",
  "firmware_version": "1.0.0",
  "hardware_revision": "A1",
  "batch_id": "BATCH-2026-001"
}
```

| Field | Type | Required | Validation | Description |
|-------|------|----------|------------|-------------|
| `factory_device_id` | string | Yes | 4–32 chars, `[A-Za-z0-9\-]` only | Unique device ID burned to NVS at manufacture |
| `firmware_version` | string | No | Any string | Firmware version flashed to this unit |
| `hardware_revision` | string | No | Any string | PCB hardware revision |
| `batch_id` | string | No | Any string | Production batch identifier |

#### Response — 200 OK

```json
{
  "toy_uuid": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "status": "PROVISIONED"
}
```

| Field | Description |
|-------|-------------|
| `toy_uuid` | Unique backend identifier. Record this — it links the physical device to the backend record. |
| `status` | Always `"PROVISIONED"` for a new or already-provisioned toy. |

#### Error responses

| HTTP Status | Cause | Action |
|-------------|-------|--------|
| 403 | Wrong or missing `factory-secret` header | Check credentials, contact Boboloo |
| 422 | `factory_device_id` format invalid (too short, special chars, etc.) | Fix the device ID |
| 500 | Server error | Retry once; if persists, contact Boboloo |

---

### POST `/api/v1/factory/provision-batch` — Batch toy provision

Creates up to 500 toy records in one call.

**Idempotent:** Already-provisioned IDs are returned as duplicates, not errors.
Re-running with the same device IDs is safe.

**Deduplication:** If the same `factory_device_id` appears multiple times in the
`device_ids` list, it is silently collapsed to a single record. No error is returned.

#### Request

```
POST /api/v1/factory/provision-batch
factory-secret: <FACTORY_SECRET_KEY>
Content-Type: application/json
```

```json
{
  "batch_id": "BATCH-2026-001",
  "device_ids": ["BBL-0001", "BBL-0002", "BBL-0003"],
  "firmware_version": "1.0.0",
  "hardware_revision": "A1"
}
```

| Field | Type | Required | Validation | Description |
|-------|------|----------|------------|-------------|
| `batch_id` | string | Yes | Any string | Production batch identifier |
| `device_ids` | array of string | Yes | 1–500 items; each item 4–32 chars `[A-Za-z0-9\-]` | Device IDs to provision |
| `firmware_version` | string | No | Any string | Applied to all devices in this call |
| `hardware_revision` | string | No | Any string | Applied to all devices in this call |

#### Response — 200 OK

```json
{
  "batch_id": "BATCH-2026-001",
  "requested": 3,
  "created": 3,
  "duplicates": 0,
  "toys": [
    {"device_id": "BBL-0001", "toy_uuid": "f47ac10b-58cc-4372-a567-0e02b2c3d479"},
    {"device_id": "BBL-0002", "toy_uuid": "a3f1b2c3-d4e5-6789-ab01-23456789cdef"},
    {"device_id": "BBL-0003", "toy_uuid": "7c8d9e0f-1234-5678-90ab-cdef01234567"}
  ]
}
```

| Field | Description |
|-------|-------------|
| `batch_id` | Echo of the request `batch_id`. |
| `requested` | Count of unique device IDs after deduplication. |
| `created` | Count of new records created in this call. |
| `duplicates` | Count of device IDs that already existed in the database. |
| `toys` | Array of `{device_id, toy_uuid}` for **newly created** devices only. Already-existing devices do not appear here. |

**Important:** `toys` contains only newly created devices.
If all devices already existed (`duplicates == requested`), `toys` is empty.
Record the `toy_uuid` values — they are the permanent backend identifiers for each physical device.

#### Error responses

| HTTP Status | Cause | Action |
|-------------|-------|--------|
| 403 | Wrong or missing `factory-secret` header | Check credentials, contact Boboloo |
| 422 | One or more `device_ids` fail format validation, or list exceeds 500 items | Check device ID format; split into smaller batches if needed |
| 500 | Server error | Retry; contact Boboloo if persists |

---

### POST `/api/v1/factory/disable` — Disable a toy

Sets a toy's status to `DISABLED` and revokes all API keys associated with it.
Use for defective or damaged units that must not be shipped.

**Idempotent:** Calling this on an already-disabled toy returns `already_disabled`
with no side effects.

#### Request

```
POST /api/v1/factory/disable
factory-secret: <FACTORY_SECRET_KEY>
Content-Type: application/json
```

```json
{
  "factory_device_id": "BBL-0042"
}
```

| Field | Type | Required | Validation |
|-------|------|----------|------------|
| `factory_device_id` | string | Yes | 4–32 chars, `[A-Za-z0-9\-]` only |

#### Response — 200 OK (first disable)

```json
{
  "device_id": "BBL-0042",
  "toy_uuid": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "status": "disabled",
  "keys_revoked": 0
}
```

#### Response — 200 OK (already disabled)

```json
{
  "device_id": "BBL-0042",
  "toy_uuid": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "status": "already_disabled",
  "keys_revoked": 0
}
```

| Field | Description |
|-------|-------------|
| `status` | `"disabled"` on first call, `"already_disabled"` if already in that state. |
| `keys_revoked` | Number of API keys revoked. Will be 0 for factory-provisioned toys (keys are only created when a parent claims the toy). |

#### Error responses

| HTTP Status | Cause | Action |
|-------------|-------|--------|
| 403 | Wrong or missing `factory-secret` header | Check credentials |
| 404 | `factory_device_id` not found — toy was never provisioned | Verify the device ID |
| 422 | `factory_device_id` format invalid | Fix the device ID |

---

## Device ID Format

```
Pattern:  ^[A-Z0-9\-]{4,32}$   (uppercase; API normalises lowercase input)
Min length: 4 characters
Max length: 32 characters
Allowed:  A–Z, 0–9, hyphen (-)
Examples: BBL-0001, TOY-A1B2C3, BATCH2026A001
```

The backend normalises to uppercase before storing. Submitting lowercase is accepted
but the stored value will be uppercase. The NVS `factory_id` on the device must match
exactly (uppercase).

---

## Provisioning Tool

The Boboloo backend team provides `factory_provision.py` — a Python script that
wraps the batch endpoint with retry logic, input validation, and output file generation.

```bash
# Provision all IDs from device_ids.txt
python factory_provision.py --batch-id BATCH-2026-001 --firmware 1.0.0 --hw A1

# Validate input without touching the backend
python factory_provision.py --batch-id BATCH-2026-001 --firmware 1.0.0 --hw A1 --dry-run
```

The script produces:
- `manifest_BATCH-*.csv` — one row per new device, includes `toy_uuid`. Use for box label printing.
- `report_BATCH-*.json` — full audit record. Email to Boboloo after each batch.

See `factory/quick-start.md` and `factory/failure-runbook.md` for full instructions.

---

## Curl Examples

```bash
# Set credentials
export API_URL="https://api.boboloo.com"
export FACTORY_SECRET="your-factory-secret"

# Single provision
curl -s -X POST $API_URL/api/v1/factory/provision \
  -H "factory-secret: $FACTORY_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"factory_device_id":"BBL-0042","firmware_version":"1.0.0","hardware_revision":"A1","batch_id":"BATCH-2026-001"}'

# Batch provision
curl -s -X POST $API_URL/api/v1/factory/provision-batch \
  -H "factory-secret: $FACTORY_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"batch_id":"BATCH-2026-001","device_ids":["BBL-0001","BBL-0002","BBL-0003"],"firmware_version":"1.0.0","hardware_revision":"A1"}'

# Disable a toy
curl -s -X POST $API_URL/api/v1/factory/disable \
  -H "factory-secret: $FACTORY_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"factory_device_id":"BBL-0042"}'
```
