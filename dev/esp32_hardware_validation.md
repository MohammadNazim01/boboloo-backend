# ESP32 Hardware Validation Plan
## Boboloo — Physical Hardware Phase (from 2026-05-19)

All checks in this document reference the actual firmware source in `firmware/main/`.
Pass/fail criteria are derived from the implemented state machines, timeouts, and
partition layout — not from aspirational spec.

---

## 1. Hardware Bring-Up Checklist

Pre-condition: fresh ESP32-WROOM-32E (8 MB flash), IDF toolchain installed.

| # | Step | Expected | Source |
|---|------|----------|--------|
| 1 | Flash `partitions.csv` layout | `esptool.py` exits 0; `idf.py partition-table` shows factory/ota_0/ota_1/nvs_keys at correct offsets | `firmware/partitions.csv` |
| 2 | Flash factory image to `0x10000` | Image boots without panic | `partitions.csv` factory offset |
| 3 | Monitor serial at 115200 | First log line: `nvs_storage: NVS init OK`; no `ESP_ERROR_CHECK` abort | `main.c:47` |
| 4 | Verify factory partition read-protect via eFuse | `espefuse.py summary` shows WR_DIS bit set for factory partition | `partitions.csv` comments |
| 5 | Confirm `factory_device_id` absent triggers halt | Log shows `"factory_device_id not set — cannot boot"`, LED goes `LED_FAST_RED_FLASH` (R=GPIO4, 250 ms blink) | `main.c:71-73`, `led_control.c:70` |
| 6 | Write `factory` NVS namespace with a test device ID | `nvs_open("factory", NVS_READONLY)` → `nvs_get_str("factory_id")` returns the written value | `main.c:62-67` |
| 7 | Confirm LED GPIO mapping | R=GPIO4, G=GPIO5, B=GPIO6 respond correctly; `LED_SLOW_BLUE_PULSE` (B on 500 ms / off 500 ms, 2 s period) visible on unprov device | `led_control.c:14-16, 44-45` |
| 8 | Confirm reset button GPIO0 is pulled up | Multimeter reads ~3.3 V on GPIO0 with no press | `reset_button.c:46-51` |
| 9 | Confirm `ota_boot_check()` on factory partition logs "no OTA validation needed" | Log: `"Running from factory partition — no OTA validation needed"` | `ota_validation.c:25` |
| 10 | Verify heap size at boot | `esp_get_free_heap_size()` logged; expect ≥120 KB before WiFi/BLE init | (add log call if not present) |

---

## 2. ESP32 Bring-Up Checklist

End-to-end boot sequence verification on first provisioned boot.

| # | Step | Expected | Source |
|---|------|----------|--------|
| 1 | Power on with no NVS data | `nvs_is_provisioned()` → false; log `"Starting BLE provisioning for device <id>"` | `main.c:76-83` |
| 2 | Confirm NimBLE stack starts | Log: `"BLE advertising as <device_id>"` within 3 s of boot | `ble_provisioning.c:307` |
| 3 | Confirm advertising name truncated to 8 chars | BLE scanner shows first 8 chars of `factory_device_id` | `ble_provisioning.c:296` |
| 4 | BLE timeout fires if no connection in 10 min | Log: `"BLE timeout — no provisioning completed"`, LED `LED_FAST_RED_FLASH`, device restarts | `ble_provisioning.c:87-91`, `provisioning_state.h:100` |
| 5 | After successful provision, device boots into runtime path | Log: `"Runtime loop started"`; heartbeat published every 30 s | `main.c:118-124` |
| 6 | Provisioned boot: WiFi connects within 20 s timeout | `wifi_connect()` returns `ESP_OK`; log: `"Connected to <ssid>"` | `wifi_connect.c:113-116`, `provisioning_state.h:101` |
| 7 | MQTT connects within 15 s of WiFi up | `mqtt_connect()` returns `ESP_OK`; log: `"MQTT connected"` | `mqtt_client.c:34`, `provisioning_state.h:102` |
| 8 | WiFi fail → device restarts (no reconnect loop on initial boot path) | Log: `"WiFi failed — restarting"`, `esp_restart()` called | `main.c:97-100` |
| 9 | MQTT fail after WiFi → device restarts | Log: `"MQTT failed — restarting"`, `esp_restart()` called | `main.c:102-105` |
| 10 | `ota_mark_valid()` called on clean provisioned boot | Log: `"Firmware marked VALID — rollback protection cancelled"` (only if OTA pending) | `main.c:112`, `ota_validation.c:63` |

