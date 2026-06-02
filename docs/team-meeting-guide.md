# Team Meeting Discussion Guide
## Boboloo — Product Readiness Review

**Date:** 2026-06-02  
**Led by:** Backend Owner  
**Attendees:** Tech Lead, Firmware Engineers, Hardware/Manufacturer Team, Product Stakeholders

---

> **How to use this guide**
>
> This is your working document for the meeting. Read the pre-meeting notes first —
> they contain findings from the live production environment that inform your questions.
> Use the question sections as a checklist. Do not skip the red flags.

---

## Pre-Meeting Notes — What You Already Know

Before the meeting starts, you have confirmed the following from the production server.
These are not hypotheticals — they are live production issues:

| Finding | Severity | Detail |
|---------|----------|--------|
| MQTT running on public HiveMQ, port 1883, TLS off | 🔴 CRITICAL | Every API key is transmitted in cleartext. No auth enforcement. Any device can connect. |
| MQTT auth/ACL (EMQX) not operational | 🔴 CRITICAL | Codebase was built for EMQX HTTP auth plugin. Production broker is public HiveMQ — it does not call your auth endpoints. |
| S3 firmware bucket is empty string | 🔴 CRITICAL | OTA system is completely non-functional in production. `S3_FIRMWARE_BUCKET=""` |
| CORS_ORIGINS=* on production | 🟠 HIGH | Any website can call the backend API. Needs to be locked to specific domains. |
| ENVIRONMENT=production | ✅ Good | `/dev-issue-key` is correctly blocked. |

Raise items 1, 2, and 3 yourself at the start of the meeting. Do not wait for someone else to surface them.

---

## 1. Firmware

**Audience:** Tech Lead, Firmware Engineers

### 1.1 Audio Subsystem

**Question to ask:**  
*"What is the current implementation status of audio capture and playback? Can we see a demo — child speaks, question publishes, AI answer plays back?"*

**Why it matters:**  
Audio is the core product feature. `main.c` has `/* TODO: forward to audio subsystem */` at the point where `audio/out` messages are received. Without audio, there is no product. Everything else is infrastructure waiting for this.

**Good answer:**  
Implemented, working on bench hardware, demo available. Timeline for integration with MQTT path confirmed.

**Bad answer:**  
"We're working on it," "it's almost done," or any answer without a specific date and a working demo. Audio being unimplemented while manufacturing conversations are happening is a planning failure.

**Red flags:**  
- No demo available
- Timeline measured in weeks with no date
- STT (speech-to-text) chip or library not yet selected
- Audio capture works but TTS playback doesn't or vice versa

**Follow-up questions:**
- Which STT engine are you using on-device? Is it running locally on the ESP32 or on a separate chip?
- What is the format of the transcribed text the firmware will publish? (Our backend expects `{"text": "..."}` as a JSON string, max 500 chars.)
- What TTS engine handles playback? Audio out arrives as plain text — who converts it to audio?
- What is the current latency from child speaking to audio playing back? What is the target?
- Have you tested this end-to-end with the backend in any environment?

---

### 1.2 OTA Command Handler

**Question to ask:**  
*"Is `handle_cmd_message` fully implemented? Walk me through what happens when the toy receives an OTA command on the `cmd` topic."*

**Why it matters:**  
In the reference firmware, `handle_cmd_message` is declared as `extern` with no definition in any provided file. If this function is not implemented, OTA updates are non-functional. OTA is a post-launch safety net — without it, any firmware bug after shipping requires a physical recall.

**Good answer:**  
Implemented, tested. Toy receives the `cmd` JSON payload, parses `type`, `version`, `url`, `sha256`, `size`, downloads over HTTPS, verifies SHA-256, writes to OTA partition, reboots, calls `esp_ota_mark_app_valid_cancel_rollback()` only after confirmed MQTT reconnect.

**Bad answer:**  
"It's in progress," or confirmation that it's not yet implemented.

**Red flags:**  
- `esp_ota_mark_app_valid_cancel_rollback()` called before MQTT reconnect is confirmed (device would accept a broken firmware permanently)
- SHA-256 verification skipped or done before download completes
- OTA update runs without confirming partition size first

