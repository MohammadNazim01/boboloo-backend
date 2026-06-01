# Boboloo MQTT Integration Contract

**Audience:** Manufacturer firmware team  
**Version:** 1.1 | **Date:** 2026-06-01  
**Status:** AUTHORITATIVE — derived from production backend source code

This document defines every MQTT interface the ESP32 firmware must implement.
All schemas, field names, and QoS values are taken directly from the running backend.
Do not implement anything not specified here without first confirming with the Boboloo backend team.

---

## 1. Broker Connection

| Parameter | Value | Notes |
|-----------|-------|-------|
| Protocol | MQTT 3.1.1 | MQTT 5 not supported |
| Transport | TLS 1.2+ | Plain TCP not accepted on port 8883 |
| Port | 8883 | Standard MQTTS |
| Broker host | Provided separately by Boboloo | Do not hardcode — use sdkconfig |
| TLS CA certificate | Provided by Boboloo as a .pem file | Embed in firmware binary via CMakeLists EMBED_FILES |
| MQTT username | `factory_device_id` (exact, uppercase) | e.g. `BBL-0042` |
| MQTT password | Raw API key (64 lowercase hex characters) | Never hash before sending |
| Client ID | Same as username (`factory_device_id`) | Must match username |
| Clean session | `false` | Persistent session ensures QoS 1 delivery |
| Keep-alive | 60 seconds | |
| Connection timeout | 15 000 ms | Before treating connect as failed |

### Authentication

The broker validates every CONNECT packet by calling the Boboloo backend.
The firmware never calls any auth endpoint directly — authentication is transparent at the broker level.

The backend cross-checks: the API key must belong to the device whose `factory_device_id` matches the MQTT username.
A key from a different device is rejected even if the key is valid.

**CONNACK return codes:**
- `rc=0` — connected, proceed
- `rc=4` — bad username/password → API key is wrong or revoked → erase NVS, restart BLE provisioning
- `rc=5` — not authorised → same action as rc=4

Do NOT erase NVS on a simple network disconnect. Only erase on CONNACK rc=4 or rc=5.

### Per-device ACL

The broker enforces strict topic isolation. Each device can only publish/subscribe to its own topics.
Attempts to access any other topic are rejected silently by the broker.

---

## 2. Topics

All topics follow the pattern: `boboloo/toy/{factory_device_id}/{subtopic}`

The `factory_device_id` segment must be the exact uppercase string from NVS.
Topics are case-sensitive.

| Topic | Direction | QoS | Retained | Purpose |
|-------|-----------|-----|----------|---------|
| `boboloo/toy/{id}/audio/in` | Toy → Backend | 1 | No | Child's spoken question (text) |
| `boboloo/toy/{id}/audio/out` | Backend → Toy | 1 | No | AI-generated reply for TTS playback |
| `boboloo/toy/{id}/status` | Toy → Backend | 1 | No | Heartbeat and telemetry |
| `boboloo/toy/{id}/cmd` | Backend → Toy | 1 | No | OTA firmware update command |

---

## 3. Payload Schemas

All payloads are UTF-8 encoded. Payloads marked **JSON** must be valid JSON objects.
Payloads marked **plain text** are raw UTF-8 strings with no JSON wrapper.

---

### 3.1 `audio/in` — Child question (Toy publishes, QoS 1)

**Format:** JSON