---

## 3. BLE Onboarding Validation Steps

Use `nRF Connect` (mobile) or a custom GATT client. Service UUID: `f0ad0001-0000-4a4b-8c8d-9e0f1a2b3c4d`.

### 3.1 Service Discovery

| # | Action | Expected |
|---|--------|----------|
| 1 | Scan for BLE device | Name = first 8 chars of device ID; flags = General Discoverable + BR/EDR Unsupported |
| 2 | Connect | LED changes from `LED_SLOW_BLUE_PULSE` to `LED_SOLID_BLUE` (B solid); state notification `{state=1, error=0}` |
| 3 | Enumerate GATT characteristics | 6 characteristics present under service UUID (0x0001–0x0006) |
| 4 | Subscribe to `PROV_STATUS` (0x0006) notifications | Subscription accepted; current value readable: `{state=1, error=0}` |

### 3.2 Characteristic Writes

| # | Characteristic | Write value | Expected notification |
|---|---------------|-------------|----------------------|
| 5 | `WIFI_SSID` (0x0002) | Valid SSID ≤32 bytes | No state change yet (only 1 of 3 fields set) |
| 6 | `WIFI_PASS` (0x0003) | Password ≤64 bytes | No state change yet |
| 7 | `TOY_API_KEY` (0x0004) | Exactly 64 hex chars | State→`CREDS_LOADED` (state=2); LED→`LED_FAST_BLUE_PULSE` |
| 8 | `TOY_API_KEY` with wrong length (e.g. 63 chars) | `BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN` returned | State stays at current value |
| 9 | `PROV_CMD` (0x0005) = `0x01` (COMMIT) before all creds | `BLE_ATT_ERR_UNLIKELY` (log: `"COMMIT received before all creds loaded"`) | No state change |
| 10 | `PROV_CMD` = `0x01` after all 3 creds written | State→`COMMITTING` (3) → `WIFI_CONNECTING` (4); LED→`LED_YELLOW_BREATHE` | |

### 3.3 State Machine Progression

| # | Trigger | Expected LED | Expected notification |
|---|---------|-------------|----------------------|
| 11 | WiFi connects | State→`MQTT_CONNECTING` (5); LED→`LED_CYAN_BREATHE` | `{state=5, error=0}` |
| 12 | MQTT connects + heartbeat published | State→`VALIDATING` (6) → after 10 s wait → `READY` (7) | `{state=7, error=0}` |
| 13 | `READY` reached | LED→`LED_SOLID_GREEN`; BLE advertising stops; connection terminated gracefully | |

### 3.4 Error / Rollback Paths

| # | Trigger | Expected |
|---|---------|----------|
| 14 | Wrong WiFi password → `ESP_FAIL` from `wifi_connect()` | State→`ERROR` (8) + `PROV_ERR_WIFI_AUTH` (2); NVS erased; re-advertising starts; LED→`LED_FAST_RED_FLASH` |
| 15 | SSID not found → `ESP_ERR_TIMEOUT` after 20 s | State→`ERROR` + `PROV_ERR_WIFI_TIMEOUT` (4); rollback same as above |
| 16 | MQTT auth failure → `ESP_FAIL` from `mqtt_connect()` | `PROV_ERR_MQTT_AUTH` (5); WiFi disconnected; NVS erased; re-advertising |
| 17 | MQTT timeout → no `MQTT_CONNECTED_BIT` within 15 s | `PROV_ERR_MQTT_TIMEOUT` (6) |
| 18 | Heartbeat published but `mqtt_is_connected()` false after 10 s | `PROV_ERR_MQTT_TIMEOUT` (6); rollback |
| 19 | BLE disconnect mid-provisioning (before COMMIT) | RAM creds cleared (`memset 0`); re-advertising; state→`UNPROVISIONED` | `ble_provisioning.c:372-376` |
| 20 | `PROV_CMD` = `0x02` (CANCEL) | `rollback_and_restart_ble()` called; NVS erased; re-advertising | `ble_provisioning.c:172-173` |

