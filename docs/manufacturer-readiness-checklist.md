# Manufacturer Readiness Checklist

**Version:** 1.0 | **Date:** 2026-06-01

This checklist gates each phase of manufacturing. No phase begins until all items
in the previous phase are checked off by the responsible party.

---

## Phase 1 — Before Integration Starts

### Backend Owner (Boboloo)

- [ ] `docs/mqtt-integration-contract.md` is finalised and sent to firmware team
- [ ] `docs/provisioning-api-contract.md` is finalised and sent to factory team
- [ ] `docs/firmware_engineer_guide.md` is current and sent to firmware team
- [ ] `docs/manufacturer_guide.md` is current and sent to factory team
- [ ] `factory/factory_provision.py` is deployed and tested on staging
- [ ] `factory/quick-start.md` is accurate for current tool version
- [ ] `factory/failure-runbook.md` covers all error scenarios
- [ ] Staging `FACTORY_SECRET_KEY` generated and ready to share
- [ ] MQTT broker CA certificate exported as a `.pem` file, ready to deliver
- [ ] MQTT broker hostname confirmed and ready to deliver (out-of-band)
- [ ] Production `ENVIRONMENT=production` confirmed on production server
- [ ] `/api/v1/factory/dev-issue-key` returns 404 on production (verify manually)
- [ ] Device ID naming convention agreed in writing with manufacturer

### Manufacturer

- [ ] Firmware repository confirmed — canonical source identified
- [ ] Audio subsystem implementation status confirmed (implemented / in progress / timeline given)
- [ ] OTA command handler (`handle_cmd_message`) confirmed implemented
- [ ] Factory flashing procedure documented and shared with Boboloo
- [ ] ECDSA signing keypair generated; private key secured offline
- [ ] Factory station hardware confirmed (OS, Python 3.8+, BLE scanner available)
- [ ] Firmware version string confirmed for first batch (e.g. `1.0.0`)
- [ ] Hardware revision string confirmed (e.g. `A1`)

---

## Phase 2 — Firmware Integration Complete

### Backend Owner (Boboloo)

- [ ] Staging credentials delivered to firmware team (API URL + factory secret + CA cert + broker hostname)
- [ ] Staging backend monitored during firmware integration tests
- [ ] Heartbeat received on staging backend within 60 s of toy MQTT connection
- [ ] `audio/in` message arrives at staging backend with correct `text` field
- [ ] `audio/out` plain-text reply received and confirmed played by toy
- [ ] OTA command delivered; toy downloads, SHA-256 verified, reboots, heartbeats on new version
- [ ] Staging DB shows toy with correct `firmware_version` after OTA success
- [ ] Redis presence key `toy:status:{device_id}` updates correctly

### Manufacturer

- [ ] MQTT TLS connection established to staging broker using CA cert
- [ ] CONNACK rc=0 received with correct `factory_device_id` as username and raw API key as password
- [ ] Subscribed to `audio/out` and `cmd` at QoS 1 on connect
- [ ] Heartbeat published on connect and every 60 s
- [ ] Full audio round-trip demonstrated: toy publishes question → AI response → toy plays reply
- [ ] OTA update completed end-to-end: receive cmd → download → SHA-256 verify → reboot → mark valid
- [ ] CONNACK rc=4/rc=5 → NVS erase and BLE restart demonstrated
- [ ] Factory reset (5-second hold) erases `boboloo` namespace; `factory_device_id` survives
- [ ] Integration test results documented and shared with Boboloo

---

## Phase 3 — Factory Provisioning Validated

### Backend Owner (Boboloo)

- [ ] Factory station setup reviewed (Python installed, tool runs, credentials configured)
- [ ] `factory_provision.py --dry-run` passes with 10 test IDs
- [ ] `factory_provision.py` live run produces correct manifest CSV and JSON report
- [ ] 10 test devices visible in staging DB with correct batch_id, firmware_version, hardware_revision
- [ ] Audit log shows `factory.provision_batch` event for the test run
- [ ] All Phase 3 acceptance tests below signed off
- [ ] Production credentials issued (API URL + production factory secret)