**Follow-up questions:**
- Has OTA been tested end-to-end? What firmware version did you update from and to?
- What happens if WiFi drops mid-download? Does the download restart or the whole OTA abort?
- What happens if the SHA-256 doesn't match? Does the device remain operational on the old firmware?
- Does the firmware report OTA status back via the `status` topic? (`ota_status`: downloading → verifying → flashing → success/failed)

---

### 1.3 Factory Device ID

**Question to ask:**  
*"Walk me through exactly how factory_device_id gets written to the device at manufacture. Show me the command."*

**Why it matters:**  
The `factory_device_id` in NVS is the only link between a physical device and the backend database. If this step is missing, wrong, or inconsistent, the entire provisioning flow fails. There is currently no factory tooling in the repo for this step.

**Good answer:**  
A specific, scripted process: NVS partition CSV generated per device, flashed to the correct NVS partition offset. Tool is scripted so a factory operator cannot make a mistake.

**Bad answer:**  
"We do it manually," "we haven't figured that out yet," or any unscripted process.

**Red flags:**  
- Writing to the wrong NVS namespace (must be `factory`, key `factory_id`)
- No verification step after writing (i.e. no readback to confirm)
- Process is manual and error-prone at scale
- The `boboloo` namespace being written instead of `factory`

**Follow-up questions:**
- How do you verify the written ID before the device leaves the station?
- Does your process guarantee the ID in NVS matches the label on the device?
- How do you generate the ID sequence? Is there a risk of collision across batches?
- What happens if the NVS write fails mid-process?

---

### 1.4 NVS Encryption

**Question to ask:**  
*"Is NVS encryption enabled in the production firmware build? Is the `nvs_keys` partition being used?"*

**Why it matters:**  
The API key is stored in plaintext NVS by default. Anyone with a USB cable and `esptool.py` can read it off a physical device. The partition table includes `nvs_keys` for encryption, but the code calls `nvs_flash_init()` — not the encrypted variant.

**Good answer:**  
NVS encryption is enabled for production builds using `nvs_flash_secure_init_partition()`. Keys are generated per-device at manufacture and written to the `nvs_keys` partition before first NVS use.

**Bad answer:**  
"We'll add it later," or no awareness that the issue exists.

**Red flags:**  
- Dismissing the concern ("users won't have UART access")
- No plan to enable it before manufacturing

**Follow-up questions:**
- Is this enabled in the production build config (`sdkconfig.prod`)?
- How are the per-device NVS encryption keys generated and stored?

---

### 1.5 Secure Boot

**Question to ask:**  
*"Is secure boot enabled? Has the ECDSA signing keypair been generated and the private key secured?"*

**Why it matters:**  
The signing infrastructure (`gen_signing_key.sh`, `sign_firmware.sh`) exists in the repo. Without eFuse-burned secure boot, anyone can flash arbitrary firmware to a toy. This is a children's device.

**Good answer:**  
Secure boot enabled in production sdkconfig. Keypair generated, private key is offline on encrypted hardware. Public key burned to eFuse at manufacture.

**Bad answer:**  
"We'll enable it for v2," or the private key is on a laptop.

---

## 2. MQTT / EMQX

**Audience:** Tech Lead, Firmware Engineers

> ⚠️ **Raise this yourself at the start of the meeting.**
> Production is currently on public HiveMQ, port 1883, TLS disabled.
> EMQX HTTP auth is in the codebase but not operational.

### 2.1 Broker

**Question to ask:**  
*"Our production backend is configured to use `broker.hivemq.com` on port 1883 with TLS disabled. Is this intentional? Are we running EMQX anywhere?"*

**Why it matters:**  
- Public HiveMQ does not support custom HTTP auth/ACL. Our backend's `/internal/mqtt/auth` and `/internal/mqtt/acl` endpoints are never called in production.
- Port 1883 is plaintext. Every API key, every child's question, every AI response is transmitted unencrypted.
- Any device in the world can connect to the public broker and publish to any topic.

**Good answer:**  
"We're in the process of migrating to EMQX Cloud. Here is the EMQX Cloud cluster URL, here is the timeline, here is the plan for migrating firmware broker configuration."

**Bad answer:**  
"Public HiveMQ is fine for now," or no awareness that this is a problem.

**Red flags:**  
- No EMQX account or cluster provisioned
- No timeline for switching
- Belief that the backend auth endpoints are currently protecting anything

