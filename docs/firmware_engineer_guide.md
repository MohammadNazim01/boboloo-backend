# Boboloo — Firmware Engineer Guide

**Audience:** ESP32 firmware engineers  
**Assumes:** Proficiency with ESP-IDF, FreeRTOS, NVS, BLE (NimBLE), MQTT, OTA  
**Version:** 1.0 | **Date:** 2026-05-29

---

## Overview

The ESP32 firmware has four primary responsibilities:

1. **Provisioning** — accept Wi-Fi credentials and API key from parent's phone via BLE
2. **MQTT connectivity** — maintain a persistent TLS connection to the cloud broker
3. **Conversation** — record child speech → STT → publish question → receive answer → TTS
4. **OTA updates** — receive and apply firmware updates delivered over MQTT

The backend is a multi-process cloud system. The firmware only needs to understand the MQTT interface, the provisioning handshake, and the NVS layout. Everything else is handled server-side.

---

## NVS Structure

Two NVS namespaces. The `factory` namespace is written at manufacture and is read-only for firmware. The `boboloo` namespace is written during BLE provisioning.

### Namespace: `factory` (written by manufacturer, read-only)

| Key | Type | Example | Notes |
|-----|------|---------|-------|
| `factory_id` | string | `TOY-A1B2C3` | Permanent device identity. Never overwrite. |

### Namespace: `boboloo` (written during BLE provisioning)

| Key | Type | Example | Notes |
|-----|------|---------|-------|
| `api_key` | string | `xK9mP2nQ8rL...` | Raw API key (~43 chars). Written first. |
| `ssid` | string | `HomeNetwork` | Wi-Fi SSID |
| `pass` | string | `password123` | Wi-Fi password |
| `provisioned` | u8 | `1` | Written **last** as an atomic completion flag |

### Write Order (Power-Loss Safe)

The `provisioned` flag must be written last. On power-loss recovery:
- If `provisioned == 0` (or missing): erase `boboloo` namespace, re-enter BLE provisioning
- If `provisioned == 1`: skip BLE, proceed to Wi-Fi connection

```
Write api_key  →  Write ssid  →  Write pass  →  Write provisioned=1
                                                         ↑
                                              Only set after all others committed
```

### Boot Decision Logic

```
On boot:
  if nvs_get_u8("boboloo", "provisioned") == 1:
    ssid, pass, api_key all present → connect to Wi-Fi
  else:
    start BLE advertising → wait for parent phone
```

---

## BLE Provisioning Flow

The toy acts as a GATT server. The parent app connects, sends credentials, and the toy confirms when ready.

### GATT Service Structure

Define one custom service with the following characteristics:

| Characteristic | UUID (suggest custom) | Properties | Description |
|---------------|----------------------|------------|-------------|
| `CHAR_DEVICE_INFO` | Custom | Read | Returns `factory_id` from NVS |
| `CHAR_WIFI_SSID` | Custom | Write (no response) | Parent writes Wi-Fi SSID |
| `CHAR_WIFI_PASS` | Custom | Write (no response) | Parent writes Wi-Fi password |
| `CHAR_API_KEY` | Custom | Write (no response) | Parent writes raw API key |
| `CHAR_PROV_CMD` | Custom | Write (no response) | Parent writes `"COMMIT"` to trigger save |
| `CHAR_PROV_STATUS` | Custom | Read + Notify | Toy reports provisioning state |

### Provisioning State Machine

```
                          ┌─────────────────┐
          Power on         │  UNPROVISIONED  │
     (provisioned=0) ────► │  BLE advertising│
                           └────────┬────────┘
                                    │ Parent connects
                                    ▼
                           ┌─────────────────┐
                           │  CREDS_LOADING  │ ◄── Parent writes SSID, PASS, API_KEY
                           └────────┬────────┘
                                    │ Parent writes "COMMIT"
                                    ▼
                           ┌─────────────────┐
                           │  COMMITTING     │ ◄── Toy writes to NVS (power-safe order)
                           └────────┬────────┘
                                    │ NVS write complete
                                    ▼
                           ┌─────────────────┐
                           │ WIFI_CONNECTING │ ◄── esp_wifi_connect()
                           └────────┬────────┘
                                    │ IP obtained
                                    ▼
                           ┌─────────────────┐
                           │ MQTT_CONNECTING │ ◄── Connect to broker with API key
                           └────────┬────────┘
                                    │ CONNACK received
                                    ▼
                           ┌─────────────────┐
                           │  VALIDATING     │ ◄── Wait for broker to accept credentials
                           └────────┬────────┘
                                    │ Broker accepts (auth endpoint validates key)
                                    ▼
                           ┌─────────────────┐
                           │     READY       │ ◄── Notify CHAR_PROV_STATUS = "READY"
                           └─────────────────┘
```