### 3.5 Device Info Read

| # | Action | Expected |
|---|--------|----------|
| 21 | Read `DEVICE_INFO` (0x0001) | JSON: `{"factory_device_id":"<id>","fw_version":"<ver>"}` |
| 22 | Attempt write to `DEVICE_INFO` | `BLE_ATT_ERR_WRITE_NOT_PERMITTED` |

---

## 4. OTA Real-Device Validation Flow

Partition layout: factory (0x10000, 512 KB) / ota_0 (0x90000, 2 MB) / ota_1 (0x290000, 2 MB).

### 4.1 Happy Path

| # | Step | Verify |
|---|------|--------|
| 1 | Device running ota_0; publish OTA cmd to `boboloo/toy/<id>/cmd` with valid URL + SHA256 + version | `handle_cmd_message()` called; log: `"Flashing to partition: ota_1 ..."` |
| 2 | HTTP 200 from firmware URL | Download proceeds; `esp_ota_write()` per 4 KB chunk; log: `"Downloaded <N> bytes"` |
| 3 | SHA256 computed inline via mbedtls | Log: `"SHA256 verified OK"` |
| 4 | `esp_ota_end()` + `esp_ota_set_boot_partition(ota_1)` + `esp_restart()` | Device reboots; runs ota_1 |
| 5 | On next boot: `ota_boot_check()` finds `ESP_OTA_IMG_PENDING_VERIFY` | Log: `"OTA PENDING VERIFY: new firmware on partition 'ota_1'"` |
| 6 | WiFi + MQTT + heartbeat succeed → `ota_mark_valid()` | Log: `"Firmware marked VALID"`; `esp_ota_mark_app_valid_cancel_rollback()` returns `ESP_OK` |
| 7 | Next OTA goes to ota_0 (inactive slot) | `esp_ota_get_next_update_partition(NULL)` returns ota_0 |

### 4.2 Rollback Paths

| # | Failure scenario | Expected |
|---|-----------------|----------|
| 8 | SHA256 mismatch | Log: `"SHA256 mismatch — firmware rejected"`, `esp_ota_abort()`, no boot partition change, old firmware still active |
| 9 | HTTP returns non-200 | Log: `"HTTP status <N> — aborting OTA"`, `esp_ota_abort()` |
| 10 | Download truncated / `read_len < 0` | Log: `"HTTP read error"`, `esp_ota_abort()` |
| 11 | `esp_ota_end()` fails (e.g. image too small) | Error logged, function returns `err`, old boot partition unchanged |
| 12 | New firmware boots but WiFi fails → `ota_mark_valid()` never called | On next reboot: bootloader sees `ESP_OTA_IMG_PENDING_VERIFY` with no `mark_valid` call; watchdog or explicit `ota_rollback()` triggers `esp_ota_mark_app_invalid_rollback_and_reboot()` → falls back to previous valid OTA slot or factory |
| 13 | Both OTA slots invalid | Bootloader falls back to factory partition (recovery image) |

### 4.3 HTTPS / Certificate Validation

| # | Test | Expected |
|---|------|----------|
| 14 | OTA URL uses self-signed cert not in Mozilla bundle | `esp_http_client_open()` fails with TLS error; OTA aborted |
| 15 | OTA URL uses valid Let's Encrypt cert | `esp_crt_bundle_attach` validates; download proceeds |
| 16 | OTA URL is plain HTTP | Configure test to use `http://` — confirm firmware rejects or verify policy in sdkconfig |

---

## 5. MQTT Reconnect Test Plan