**Follow-up questions:**
- What is the EMQX Cloud plan tier? What is the connection limit?
- How does the firmware receive the new broker hostname? Via OTA? Via a new firmware build?
- Will the MQTT username/password (factory_device_id + api_key) work on EMQX without changes?
- Has the EMQX HTTP auth plugin been configured to call our `/internal/mqtt/auth` endpoint?
- What is the `MQTT_AUTH_SECRET` value on production? Is it configured on both EMQX and the backend?

---

### 2.2 MQTT Gateway Single Point of Failure

**Question to ask:**  
*"The MQTT gateway is a single Docker container. What happens if it crashes? How long before toys are disconnected? How long before it recovers?"*

**Why it matters:**  
The gateway is the only process that subscribes to `boboloo/toy/+/audio/in` and `boboloo/toy/+/status`. If it goes down, all toy messages are dropped. With `clean_session=true` on the broker side (gateway client), queued messages are lost.

**Good answer:**  
Docker restart policy is `always`. The container recovers in seconds. Monitoring alerts on disconnect. Message queue (Redis) persists outbound messages so nothing is lost during brief restarts.

**Bad answer:**  
No monitoring, no alerting, no plan.

**Follow-up questions:**
- Is there a health check on the gateway container that distinguishes "process running" from "MQTT connected"?
- What is the broker reconnect behaviour in `gateway.py` if EMQX becomes unreachable?

---

### 2.3 MQTT Auth Security

**Question to ask:**  
*"The `MQTT_AUTH_SECRET` header is used to verify that auth/ACL calls actually come from EMQX. Is this secret configured on production? What is the value?"*

**Why it matters:**  
If `MQTT_AUTH_SECRET` is empty or unset, the backend accepts auth/ACL requests from anyone who discovers the endpoint URL. An attacker could call it directly to validate API keys.

**Good answer:**  
`MQTT_AUTH_SECRET` is set, matches the secret configured in EMQX, is rotated periodically.

**Bad answer:**  
Empty string, or "we haven't set it up yet."

---

### 2.4 Topic ACL Enforcement

**Question to ask:**  
*"When a toy publishes to `audio/in`, can it also publish to another toy's `audio/in` topic? Is ACL enforced?"*

**Why it matters:**  
The backend returns per-device ACL in the auth response. On EMQX this prevents one toy from impersonating another. On public HiveMQ, there is zero enforcement.

**Good answer:**  
EMQX ACL enforcement is confirmed working. The backend's inline ACL response is being honoured. Test demonstrates that publishing to another device's topic is rejected.

**Bad answer:**  
"We haven't tested that" on any broker other than EMQX.

---

## 3. OTA Updates

**Audience:** Tech Lead, Firmware Engineers

> ⚠️ **Production `S3_FIRMWARE_BUCKET` is empty string. OTA delivery is non-functional.**
> Raise this yourself.

### 3.1 S3 Configuration

**Question to ask:**  
*"The production `S3_FIRMWARE_BUCKET` environment variable is empty. OTA is completely non-functional. What S3 bucket is the firmware going to be stored in, and when is this configured?"*

**Why it matters:**  
`ota_service.py` generates pre-signed S3 URLs for firmware downloads. With no bucket set, no URL can be generated, no OTA command can be issued, and no firmware update can ever be delivered after shipping.

**Good answer:**  
An S3 bucket exists. IAM policy for the backend EC2 role allows `s3:GetObject` and `s3:PutObject` on that bucket. `S3_FIRMWARE_BUCKET` will be set before any production OTA is attempted. A test binary has been uploaded and a pre-signed URL has been generated manually to confirm access works.

**Bad answer:**  
No bucket created, no IAM permissions, or "we'll set it up when we need it."

**Follow-up questions:**
- What region is the S3 bucket in? Is it the same region as the EC2 instance? (Same region = no egress cost for the pre-signed URL generation.)
- Is the EC2 instance using an IAM role, or are `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` hardcoded in `.env`?
- What is the pre-signed URL expiry? (Currently 30 minutes in code.) Is that enough time for a slow factory WiFi to download the firmware?

---

### 3.2 Release Process

**Question to ask:**  
*"Walk me through exactly how a firmware update gets to a toy. Who does each step, in what order?"*

