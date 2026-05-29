# Boboloo — Manufacturer & Factory Guide

**Audience:** Hardware manufacturers, contract electronics manufacturers (CEM), factory floor operators  
**Assumes:** You know PCB assembly, firmware flashing, and QA processes. You do NOT need to understand the cloud backend.  
**Version:** 1.0 | **Date:** 2026-05-29

---

## What Is Boboloo?

Boboloo is an AI-powered children's toy. Inside the toy is an ESP32 microcontroller that:
- Records the child's voice
- Converts speech to text on-device
- Sends the text to Boboloo's cloud servers over Wi-Fi
- Receives an AI-generated response and plays it back as audio

Your responsibility as the manufacturer ends when the toy ships. The cloud connection and AI features are activated later by the parent (end customer) through a phone app.

---

## What Factory Is Responsible For

| Factory Responsibility | Details |
|------------------------|---------|
| PCB assembly and soldering | Full board assembly including ESP32 module |
| Flashing the firmware binary | Using `esptool.py` at production line |
| Writing the Device ID to NVS | Permanent unique identifier burned into flash |
| Calling the provisioning API | Registering each device in the cloud database |
| Visual inspection and functional testing | Per the QA checklist in this document |
| Packaging and shipping | Standard product packaging |

## What Factory Is NOT Responsible For

| Not Factory's Responsibility | Who Handles It |
|------------------------------|----------------|
| Wi-Fi setup | Parent does this via Bluetooth (phone app) |
| Cloud account creation | Parent does this (Firebase account) |
| API key generation | Backend generates it automatically when parent connects via BLE and claims the toy |
| Firmware updates after shipping | Boboloo team pushes OTA updates |
| Child profile creation | Parent does this in the app |
| Audio/AI behavior | Cloud software team |
| QR codes or physical labels beyond the serial number | Not required — the parent app reads the Device ID directly from the toy over Bluetooth |

---

## Hardware Requirements

### Minimum ESP32 Specification

| Parameter | Requirement |
|-----------|-------------|
| Chip | ESP32 (ESP-WROOM-32 or equivalent) |
| Flash size | **8 MB minimum** |
| PSRAM | Recommended for audio buffering |
| Wi-Fi | 802.11 b/g/n (2.4 GHz) |
| Bluetooth | Bluetooth 4.2 or BLE 5.0 (NimBLE stack) |
| USB/UART | For firmware flashing (can be removed post-production) |

### Required Peripherals

| Peripheral | Purpose |
|------------|---------|
| Microphone | Capture child speech |
| Speaker + amp | Play AI audio response |
| On-device STT chip or processor | Convert speech to text before sending |
| Power management | Battery + USB charging |
| Status LED (optional but recommended) | Provisioning and connection feedback |

---

## Flash Memory Partition Layout

The ESP32 flash must use this exact partition layout. Deviating from it will break OTA updates.

```
Flash (8 MB total):

  Address    Partition      Type    Size     Purpose
  ─────────────────────────────────────────────────────────────
  0x0000     nvs            data    24 KB    Wi-Fi creds, API key storage
  0x6000     otadata        data    8 KB     Tracks which OTA partition boots
  0x8000     phy_init       data    4 KB     RF calibration data
  0xA000     factory        app     512 KB   Recovery firmware (never overwritten)
  0x110000   ota_0          app     2 MB     First OTA slot (initial firmware)
  0x310000   ota_1          app     2 MB     Second OTA slot (updated firmware)
  0x510000   nvs_keys       data    4 KB     NVS encryption keys (optional)
```

**Critical:** The `factory` partition contains a read-only recovery image. If OTA fails, the device automatically boots back to this partition. Never overwrite the factory partition after initial flash.

---

## NVS (Non-Volatile Storage) Structure

NVS is a key-value store in flash. The factory must write one value before shipping. The rest is filled in later by the parent's phone during Wi-Fi setup.

### At Time of Manufacture (Factory Must Write)

| NVS Namespace | Key | Value | Example |
|---------------|-----|-------|---------|
| `factory` | `factory_id` | The device's unique ID string | `TOY-A1B2C3` |

**This value is permanent and must never be changed.** It is the identity of the device for its entire lifetime.

### After Parent Setup (Written by Phone App — Not Factory)

| NVS Namespace | Key | Value |
|---------------|-----|-------|
| `boboloo` | `api_key` | Raw API key (written by parent phone app) |
| `boboloo` | `ssid` | Wi-Fi network name (written by parent phone app) |
| `boboloo` | `pass` | Wi-Fi password (written by parent phone app) |
| `boboloo` | `provisioned` | `1` — set last as an atomic completion flag |

At time of shipping, the `boboloo` namespace must be **empty**. If it is populated, the device will try to connect to a network that does not exist.

### Writing factory_id to NVS (esptool + nvs_flash_gen)