### Manufacturer

- [ ] Factory provisioning station is operational (laptop, Python, script, credentials)
- [ ] `device_ids.txt` workflow demonstrated: build file → dry run → live run
- [ ] Manifest CSV correct: one row per device, non-null `toy_uuid` for each
- [ ] JSON audit report sent to Boboloo after test run
- [ ] BLE advertisement visible for all 10 test units after flashing
- [ ] `DEVICE_INFO` GATT characteristic returns correct `factory_device_id` for each unit
- [ ] Factory reset confirmed: units return to unprovisioned state after test

### Phase 3 Acceptance Tests (both parties sign off)

- [ ] `POST /provision` returns 200 with valid UUID for a new ID
- [ ] `POST /provision` called again with same ID returns same UUID (idempotency)
- [ ] `POST /provision-batch` with 10 IDs returns 10 real UUIDs in `toys[]`
- [ ] `POST /provision-batch` with duplicate IDs in one request: no 500 error, deduplicated correctly
- [ ] `POST /provision-batch` with 501 IDs: rejected with 422
- [ ] `POST /provision` with ID shorter than 4 chars: rejected with 422
- [ ] `POST /provision` with special characters in ID: rejected with 422
- [ ] `POST /provision` with wrong factory secret: 403
- [ ] `POST /disable` sets status to DISABLED, returns `status: "disabled"`
- [ ] `POST /disable` called again on disabled toy: returns `status: "already_disabled"`
- [ ] Audit log shows all events for the test run

---

## Phase 4 — Pilot Batch (10–50 units)

### Backend Owner (Boboloo)

- [ ] Pilot device ID list received from manufacturer before provisioning starts
- [ ] All pilot IDs in production DB after provisioning, status = PROVISIONED
- [ ] All pilot IDs have correct `firmware_version` and `hardware_revision`
- [ ] Manifest CSV received same day as provisioning
- [ ] JSON audit report received same day
- [ ] 3 pilot units complete full consumer flow: BLE → WiFi → MQTT → audio in/out
- [ ] Backend `last_seen` and `firmware_version` update correctly for those 3 units
- [ ] OTA update pushed to 1 pilot unit on production — verified end-to-end
- [ ] Phase 5 shipping checklist signed off

### Manufacturer

- [ ] All pilot units boot cleanly — 0 units showing error LED at boot
- [ ] 100% of pilot units have `factory_device_id` readable via BLE DEVICE_INFO characteristic
- [ ] `factory_device_id` on BLE matches label on box for 100% of units
- [ ] `factory_provision.py` run against production for full pilot batch
- [ ] Manifest CSV and JSON report sent to Boboloo same day
- [ ] All pilot units in unprovisioned state (`boboloo` NVS empty) before boxing

---

## Phase 5 — Production (Per Batch)

### Backend Owner (Boboloo)

- [ ] Batch verified in DB same day manufacturer provisions it
- [ ] Any provisioning failures investigated same day
- [ ] Defective units disabled via `POST /disable` when notified
- [ ] Manifest CSV and JSON report archived

### Manufacturer

- [ ] `factory_provision.py` run for each batch against production
- [ ] Manifest CSV and JSON report sent to Boboloo same day as provisioning
- [ ] Failed units reported to Boboloo same day — not shipped
- [ ] All units confirmed unprovisioned (NVS clean) before boxing
- [ ] Manifests and reports archived for minimum 2 years

---

## Credential Release Summary

| Credential | Released at |
|------------|-------------|
| Integration contract documents | Phase 1 — immediately |
| Factory provisioning tool + docs | Phase 1 — immediately |
| Staging factory secret | Start of Phase 2 |
| MQTT CA certificate | Start of Phase 2 |
| MQTT broker hostname | Start of Phase 2 |
| Production factory secret | End of Phase 3 (all acceptance tests signed off) |
| Production API URL | End of Phase 3 |