**BLE Timeout:** If the parent app does not complete provisioning within 10 minutes, the toy should stop advertising and restart. This conserves battery.

**On READY:** Notify `CHAR_PROV_STATUS = "READY"`. Parent app shows "Toy connected" screen.

**Failure handling:** If MQTT auth fails (broker rejects the key), notify `CHAR_PROV_STATUS = "AUTH_FAILED"` and erase the `boboloo` NVS namespace so the toy re-enters provisioning on next boot.

---

## MQTT Architecture

### Broker Connection Parameters

| Parameter | Value | Notes |
|-----------|-------|-------|
| Host | From `MQTT_HOST` env var (currently `broker.hivemq.com`) | Provided by Boboloo team |
| Port | `8883` | TLS only — no plaintext connections |
| TLS | Required | Use CA bundle for HiveMQ/EMQX |
| Client ID | `factory_id` (e.g. `TOY-A1B2C3`) | Must match username |
| Username | `factory_id` (e.g. `TOY-A1B2C3`) | Must match Client ID |
| Password | Raw API key (from NVS `boboloo/api_key`) | Never hash before sending |
| Keepalive | 60 seconds | Server expects this interval |
| Clean session | `false` (persistent session) | Ensures QoS 1 message delivery |

**Authentication happens at the broker.** When the toy connects, the broker makes an HTTP call to the Boboloo backend to validate the username/password pair. The backend hashes the password and looks it up in the database. The toy does not call any auth HTTP endpoint directly.

### Required MQTT Topics

```
Inbound (toy PUBLISHES):
  boboloo/toy/{factory_id}/audio/in     ← send child's question
  boboloo/toy/{factory_id}/status       ← send telemetry / heartbeat

Outbound (toy SUBSCRIBES):
  boboloo/toy/{factory_id}/audio/out    ← receive AI reply
  boboloo/toy/{factory_id}/cmd          ← receive OTA and other commands
```

All substitutions use the exact `factory_id` value from NVS (uppercase). The broker enforces ACLs — the toy cannot publish or subscribe to any other topic.

### Connection Sequence on Boot

```
1. Read factory_id, api_key from NVS
2. Connect to broker:
     host: MQTT_HOST, port: 8883, TLS: on
     client_id: factory_id
     username: factory_id
     password: api_key (raw, as stored in NVS)
     keepalive: 60
3. Wait for CONNACK:
     rc=0 → connected, proceed
     rc=4 → bad credentials → see Credential Rejection below
     rc=5 → not authorized → see Credential Rejection below
4. Subscribe to:
     boboloo/toy/{factory_id}/audio/out  (QoS 1)
     boboloo/toy/{factory_id}/cmd        (QoS 1)
5. Publish initial status heartbeat:
     boboloo/toy/{factory_id}/status  {"battery_level": XX, "wifi_signal": XX}
```

### Credential Rejection (rc=4 or rc=5)

This happens when the API key in NVS is invalid or has been revoked.

```
CONNACK rc=4 or rc=5:
  1. Erase NVS boboloo namespace
  2. Notify CHAR_PROV_STATUS = "AUTH_FAILED" (if BLE still active)
  3. Set provisioned = 0
  4. Reboot into BLE provisioning mode
  5. Wait for parent to re-provision via phone app
```

### Reconnection Behavior

```
On disconnect (network drop, broker restart, etc.):
  1. Wait: exponential backoff starting at 1s, max 60s
     Attempt 1: 1s
     Attempt 2: 2s
     Attempt 3: 4s
     ...
     Attempt 6+: 60s
  2. Re-read credentials from NVS (they may have changed)
  3. Re-connect using same parameters
  4. Re-subscribe to audio/out and cmd topics
  5. Publish status heartbeat once connected

DO NOT erase NVS on simple disconnect — only on auth rejection.
```

---

## Message Formats