Use the ESP-IDF NVS partition generator tool:

```bash
# 1. Create a CSV file describing the NVS content
cat > nvs_values.csv << EOF
key,type,encoding,value
factory_id,data,string,TOY-A1B2C3
EOF

# 2. Generate a binary NVS partition image
python $IDF_PATH/components/nvs_flash/nvs_partition_generator/nvs_partition_gen.py \
  generate nvs_values.csv nvs_partition.bin 0x6000

# 3. Flash only the NVS partition (do NOT flash the full image here)
esptool.py --port /dev/ttyUSB0 write_flash 0x0 nvs_partition.bin
```

In production, use a fixture that generates a unique `factory_id` per board and flashes the corresponding NVS image.

---

## Device ID Format

Each Boboloo toy must have a globally unique Device ID. The format is:

```
TOY-[6 alphanumeric characters]
Example: TOY-A1B2C3
```

Requirements:
- Must be unique across all manufactured toys, all time
- Must be uppercase
- Must match exactly what is written to the NVS `factory` namespace `factory_id` key
- Must be registered in the cloud database before shipping (see Provisioning section)
- Use a sequential or batch-structured naming scheme to avoid collisions

Example batch: `TOY-AA0001` through `TOY-AA9999` for one production run.

---

## Firmware Flashing Process

### Files Required (supplied by Boboloo firmware team)

| File | Purpose | Flash Address |
|------|---------|---------------|
| `bootloader.bin` | ESP32 bootloader | `0x0` |
| `partition-table.bin` | Partition layout descriptor | `0x8000` |
| `boboloo-vX.Y.Z-signed.bin` | Main application firmware | `0x10000` |
| `factory-recovery.bin` | Recovery partition | `0xA000` |

The firmware team will provide a single combined flash command. A typical flash command looks like:

```bash
esptool.py \
  --chip esp32 \
  --port /dev/ttyUSB0 \
  --baud 921600 \
  write_flash \
  0x0 bootloader.bin \
  0x8000 partition-table.bin \
  0xA000 factory-recovery.bin \
  0x10000 boboloo-vX.Y.Z-signed.bin
```

**Important:**
- Flash speed 921600 baud is safe for most setups. Drop to 460800 if you see CRC errors.
- Verify with `--verify` flag on first boards of each batch.
- After flashing, write the NVS partition separately (see NVS section above).

### Flash Verification

```bash
esptool.py --port /dev/ttyUSB0 read_flash 0x0 0x800000 readback.bin
# Then diff with original combined binary
```

Run this on 5% of boards per batch (minimum 3 boards per batch).

---

## Cloud Provisioning — Registering Each Device

Every device must be registered in Boboloo's cloud database before it ships. This is what lets the parent app verify the toy is genuine and claim ownership of it via Bluetooth.

### What Provisioning Does

- Creates a record in the cloud database for the device
- Links the Device ID to your batch ID and firmware version
- Sets the device status to `PROVISIONED` (ready to be claimed by a parent)

### How to Provision

**Endpoint:** `POST https://api.boboloo.com/api/v1/factory/provision`  
**Required Header:** `factory-secret: <YOUR_FACTORY_SECRET_KEY>`  
**Content-Type:** `application/json`

**Single device:**
```json
POST /api/v1/factory/provision
factory-secret: <secret>

{
  "factory_device_id": "TOY-A1B2C3",
  "firmware_version": "1.0.0",
  "hardware_revision": "REV-B",
  "batch_id": "BATCH-2026-001"
}
```

**Response (success):**
```json
{
  "toy_uuid": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "status": "PROVISIONED"
}
```

**Batch provisioning (up to hundreds at once):**
```json
POST /api/v1/factory/provision-batch
factory-secret: <secret>

{
  "device_ids": ["TOY-A1B2C3", "TOY-D4E5F6", "TOY-G7H8I9"],
  "firmware_version": "1.0.0",
  "hardware_revision": "REV-B",
  "batch_id": "BATCH-2026-001"
}
```

**Response:**
```json
{
  "batch_id": "BATCH-2026-001",
  "requested": 3,
  "created": 3,
  "duplicates": 0
}
```

**Important notes:**
- Provisioning is **idempotent** — calling it twice for the same device is safe (second call is ignored).
- The `factory-secret` key is provided separately by the Boboloo team. Do not share it.
- Provision **before** shipping. The `factory_id` in NVS must match the provisioned record in the cloud, otherwise the parent app cannot claim the toy.
- Record the response `toy_uuid` for your own manufacturing records.

### Provisioning Script

The Boboloo team provides a Python helper script:

```bash
python provision_toy.py TOY-A1B2C3 TOY-D4E5F6
# or for a full batch:
python provision_toy.py --batch 100 --prefix TOY-AA
```