**Why it matters:**  
There is no documented release process. If the process is not agreed before manufacturing, the first firmware bug post-launch will cause confusion about who owns each step.

**Expected process (backend side):**
1. Firmware team builds and signs the binary → `boboloo-v1.2.0-signed.bin`
2. Firmware team provides SHA-256 and file size to backend team
3. Backend team uploads binary to S3: `aws s3 cp boboloo-v1.2.0-signed.bin s3://{bucket}/releases/1.2.0/`
4. Backend team registers release via OTA admin endpoint: `POST /sys/control/ota/releases`
5. Backend team optionally marks it stable: `POST /sys/control/ota/releases/1.2.0/stable`
6. Backend team pushes to specific devices or all devices on old version: `POST /sys/control/ota/push`

**Red flags:**
- No one owns the S3 upload step
- No one owns the release registration step
- "We'll figure it out when we need to push an update"

**Follow-up questions:**
- Who owns the firmware signing key? Who signs the binary?
- Does the firmware team provide the SHA-256 or does the backend team compute it?
- What is the rollout strategy? Single device test → percentage rollout → all devices?
- What is the on-call process if a bad firmware update bricks a toy?

---

### 3.3 Rollback Strategy

**Question to ask:**  
*"If we push a firmware update that breaks WiFi connectivity, what happens? What is the recovery path?"*

**Why it matters:**  
The firmware's `esp_ota_mark_app_valid_cancel_rollback()` is only called after confirmed MQTT reconnect. A firmware that breaks connectivity would never mark itself valid and would roll back automatically. But this only works if the previous partition is still intact.