Reconnect is handled by `wifi_connect.c:reconnect_task` (exponential backoff 2 s → 60 s cap) plus `mqtt_client.c` MQTT_EVENT_DISCONNECTED handler.

| # | Test | Method | Expected |
|---|------|--------|----------|
| 1 | Drop WiFi AP; device was in runtime loop | Kill router SSID or change password mid-run | Log: `"WiFi lost, reconnecting in 2000 ms"`; `WIFI_FAIL_BIT` set; retry after 2 s |
| 2 | Backoff progression | Log successive reconnect attempts | Delays: 2 s, 4 s, 8 s, 16 s, 32 s, 60 s (cap) — `wifi_connect.c:62-63` |
| 3 | Restore AP | Re-enable router | Log: `"Connected to <ssid>"`; `s_retry_count` reset to 0; `WIFI_CONNECTED_BIT` set |
| 4 | Reconnect path does NOT restart MQTT manually | Verify MQTT reconnect | ESP-IDF MQTT client auto-reconnects on WiFi restore (client still alive); verify `MQTT_EVENT_CONNECTED` log re-appears |
| 5 | MQTT broker bounces (WiFi stays up) | Restart MQTT broker mid-run | Log: `"MQTT disconnected"` then `"MQTT connected"` (IDF client auto-reconnect) |
| 6 | 30 s heartbeat still fires after reconnect | Wait 30 s after WiFi restore | Log: `"boboloo/toy/<id>/status"` publish at 30 s intervals — `main.c:122-124` |
| 7 | `mqtt_is_connected()` returns false during outage | Read via debug command or log | Event group `MQTT_CONNECTED_BIT` cleared on `MQTT_EVENT_DISCONNECTED` |
| 8 | Sustained 1-hour WiFi drop | Overnight soak or automation | Device still connected and publishing heartbeats after WiFi restored |

---

## 6. WiFi Instability Test Plan

Target: `wifi_connect.c` — `PROV_WIFI_MAX_RETRIES = 5`, `PROV_WIFI_CONNECT_TIMEOUT_MS = 20 000 ms`.

| # | Test | Setup | Pass Criterion |
|---|------|-------|---------------|
| 1 | Signal attenuation | Move device 10–15 m from router or use RF attenuator | Connects; RSSI logged; no continuous disconnect/reconnect storm |
| 2 | Retry exhaustion on initial connect | Misconfigure SSID so all 5 retries fail during provisioning | `wifi_connect()` returns after 5 attempts; `WIFI_FAIL_BIT` set; provisioning rolls back with `PROV_ERR_WIFI_TIMEOUT` |
| 3 | PMF handshake | Connect to WPA3-capable AP | `pmf_cfg.capable=true` allows WPA2/WPA3 mixed; verify `IP_EVENT_STA_GOT_IP` fires |
| 4 | WEP/open network | Connect to WEP AP | `WIFI_AUTH_WPA2_PSK` threshold rejects; `WIFI_FAIL_BIT` set; confirm behaviour is intentional |
| 5 | IP lease expiry | Set AP DHCP lease to 60 s | On lease renewal: `IP_EVENT_STA_GOT_IP` fires again; `s_retry_count` resets to 0 |
| 6 | AP reboot (brief outage ~10 s) | Reboot router | Reconnect task fires WIFI_FAIL_BIT → waits 2 s → connects on AP restore |
| 7 | AP channel change | Change AP to non-overlapping channel mid-run | Reconnect occurs within 2 s + scan time |
| 8 | Dual-band AP (2.4 GHz / 5 GHz same SSID) | Confirm device on 2.4 GHz | ESP32 is 2.4 GHz only; verify no incorrect 5 GHz association attempt |
| 9 | Continuous toggle (1 min on / 30 s off, 4 h) | Script AP toggling | No heap leak; device reliably reconnects every cycle |

---

## 7. Memory / Heap Monitoring Plan

### 7.1 Instrumentation Points

Add the following log calls (or enable via menuconfig `CONFIG_FREERTOS_USE_TRACE_FACILITY`):