---

---

## Production QA Checklist

Run this checklist for every unit before packaging.

### Electrical / Hardware
- [ ] Board passes automated electrical test (shorts, opens, power rail voltages)
- [ ] ESP32 chip identified correctly by `esptool.py chip_id`
- [ ] Flash memory 8MB verified
- [ ] Battery charges correctly (correct voltage under load)
- [ ] Speaker produces audio at target volume
- [ ] Microphone records audio (measured SNR)

### Firmware Flash Verification
- [ ] Bootloader flashed and boots without error
- [ ] Partition table verified
- [ ] Application firmware version matches target (read from device log on first boot)
- [ ] Factory recovery partition present and not modified
- [ ] NVS `factory_id` reads back correctly:
  ```
  Expected: TOY-XXXXXX
  Actual:   ___________
  ```

### NVS Verification
- [ ] `factory` namespace contains exactly one key: `factory_id`
- [ ] `boboloo` namespace is empty (no stale credentials)
- [ ] `factory_id` value matches the Device ID assigned to this board

### Cloud Provisioning Verification
- [ ] Device provisioned via API (status `PROVISIONED` confirmed in response)
- [ ] `factory_id` in NVS matches the provisioned cloud record exactly

### Functional Test (Bluetooth)
- [ ] Device enters BLE advertising mode on power-on (firmware requirement)
- [ ] BLE advertisement visible from test phone
- [ ] GATT service responds to read of `CHAR_DEVICE_INFO` with correct `factory_id`

### Final
- [ ] Serial number label attached
- [ ] Packaging sealed

---

## Batch Acceptance Criteria

| Defect Type | Accept / Reject |
|-------------|----------------|
| Electrical failure | Reject individual unit |
| Flash CRC error | Reject individual unit, re-flash |
| NVS factory_id mismatch | Reject batch, investigate fixture |
| Cloud provisioning failure | Hold batch, contact Boboloo team |
| BLE not advertising | Reject individual unit |
| CHAR_DEVICE_INFO returns wrong factory_id | Reject individual unit, re-flash NVS |

---

## What Happens After the Toy Ships

This section is for factory understanding only — no action required.

```
1. [Factory]    Toy ships to customer. Device ID is in the cloud as PROVISIONED.

2. [Parent]     Parent downloads the Boboloo app and creates an account.

3. [Parent]     Parent opens the app and turns on the toy.
                App connects to the toy over Bluetooth (BLE).
                App reads the Device ID directly from the toy's BLE characteristic (CHAR_DEVICE_INFO).
                App sends the Device ID to the cloud backend.

4. [Cloud]      The backend verifies the Device ID exists and is PROVISIONED.
                The backend generates a secret API key for this toy.
                Status changes to ACTIVE.
                The API key is sent back to the parent app.

5. [Parent]     App sends the toy (over the same BLE connection): Wi-Fi credentials + API key.
                Toy saves these to NVS boboloo namespace.

6. [Toy]        Toy connects to Wi-Fi using the saved credentials.
                Toy connects to the cloud MQTT broker using the API key.

7. [Child]      Child uses the toy. Questions go to the cloud, AI answers come back.

8. [Cloud]      Firmware updates (OTA) are pushed over Wi-Fi automatically.
                Factory never needs to handle this.
```

---

## OTA Firmware Update — Factory Awareness

After toys ship, Boboloo will push firmware updates over the air (OTA). Factory has no role in this process, but should be aware of what makes it work:

- The `ota_0` and `ota_1` partitions are the OTA slots. ESP-IDF alternates between them.
- The `factory` recovery partition is never touched by OTA. It is always available as a fallback.
- If an OTA update fails (bad download, power cut), the device automatically reverts to the previous partition.
- The `otadata` partition tracks which slot is currently active. Do not overwrite it at the factory.

**Factory requirement for OTA compatibility:**
- Use the official partition table provided by the firmware team (listed above).
- Do not add custom partitions that overlap OTA slot addresses.
- Ensure the recovery partition is correctly flashed.

---

## Contact and Escalation

| Issue | Contact |
|-------|---------|
| Factory secret key questions | Boboloo production team |
| Firmware binary releases | Boboloo firmware team |
| Cloud provisioning API errors | Boboloo backend team |
| Batch failures (>5% defect rate) | Boboloo production manager |
| BLE characteristic or NVS format questions | Boboloo firmware team |

---

## Assumptions

1. The Boboloo firmware team provides signed, verified binary files for each production run.
2. Each factory batch has a unique `batch_id` string agreed upon in advance with Boboloo.
3. Factory has internet access to call the provisioning API from the production floor.
4. The `FACTORY_SECRET_KEY` is rotated by Boboloo between major production runs and communicated securely.
5. NVS encryption is optional for the current revision but may become mandatory in future hardware revisions.