### Publishing a Child Question

Topic: `boboloo/toy/{factory_id}/audio/in`  
QoS: 1  
Payload (JSON):

```json
{
  "text": "Why is the sky blue?"
}
```

The backend validates:
- Payload must be valid JSON
- `text` field must be present and non-empty
- `text` must be 500 characters or fewer

If the payload is malformed, the gateway drops it silently. The toy receives no error acknowledgment.

### Publishing Telemetry (Heartbeat)

Topic: `boboloo/toy/{factory_id}/status`  
QoS: 1  
Frequency: Every 60 seconds while connected, plus once on initial connect  
Payload (JSON):

```json
{
  "battery_level": 80,
  "wifi_signal": -65
}
```

All fields are optional but recommended. Additional fields during OTA:

```json
{
  "battery_level": 75,
  "wifi_signal": -60,
  "firmware_version": "1.1.0",
  "ota_status": "downloading"
}
```

Valid `ota_status` values: `downloading`, `verifying`, `flashing`, `success`, `failed`, `rollback`

### Receiving an AI Reply

Topic: `boboloo/toy/{factory_id}/audio/out`  
Payload: **Plain UTF-8 text string** (not JSON)

```
The sky is blue because sunlight bounces off tiny air molecules!
```

On receipt: feed directly to TTS engine. The string is already tuned for the child's age and vocabulary level by the AI.

### Receiving an OTA Command

Topic: `boboloo/toy/{factory_id}/cmd`  
Payload (JSON):

```json
{
  "type": "ota",
  "version": "1.2.3",
  "url": "https://s3.amazonaws.com/bucket/releases/1.2.3/boboloo-1.2.3-signed.bin?X-Amz-Signature=...",
  "sha256": "a3f8d2e1b4c96f0e7d5a2b8c1f3e9d4a...",
  "size": 1048576
}
```

The URL is a pre-signed HTTPS link. It expires 30 minutes after it was generated. If the toy receives the command and cannot start downloading within 30 minutes, the URL will be stale — the backend must re-push.

---

## OTA Implementation

### Full OTA Flow

```
1. Receive OTA command on /cmd topic
2. Parse JSON: type, version, url, sha256, size

3. Report status: {"ota_status": "downloading"}

4. Download firmware:
   - HTTPS GET to the pre-signed URL
   - Stream into the inactive OTA partition (ota_0 or ota_1, whichever is not active)
   - Use esp_https_ota_perform() or equivalent streaming write

5. Verify download:
   - Compute SHA-256 of downloaded bytes
   - Compare to sha256 field from command
   - If mismatch: abort, report {"ota_status": "failed", "reason": "sha256_mismatch"}

6. Report status: {"ota_status": "flashing"}

7. Finalize:
   - esp_ota_end() — completes the write
   - esp_ota_set_boot_partition() — marks new partition as next boot
   
8. Reboot

9. On successful boot:
   - Toy connects to Wi-Fi and MQTT
   - Call esp_ota_mark_app_valid_cancel_rollback() AFTER successful MQTT CONNACK
   - Report: {"firmware_version": "1.2.3", "ota_status": "success"}

10. On failed boot:
    - esp-idf watchdog or esp_ota_mark_app_invalid_rollback_and_reboot()
    - Device reboots into previous partition
    - Report: {"ota_status": "rollback"}
```

### Critical OTA Rule

`esp_ota_mark_app_valid_cancel_rollback()` must only be called **after** the device has confirmed it can reach the MQTT broker. If called before network validation, a firmware that breaks Wi-Fi connectivity would be permanently accepted and the device would be bricked.

Recommended validation sequence:
```
Boot new firmware
  → Connect to Wi-Fi (timeout 30s)
  → Connect to MQTT broker (timeout 30s)
  → Receive CONNACK rc=0
  → Call esp_ota_mark_app_valid_cancel_rollback()
  → Publish OTA success report
```

If either connection fails within timeout: do not mark valid → automatic rollback on watchdog.

### Rollback Scenario

If the new firmware fails to boot or connect:

```
esp-idf detects watchdog timeout or explicit rollback call
  → Boots into otadata-tracked previous partition
  → Toy comes online with old firmware
  → Reports: {"ota_status": "rollback", "firmware_version": "1.1.0"}
  → Backend logs the rollback, does not re-push automatically
```