| Location | What to measure |
|----------|----------------|
| `app_main()` after `nvs_storage_init()` | Baseline free heap (before any subsystem) |
| After `led_control_init()` + `reset_button_init()` | Post-peripheral init heap |
| After `wifi_connect_init()` | Post-WiFi stack heap (WiFi task consumes ~50 KB) |
| After `mqtt_client_init()` | Post-MQTT client init |
| After `ble_provisioning_start()` returns (runtime path) | Post-BLE teardown (NimBLE deinit should free ~30 KB) |
| Inside `reconnect_task` on each iteration | Runtime steady-state heap |
| Inside `on_mqtt_message()` per large payload | Per-message heap delta |

### 7.2 Heap Health Thresholds

| Metric | Minimum acceptable | Tool |
|--------|-------------------|------|
| Free heap at runtime (post-BLE-teardown) | ≥ 80 KB | `esp_get_free_heap_size()` |
| Minimum free heap ever (`esp_get_minimum_free_heap_size()`) | ≥ 40 KB | After 4 h soak |
| Largest free contiguous block | ≥ 16 KB | `heap_caps_get_largest_free_block(MALLOC_CAP_8BIT)` |
| OTA buffer malloc (4 KB) | Must succeed | `ota_update.c:84` — log `"OTA buffer malloc failed"` means OOM |

### 7.3 Soak Tests

| # | Test | Duration | Pass Criterion |
|---|------|----------|---------------|
| 1 | Provisioning → runtime loop idle | 4 h | Heap monotonically stable (±2 KB drift max) |
| 2 | MQTT message burst (simulate 60 msgs/min) | 1 h | No heap growth; no `MQTT_EVENT_ERROR` |
| 3 | WiFi disconnect/reconnect cycles | 50 cycles | No task stack overflow; `uxTaskGetStackHighWaterMark(reconnect_task)` > 256 words |
| 4 | OTA download (2 MB firmware) | Per update | `esp_get_free_heap_size()` returns to pre-OTA baseline after `esp_restart()` |
| 5 | Factory reset button hold × 10 | 10 resets | Each reset erases NVS cleanly; heap at start of each boot equals baseline |

### 7.4 Stack Sizes to Monitor