```json
{
  "text": "Why is the sky blue?"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `text` | string | Yes | Transcribed child speech. Max 500 characters. Non-empty. |

Notes:
- The `device_id` is NOT included in the payload — it is derived from the topic by the backend.
- Additional fields (e.g. `battery_level`) in this payload are ignored by the backend.
- If `text` is missing, empty, or exceeds 500 characters the message is silently dropped.
- If the payload is not valid JSON the message is silently dropped.

---

### 3.2 `audio/out` — AI reply (Backend publishes, Toy subscribes at QoS 1)

**Format:** PLAIN UTF-8 TEXT — not JSON

```
The sky looks blue because of a process called Rayleigh scattering.
```

The payload is the raw AI-generated reply string.
Feed it directly to the TTS engine.
Do NOT attempt to JSON-parse this payload.
Max length: 2 000 characters.

---

### 3.3 `status` — Heartbeat and telemetry (Toy publishes, QoS 1)

**Format:** JSON

```json
{
  "battery_level": 80,
  "wifi_signal": -65
}
```

All fields are optional. The backend reads any fields present and ignores the rest.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `battery_level` | integer | No | Battery percentage (0–100) |
| `wifi_signal` | integer | No | WiFi RSSI in dBm (negative integer, e.g. -65) |
| `firmware_version` | string | No | Current firmware version string. Include when reporting OTA result. |
| `ota_status` | string | No | OTA progress or result. See values below. |
| `reason` | string | No | Failure reason when `ota_status` is `failed` or `rollback`. |

**`ota_status` valid values:**

| Value | When to send |
|-------|-------------|
| `downloading` | OTA download has started |
| `verifying` | SHA-256 verification in progress |
| `flashing` | Writing to OTA partition |
| `success` | New firmware booted and MQTT connection confirmed |
| `failed` | OTA failed — include `reason` field |
| `rollback` | New firmware failed to boot, reverted to previous version |

**When to publish:**
- Once on initial MQTT connection after boot
- Every 60 seconds while connected
- On any OTA status change

**Backend behaviour:**
- Updates Redis presence key `toy:status:{device_id}` with a 120-second TTL on every message
- Writes `firmware_version` to the database only when `ota_status` is present in the payload

---

### 3.4 `cmd` — OTA command (Backend publishes, Toy subscribes at QoS 1)

**Format:** JSON

```json
{
  "type": "ota",
  "version": "1.3.0",
  "url": "https://s3.amazonaws.com/bucket/releases/1.3.0/boboloo-1.3.0-signed.bin?X-Amz-Signature=...",
  "sha256": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
  "size": 892416
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | Yes | Always `"ota"` in v1. Ignore commands with unknown types. |
| `version` | string | Yes | Target firmware version (semver). |
| `url` | string | Yes | Pre-signed HTTPS URL for the signed firmware binary. Valid for 30 minutes. Begin download immediately. |
| `sha256` | string | Yes | SHA-256 hex digest of the signed binary (64 lowercase hex characters). Verify after download. |
| `size` | integer | Yes | Expected binary size in bytes. Use for pre-flight partition size check. |

**Important:** The field is `size`, not `file_size`.

---

## 4. Connection Lifecycle

### On every boot (provisioned device)

1. Read `factory_device_id` and `api_key` from NVS
2. Connect to WiFi
3. TCP connect to broker on port 8883
4. TLS handshake — verify broker CA certificate
5. MQTT CONNECT with `username=factory_device_id`, `password=api_key`, `clean_session=false`
6. Wait for CONNACK — if not received within 15 000 ms, treat as timeout
7. On `rc=0`: subscribe to `audio/out` and `cmd` at QoS 1
8. Publish initial status heartbeat
9. Enter runtime loop (heartbeat every 60 s, process incoming messages)

### On MQTT disconnect during runtime

1. Do NOT erase NVS
2. Do NOT restart BLE provisioning
3. Reconnect with exponential backoff: 1 s → 2 s → 4 s → ... → 60 s cap
4. On reconnect: re-subscribe to `audio/out` and `cmd`, publish status heartbeat

### On CONNACK rc=4 or rc=5

1. API key is invalid or revoked
2. Erase the `boboloo` NVS namespace
3. Set `provisioned = 0`
4. Restart device into BLE provisioning mode
5. Wait for parent to re-provision via phone app

---

## 5. OTA Update Flow

1. Receive `cmd` message with `type="ota"`
2. Verify `version` > current running version — if same or lower, discard silently
3. Check OTA partition has enough space for `size` bytes
4. Publish `{"ota_status": "downloading"}` to `status` topic
5. HTTPS GET to `url` — stream into inactive OTA partition
6. Publish `{"ota_status": "verifying"}`
7. Compute SHA-256 of downloaded data — compare to `sha256` field
8. If mismatch: abort, publish `{"ota_status": "failed", "reason": "sha256_mismatch"}`, do not reboot
9. Publish `{"ota_status": "flashing"}`
10. `esp_ota_end()` → `esp_ota_set_boot_partition()`
11. Reboot
12. On successful boot: connect WiFi → connect MQTT → receive CONNACK rc=0
13. Call `esp_ota_mark_app_valid_cancel_rollback()` ONLY after successful CONNACK
14. Publish `{"firmware_version": "1.3.0", "ota_status": "success"}`

**Critical:** Do not call `esp_ota_mark_app_valid_cancel_rollback()` before confirming MQTT connection.
A firmware that breaks network connectivity would be permanently accepted.

### Rollback

If the new firmware fails to connect within timeout:
- `esp_ota_mark_app_invalid_rollback_and_reboot()` or watchdog fires
- Device boots into previous firmware
- Publish `{"ota_status": "rollback", "firmware_version": "<previous_version>"}`
- Backend logs the rollback — does not automatically re-push

---

## 6. Error Handling Summary

| Scenario | Required firmware behaviour |
|----------|---------------------------|
| CONNACK rc=4 or rc=5 | Erase `boboloo` NVS, restart BLE provisioning |
| MQTT disconnect (runtime) | Reconnect with backoff — do not erase NVS |
| WiFi loss (runtime) | Reconnect WiFi, then reconnect MQTT — do not erase NVS |
| OTA SHA-256 mismatch | Abort, do not reboot, publish `ota_status: failed` |
| `factory_device_id` absent at boot | Halt with error LED — do not enter any operational mode |
| `audio/in` payload not valid JSON | Message silently dropped by backend — not a firmware error |
| `audio/out` longer than expected | Feed full string to TTS regardless |

---

## 7. Changelog

| Version | Date | Change |
|---------|------|--------|
| 1.0 | 2026-06-01 | Initial release |
| 1.1 | 2026-06-01 | Corrected audio/out format (plain text, not JSON); corrected heartbeat field names (`firmware_version` not `fw_version`); corrected OTA field name (`size` not `file_size`); clarified audio/in does not require `device_id` in payload |