**Good answer:**  
- Automatic rollback via ESP-IDF watchdog is tested and confirmed working
- Factory partition (read-only recovery image) is correctly flashed and tested
- Rollback is reported to backend via `{"ota_status": "rollback"}` status message
- Backend does not automatically re-push after a rollback (confirmed — it doesn't)
- Post-rollback process defined: investigation → fix → staged re-release

**Bad answer:**  
Rollback has not been tested. Or the factory recovery partition is not flashed.

**Follow-up questions:**
- Has rollback been triggered intentionally in testing? What was the result?
- What does the LED show during rollback?
- Is the factory recovery partition included in the production flash script?

---

### 3.4 Staged Rollout

**Question to ask:**  
*"How do we do a staged rollout — for example, push to 10 devices first, then 100, then all?"*

**Why it matters:**  
The current OTA push has two modes: single device, or all devices on a given version. There is no percentage rollout or gradual expansion built in. For a children's product, pushing to all devices simultaneously is high risk.

**Good answer:**  
Explicit staged plan: push to 1 internal test device → 10 pilot batch devices → all. Manual confirmation before each stage. Monitoring heartbeat reports and `ota_status` after each push.

**Bad answer:**  
"We'll push to everyone at once" or no staged plan.

---

## 4. Hardware

**Audience:** Hardware/Manufacturer Team

### 4.1 Pilot Batch Readiness

**Question to ask:**  
*"Are the pilot units physically assembled with the production PCB? What hardware revision are we on?"*

**Why it matters:**  
Pilot batch units must match production hardware. Testing on development boards and then manufacturing a different PCB revision is a common source of last-minute failures.

**Good answer:**  
Pilot units use production PCB revision (e.g. Rev A1). BOM locked. Components sourced or on order. Assembly fixtures ready.

**Bad answer:**  
Pilot units are dev boards. PCB revision is still changing.

**Red flags:**  
- PCB is not finalised
- Microphone, speaker, or amplifier components have not been validated with the firmware
- Power management not validated (battery life unknown)

**Follow-up questions:**
- Has the ESP32 been validated on the production PCB with all peripherals (mic, speaker, amp)?
- What is the measured battery life in normal operation?
- What is the measured current draw in BLE advertising mode vs WiFi+MQTT connected?
- Has the antenna layout been validated? What is the measured WiFi signal range?

---

### 4.2 Flash Memory Validation

**Question to ask:**  
*"Has the production partition table been validated end-to-end — factory partition, ota_0, ota_1, nvs_keys, all at correct offsets?"*

**Why it matters:**  
The `partitions.csv` defines a specific layout for 8MB flash. If the production PCB uses a different flash chip or a different size, OTA will fail silently and possibly corrupt data.

**Good answer:**  
`idf.py partition-table` output matches `partitions.csv`. Flash chip is confirmed 8MB. Test confirms factory partition is read-protected and cannot be overwritten by OTA.

**Bad answer:**  
"We haven't run the partition validation test yet."

**Follow-up questions:**
- Is the flash chip's write speed adequate for OTA at the production baud rate?
- Has anyone tested what happens when OTA writes to `ota_1` and then `ota_0` fails on the next update — is the device recoverable?

---

### 4.3 BLE Range and Reliability

**Question to ask:**  
*"Has BLE provisioning been tested at typical home distances — 3–5 metres, through a wall?"*

**Why it matters:**  
A parent trying to set up their child's toy from across a room and failing is a critical UX failure. BLE range is antenna-dependent.

**Good answer:**  
Tested at 5 metres clear line-of-sight, 3 metres through a single wall. Provisioning completes within 30 seconds in all cases.

**Bad answer:**  
"It works on the bench."

---

## 5. Manufacturing

**Audience:** Hardware/Manufacturer Team, Tech Lead

### 5.1 Factory Provisioning Workflow

**Question to ask:**  
*"Has the factory team run `factory_provision.py` against the staging backend? Show me the manifest CSV and JSON report from that run."*

**Why it matters:**  
The provisioning tool has been written, tested against the live backend, and verified. But the manufacturer has not yet demonstrated they can run it. This is the single most important factory workflow.

**Good answer:**  
They have the tool, have run it on staging, can show the manifest CSV and JSON report. The factory station is set up with Python, the tool, and the `.env.factory` file.

**Bad answer:**  
"We haven't set it up yet."

**Follow-up questions:**
- How many provisioning stations will you run simultaneously?
- What is the per-device provisioning time from BLE scan to API call complete?
- What is the plan if the provisioning laptop loses internet connectivity mid-batch?
- Who is the named person responsible for sending us the manifest CSV after each batch?

---

### 5.2 QA Process

**Question to ask:**  
*"Walk me through the QA checklist for a single unit. What tests does every toy pass before boxing?"*

**Why it matters:**  
The `docs/manufacturer_guide.md` defines a checklist. The question is whether it's actually being followed and whether it's operationally practical at production speed.

**Expected answer should include:**
- Electrical test (shorts, power rails)
- Flash verification
- NVS readback (`factory_id` correct, `boboloo` namespace empty)
- Cloud provisioning confirmed (`status: PROVISIONED`)
- BLE advertisement visible
- DEVICE_INFO characteristic returns correct `factory_device_id`

**Red flags:**
- QA checklist is aspirational, not operational
- BLE check skipped because "it's always fine"
- No verification that `boboloo` NVS is clean before boxing (toy would try to connect to non-existent WiFi on first boot)

**Follow-up questions:**
- What is the defect rate in the current test run?
- What percentage of boards fail electrical test?
- How long does the QA checklist take per unit at full production speed?
- What is the acceptance threshold for batch-level defects (e.g. reject a batch if >X% fail)?

---

### 5.3 Device ID Assignment

**Question to ask:**  
*"Show me the exact process for assigning and burning a `factory_device_id`. How do you guarantee uniqueness across batches?"*

**Why it matters:**  
A duplicate `factory_device_id` across two physical devices creates a collision in the backend database. The second provisioning call returns the first toy's `toy_uuid`. The wrong toy gets the wrong identity.

**Good answer:**  
Sequential assignment from a pre-agreed range (e.g. `BBL-000001` to `BBL-999999`). Range assigned per batch by Boboloo before manufacturing starts. NVS partition image generated programmatically per device, verified with readback.

**Bad answer:**  
"We increment a counter manually," or "we'll figure it out."

**Red flags:**
- No central allocation of ID ranges
- IDs generated on the factory floor without coordination
- No readback verification after NVS write

---

### 5.4 Batch Turnaround

**Question to ask:**  
*"After a batch is assembled, how long before the manifest CSV reaches us? What is the turnaround?"*

**Why it matters:**  
The backend verifies each batch in the database. Delays in receiving the manifest mean delays in verifying the batch before shipping.

**Good answer:**  
Same day. Manifest CSV and JSON report sent within hours of provisioning completing.

**Bad answer:**  
"End of the week," or no defined process.

---

## 6. Launch Readiness

**Audience:** All

### 6.1 Biggest Blockers — Raise These Explicitly

Present this table to the room. Ask each team to own a date for their items.

| Blocker | Severity | Who | Current Status |
|---------|----------|-----|----------------|
| MQTT on public HiveMQ, no TLS | 🔴 CRITICAL | Tech Lead + Backend | Broker not migrated to EMQX |
| S3 firmware bucket not configured | 🔴 CRITICAL | Backend | `S3_FIRMWARE_BUCKET=""` in production |
| Audio subsystem not implemented | 🔴 CRITICAL | Firmware | TODO in main.c |
| CORS_ORIGINS=* on production | 🟠 HIGH | Backend | Wildcard allows any origin |
| NVS encryption not enabled | 🟠 HIGH | Firmware | API key readable from flash |
| OTA command handler status unknown | 🟠 HIGH | Firmware | extern declaration, no confirmed implementation |
| EMQX HTTP auth not operational | 🔴 CRITICAL | Backend + DevOps | No ACL enforcement in production |

**Questions to ask for each blocker:**
- What is the person's name who owns this?
- What is the specific date it will be resolved?
- What is blocking it from being resolved today?

---

### 6.2 Dependency Map

Present this to make the critical path visible:

```
EMQX migration
  └─► MQTT auth/ACL operational
      └─► Per-device topic isolation enforced
          └─► Production security baseline met

S3 bucket configured
  └─► OTA firmware delivery functional
      └─► Post-launch firmware updates possible

Audio subsystem complete
  └─► End-to-end demo possible
      └─► Firmware integration testing can start
          └─► Factory provisioning validation
              └─► Pilot batch
                  └─► Production manufacturing
```

Nothing on the right side can start without the left side being done.

---

### 6.3 Questions for Product Stakeholders

**Question to ask:**  
*"Given that audio is not yet implemented and the MQTT broker is not production-ready, what is the realistic launch date?"*

**Why it matters:**  
Stakeholders may have a launch date in mind that does not account for the technical blockers. Better to surface this in the meeting than after manufacturing starts.

**Good answer:**  
A realistic date that accounts for: EMQX migration (days to weeks), audio completion (firmware estimate), integration testing (weeks), pilot batch (weeks), production run (weeks). No date is more credible than a wrong date.

**Bad answer:**  
A fixed date that ignores the blockers. Or no date.

**Follow-up questions:**
- What is the minimum viable product for launch? Does that include OTA, or just the core audio loop?
- What happens to devices already in the field if we need to push a critical security fix and OTA is not working?
- Is there a soft-launch plan (limited devices, controlled users) before full manufacturing?

---

### 6.4 What You Need from This Meeting

Close the meeting by confirming these outputs:

**By end of meeting, every item must have a name and a date:**

- [ ] Named owner for EMQX migration with go-live date
- [ ] Named owner for S3 bucket configuration with completion date
- [ ] Audio subsystem demo date confirmed
- [ ] OTA end-to-end test date confirmed
- [ ] Pilot batch device count and date confirmed
- [ ] Factory provisioning station setup date confirmed
- [ ] Device ID naming convention agreed
- [ ] Firmware version and hardware revision strings agreed for pilot batch
- [ ] CORS_ORIGINS update scheduled
- [ ] Next meeting date set

If any item leaves the meeting without a name and a date, it will not get done.

---

## Quick Reference — Good vs Bad Answers

| Topic | Good Answer | Bad Answer |
|-------|-------------|------------|
| Audio | "Working on bench, demo available, integrated with MQTT path by [date]" | "Almost done" / "Working on it" |
| OTA | "Tested end-to-end, rollback confirmed, S3 bucket being configured" | "We haven't done the S3 part yet" |
| EMQX | "Migration in progress, cluster provisioned, cutover by [date]" | "Public HiveMQ is fine for now" |
| Factory flashing | "Scripted, verified with readback, process documented" | "We do it manually" |
| Pilot batch | "PCBs are production revision, assembled, ready for test" | "We're using dev boards" |
| Launch date | Realistic date with buffer for the blockers | Fixed date that ignores blockers |
| NVS encryption | "Enabled in production build config" | "We'll add it later" |