| Task | Stack allocated | Check call |
|------|----------------|-----------|
| `nimble_host_task` | NimBLE default | `uxTaskGetStackHighWaterMark()` |
| `wifi_reconnect` (2 KB) | `wifi_connect.c:141` | High water mark > 256 words |
| Main task (app_main) | IDF default 3584 B | Monitor during OTA (SHA256 ctx on stack would overflow — verify it's heap-allocated: `ota_update.c:91` uses local ctx, SHA context is ~108 bytes, safe) |

---

## 8. Manufacturing QA Checklist

One unit per device. Automate with `dev/provision_toy.py` as the backend harness.

### 8.1 Pre-Flash

| # | Check | Method |
|---|-------|--------|
| 1 | Flash ID matches W25Q64 (8 MB) | `esptool.py flash_id` → MFR 0xEF, device 0x4017 |
| 2 | Chip type is ESP32 (not S2/S3/C3) | `esptool.py chip_id` |
| 3 | No pre-existing NVS data | `esptool.py read_flash 0x9000 0x6000 nvs_dump.bin` → all 0xFF |
| 4 | Power supply stable at 3.3 V ± 50 mV | Bench meter or factory tester |

### 8.2 Flash Sequence

| # | Step | Tool |
|---|------|------|
| 5 | Flash bootloader + partition table + factory image | `idf.py flash` or `esptool.py write_flash` with offsets from `partitions.csv` |
| 6 | Write per-device `factory_device_id` to `factory` NVS namespace | `nvs_partition_gen.py` → `esptool.py write_flash 0x9000` |
| 7 | Write `nvs_keys` partition (per-device AES-256 key) | Custom tool; key never leaves HSM |
| 8 | Set eFuse WR_DIS for factory partition | `espefuse.py burn_efuse` — irreversible; double-check before burn |
| 9 | Verify `factory_device_id` readable post-flash | Serial log: device ID printed; no red LED |

### 8.3 Functional Smoke Tests (per unit, automated)

| # | Test | Pass |
|---|------|------|
| 10 | Power on → BLE advertising within 5 s | Scanner sees device name |
| 11 | BLE provision with test WiFi + test API key | LED goes green; heartbeat received on backend within 30 s |
| 12 | Factory reset (hold GPIO0 for 5 s) | LED triple-white-flash; device re-advertises |
| 13 | Re-provision after factory reset | Full BLE flow completes to READY again |
| 14 | LED pattern completeness | Each pattern in `led_pattern_t` visually confirmed during provisioning flow |
| 15 | Heartbeat received in Redis backend | `dev/test_backend.py` or `dev/queue_inspector.py` confirms status message |

### 8.4 Pass Criteria Summary

A unit ships only if steps 1–15 all pass without manual intervention. Any `LED_FAST_RED_FLASH` that is not induced by a deliberate error injection is a manufacturing defect.

---

## 9. Pilot Batch Testing Strategy

Target: first batch of N ≤ 50 devices. Goal: validate production firmware + backend before volume ramp.

### 9.1 Batch Composition

| Subset | Count | Purpose |
|--------|-------|---------|
| Lab QA units | 5 | Destructive / edge-case testing (drop tests, power cut during OTA) |
| Internal pilot | 10 | Team members and families; real home WiFi environments |
| Canary (staged rollout) | 10 | First OTA update pushed only to this group |
| Full pilot | 25 | Broader beta users; standard production flow |

### 9.2 Staged OTA Rollout

1. Flash all 50 units with `fw_version = "1.0.0"` at factory.
2. Deploy `"1.0.1"` OTA command only to canary group (10 devices) via backend.
3. Monitor for 48 h: check heartbeat continuity, rollback rate, heap metrics.
4. If rollback rate < 2 %, push to full pilot (25 devices).
5. Lab QA units receive `"1.0.1"` simultaneously and run automated fault injection.

### 9.3 Per-Device Telemetry to Collect During Pilot

| Metric | Collection method |
|--------|-----------------|
| Heartbeat interval (target 30 s) | Backend logs `boboloo/toy/<id>/status` timestamp delta |
| `fw_version` in heartbeat payload | Confirm post-OTA version matches expected |
| WiFi disconnect frequency | Count `"WiFi lost"` log events via serial or MQTT status |
| Heap at runtime (add to heartbeat payload) | Extend heartbeat JSON: `"free_heap": esp_get_free_heap_size()` |
| OTA success/rollback rate | Count `"Firmware marked VALID"` vs `"OTA ROLLBACK triggered"` |
| Provisioning error codes | Log `prov_error_t` per device to backend during pilot onboarding |

### 9.4 Acceptance Gate Before Volume Production

| Gate | Threshold |
|------|-----------|
| Provisioning success rate | ≥ 98 % first-attempt success across all 50 |
| 48 h heartbeat uptime | ≥ 95 % of expected heartbeats received |
| OTA rollback rate | < 2 % on canary batch before broader push |
| Zero crash loops | No device stuck in reboot loop (would show as heartbeat gap > 5 min) |
| Memory stability | No unit's minimum heap drops below 40 KB over 48 h |
| Factory reset recovery | 100 % of devices re-provision cleanly after factory reset |

### 9.5 Failure Escalation

- Any rollback rate > 5 %: halt OTA push, revert backend to previous fw manifest.
- Any provisioning error code `PROV_ERR_NVS_WRITE (1)`: halt batch, inspect NVS partition flash integrity.
- Any `LED_FAST_RED_FLASH` at runtime (not during provisioning error path): pull unit for serial log capture.
- Heap below 40 KB threshold: capture `esp_get_minimum_free_heap_size()` log + heap dump via GDB stub or coredump partition.

---

*Last updated: 2026-05-19. All timeout values, GPIO assignments, UUIDs, and topic strings are cross-referenced to the firmware source in `firmware/main/`.*