---

## HTTP Runtime Alternative (Testing / Non-MQTT Devices)

For testing without MQTT, the backend also exposes HTTP endpoints that the toy can call directly. This is not the production path but is useful for development.

**Send a question:**
```
POST https://api.boboloo.com/api/v1/toy/runtime/ask
Header: X-Toy-Key: <raw_api_key>
Body: {"question": "Why do dogs wag their tails?", "battery_level": 80}

Response: {"conversation_id": "uuid", "status": "processing"}
```

**Poll for the answer:**
```
GET https://api.boboloo.com/api/v1/toy/runtime/latest-answer/{conversation_id}
Header: X-Toy-Key: <raw_api_key>

Response: {"answer": "Dogs wag their tails to show they are happy!", "ready": true}
```

**Send a heartbeat:**
```
POST https://api.boboloo.com/api/v1/toy/runtime/heartbeat
Header: X-Toy-Key: <raw_api_key>

Response: {"status": "alive"}
```

The `X-Toy-Key` header uses the **raw** API key (exactly as stored in NVS), not a hash.

---

## Authentication Details

### How API Key Authentication Works (HTTP Path)

1. Toy sends raw API key in `X-Toy-Key` header
2. Backend computes `SHA-256(raw_key)`
3. Looks up the hash in Redis cache (fast path, ~99% hit rate)
4. If Redis miss: queries PostgreSQL database
5. Validates: toy status must be `ACTIVE`, `is_active` must be `true`
6. Returns toy identity to the route handler

### How API Key Authentication Works (MQTT Path)

1. Toy sends raw API key as MQTT `password`
2. EMQX broker intercepts the CONNECT packet
3. EMQX calls Boboloo backend: `POST /internal/mqtt/auth` with username + password
4. Backend validates key (same SHA-256 lookup as HTTP path)
5. Backend also cross-checks: key owner's `factory_device_id` must match the MQTT `username`
6. If valid: EMQX returns `CONNACK rc=0` with per-device ACL
7. If invalid: EMQX returns `CONNACK rc=4`

**The toy never calls any auth endpoint directly.** Auth is transparent at the broker level.

### Key Rotation

When a parent rotates their toy's API key (via the phone app):
- Old key is immediately deleted from Redis cache (revocation is instant)
- Old key is marked revoked in the database
- New raw key is sent to the parent's phone
- Parent re-provisions the toy via BLE with the new key

On next MQTT connect attempt with old key: broker returns `rc=4 → AUTH_FAILED` → toy enters BLE provisioning mode.

---

## Rate Limits

The backend rate-limits toy requests on the HTTP path:

| Limit | Value | Window |
|-------|-------|--------|
| Questions per toy | 20 requests | 60 seconds |

If the toy exceeds this, the backend returns HTTP `429 Too Many Requests`. The toy should back off for at least 10 seconds before retrying.

There is no equivalent rate limit on the MQTT path — the broker does not enforce this.

---

## Required Backend Interactions Summary

| Event | Action | Protocol |
|-------|--------|---------|
| Factory produces toy | Nothing — backend team handles provisioning | N/A |
| Parent sets up toy | Parent phone connects via BLE, sends credentials | BLE GATT |
| Toy boots (provisioned) | Connect to MQTT broker | MQTT over TLS |
| Child speaks | Publish question to `audio/in` | MQTT |
| AI answers | Receive reply from `audio/out` | MQTT |
| Periodic heartbeat | Publish to `status` | MQTT (every 60s) |
| OTA command received | Download, flash, reboot, report result | HTTPS + MQTT |
| API key rotated | Disconnect → enter BLE provisioning | BLE GATT |
| Network drop | Exponential backoff reconnect | MQTT |

---

## Assumptions

1. ESP-IDF v5.x or later (for `esp_https_ota` and NimBLE support).
2. The TLS CA certificate for the MQTT broker is bundled into the firmware binary (not fetched at runtime).
3. The on-device STT is handled by a separate chip or DSP; the firmware receives a text string and publishes it.
4. `factory_id` is always uppercase ASCII. The backend normalizes uppercase on its end as well.
5. The toy does not need to parse or act on `audio/out` payloads beyond routing them to TTS — no JSON parsing needed for replies.
6. BLE is disabled once MQTT connection is established (power saving). It re-enables only when provisioning is needed.
