# Boboloo API Key Lifecycle
## Complete Technical Reference: Factory → Claim → BLE → MQTT

---

## Core Principle

There are two forms of the API key that exist simultaneously and serve different purposes:

| Form | Where It Lives | Who Sees It | Purpose |
|------|---------------|-------------|---------|
| **Raw Key** | ESP32 NVS flash, HTTP claim response | Parent app (once), toy firmware | MQTT password (must be plaintext over TLS) |
| **SHA-256 Hash** | PostgreSQL `api_keys` table, Redis cache | Backend only | Identity verification without exposing original |

**Why this split matters:** The ESP32 must send the raw key as the MQTT password. If it stored a hash instead, it would send a hash — and the backend would hash that hash — and the two would never match.

---

## Realistic Example Values

These values will be used throughout this document to make the flow concrete.

```
factory_device_id  =  "TOY-A1B2C3"
toy_uuid           =  "f47ac10b-58cc-4372-a567-0e02b2c3d479"
parent_uid         =  "firebase_uid_abc123"

raw_api_key        =  "xK9mP2nQ8rL4vW7yJ3hF6tB1cE5uD0sA"
sha256_hash        =  "a3f8d2e1b4c96f0e7d5a2b8c1f3e9d4a..."
                      (full 64-character hex string)
```

---

## Phase 1 — Factory Time

**When:** Before the toy ever reaches a customer. Performed by the manufacturer.

**What happens:**
1. ESP32 chip is flashed with firmware binary
2. A factory provisioning script calls `POST /api/v1/factory/provision`
3. Backend creates a `Toy` record in PostgreSQL with `status = PROVISIONED`
4. The toy's `factory_device_id` is written to NVS under namespace `"factory"`, key `"factory_id"`

### NVS State After Factory

```
Namespace "factory":
  factory_id  →  "TOY-A1B2C3"   ← permanent, never overwritten

Namespace "boboloo":
  (empty — no wifi, no api_key, no provisioned flag)
```

### PostgreSQL State After Factory

```sql
SELECT * FROM toys WHERE factory_device_id = 'TOY-A1B2C3';

id           = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
factory_device_id = "TOY-A1B2C3"
status       = "PROVISIONED"
parent_id    = NULL        ← not yet claimed
active_child_id = NULL
created_at   = "2025-01-15 09:23:11 UTC"

SELECT * FROM api_keys WHERE toy_id = 'f47ac10b...';
(0 rows)                   ← no key exists yet
```

### Redis State After Factory

```
(nothing — toy not in Redis at all)
```

**At this point:** The toy is a blank device. It knows its own identity (`factory_device_id`) but has no credentials to connect to anything.

---

## Phase 2 — Claim Time

**When:** Parent opens the Boboloo app, scans the QR code on the toy's box, taps "Claim Toy."

**What the QR code contains:** Just the `factory_device_id` string — `"TOY-A1B2C3"`.

### Backend Key Generation Flow

The parent app sends: `POST /api/v1/toy/claim/`

```
Headers:
  Authorization: Bearer <firebase_jwt_token>
  Content-Type: application/json

Body:
  { "factory_device_id": "TOY-A1B2C3" }
```

Inside `toy_claim_service.py`, the backend executes:

```python
# Step 1: Find the toy and lock the row (prevents double-claim race)
toy = await db.execute(
    select(Toy)
    .where(Toy.factory_device_id == "TOY-A1B2C3")
    .with_for_update()           # SELECT FOR UPDATE row lock
)

# Step 2: Verify toy is unclaimed
# toy.status == "PROVISIONED" and toy.parent_id == None

# Step 3: Generate the raw API key
raw_api_key = secrets.token_urlsafe(32)
# Result: "xK9mP2nQ8rL4vW7yJ3hF6tB1cE5uD0sA"
# 32 random bytes → base64url encoded → 43 characters

# Step 4: Hash it — backend NEVER stores the raw key
key_hash = hashlib.sha256(raw_api_key.encode()).hexdigest()
# Result: "a3f8d2e1b4c96f0e7d5a2b8c1f3e9d4a..."
# (64 hex characters)

# Step 5: Store ONLY the hash in PostgreSQL
db.add(APIKey(
    key_hash = "a3f8d2e1b4c96f0e7d5a2b8c1f3e9d4a...",
    toy_id   = toy.id,
    revoked  = False,
    created_at = datetime.utcnow()
))

# Step 6: Update toy status
toy.status    = "CLAIMED"
toy.parent_id = parent.id

# Step 7: Populate Redis cache (24-hour TTL)
await redis_client.set(
    f"toy_key:{key_hash}",
    str(toy.id),
    ex=86400
)

# Step 8: Return raw key — ONLY TIME IT LEAVES THE BACKEND
return {
    "toy_uuid":    "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    "toy_api_key": "xK9mP2nQ8rL4vW7yJ3hF6tB1cE5uD0sA",  ← raw key
    "status": "claimed"
}
```

### PostgreSQL State After Claim

```sql
SELECT * FROM toys WHERE factory_device_id = 'TOY-A1B2C3';

status    = "CLAIMED"
parent_id = "parent_db_uuid_xyz"

SELECT * FROM api_keys WHERE toy_id = 'f47ac10b...';

id       = "key_uuid_001"
key_hash = "a3f8d2e1b4c96f0e7d5a2b8c1f3e9d4a..."   ← HASH only
revoked  = false
created_at = "2025-01-20 14:31:07 UTC"
```

### Redis State After Claim

```
toy_key:a3f8d2e1b4c96f0e7d5a2b8c1f3e9d4a...  →  "f47ac10b-58cc-4372-a567-0e02b2c3d479"
TTL: 86400 seconds (24 hours)
```

### What the Parent App Holds (in Memory)

```
raw_api_key  =  "xK9mP2nQ8rL4vW7yJ3hF6tB1cE5uD0sA"
toy_uuid     =  "f47ac10b-58cc-4372-a567-0e02b2c3d479"
```

The parent app now needs to get this raw key onto the toy. It does this via Bluetooth.

---

## Phase 3 — BLE Provisioning

**When:** Immediately after claiming, parent app connects to the toy via Bluetooth Low Energy.

**Critical point:** This entire phase is **purely local** — phone to toy directly. No internet required. The backend is not involved at all.

### Why BLE and Not Direct App-to-Cloud

The toy has no internet credentials yet. It cannot make HTTPS calls. The only radio it has active is Bluetooth. The parent app acts as a local bridge: it received the raw key from the backend in Phase 2, and now delivers it to the toy's flash memory over Bluetooth.

### BLE GATT Structure

The toy advertises a GATT server with these characteristics:

| Characteristic Name | UUID | Direction | Purpose |
|--------------------|------|-----------|---------|
| `CHAR_DEVICE_INFO` | ...0001 | Toy → Phone | Read factory_device_id |
| `CHAR_WIFI_SSID` | ...0002 | Phone → Toy | Write Wi-Fi network name |
| `CHAR_WIFI_PASS` | ...0003 | Phone → Toy | Write Wi-Fi password |
| `CHAR_API_KEY` | ...0004 | Phone → Toy | Write raw API key |
| `CHAR_PROV_CMD` | ...0005 | Phone → Toy | Write "COMMIT" command |
| `CHAR_PROV_STATUS` | ...0006 | Toy → Phone | Read provisioning status |

### Exact Write Sequence

Parent app performs these BLE writes in order:

```
1. WRITE  CHAR_WIFI_SSID  →  "HomeNetwork_5G"
2. WRITE  CHAR_WIFI_PASS  →  "mypassword123"
3. WRITE  CHAR_API_KEY    →  "xK9mP2nQ8rL4vW7yJ3hF6tB1cE5uD0sA"
4. WRITE  CHAR_PROV_CMD   →  "COMMIT"
```

### What the Toy Does on COMMIT

The toy's firmware (`ble_provisioning.c`) handles the COMMIT command:

```c
// Step 1: Write to NVS in safe order
// api_key first — if power fails mid-write, we can detect incomplete state
nvs_set_str(nvs_handle, "api_key", received_api_key);  // writes raw key
nvs_set_str(nvs_handle, "ssid",    received_ssid);
nvs_set_str(nvs_handle, "pass",    received_pass);
nvs_set_u8(nvs_handle,  "provisioned", 1);             // LAST — atomic flag
nvs_commit(nvs_handle);

// Step 2: Transition BLE state machine
state = WIFI_CONNECTING;

// Step 3: Connect to Wi-Fi, then MQTT
// Step 4: On successful MQTT connect, update status to READY
// Step 5: Notify CHAR_PROV_STATUS = "READY"
```

### NVS State After BLE Provisioning

```
Namespace "factory":
  factory_id   →  "TOY-A1B2C3"              ← unchanged, permanent

Namespace "boboloo":
  ssid         →  "HomeNetwork_5G"
  pass         →  "mypassword123"
  api_key      →  "xK9mP2nQ8rL4vW7yJ3hF6tB1cE5uD0sA"  ← RAW key
  provisioned  →  1
```

**Why RAW key in NVS, not hash:**
The toy must send the raw key as the MQTT password (see Phase 4). Storing a hash would mean sending a hash as password. The backend would then hash that hash. The two values would never match. The raw key must be stored.

---

## Phase 4 — MQTT Authentication

**When:** Every time the toy boots, connects to Wi-Fi, and establishes a connection to the EMQX broker.

### MQTT Connection Parameters

The toy reads from NVS and builds this MQTT CONNECT packet:

```
Broker:    broker.hivemq.com (or configured MQTT_HOST)
Port:      8883  ← MQTTS (MQTT over TLS)
Client ID: "TOY-A1B2C3"           ← factory_device_id
Username:  "TOY-A1B2C3"           ← same as client ID
Password:  "xK9mP2nQ8rL4vW7yJ3hF6tB1cE5uD0sA"   ← raw key from NVS
```

The TLS layer encrypts everything before it leaves the chip. The raw key is never visible on the network.

### EMQX HTTP Auth Plugin Flow

EMQX does not validate the password itself. Instead, for every new connection, it makes an HTTP call to the backend:

```
POST /internal/mqtt/auth
X-Mqtt-Auth-Secret: <MQTT_AUTH_SECRET>
Content-Type: application/json

{
  "clientid":  "TOY-A1B2C3",
  "username":  "TOY-A1B2C3",
  "password":  "xK9mP2nQ8rL4vW7yJ3hF6tB1cE5uD0sA"
}
```

### Backend Auth Handler (`mqtt_auth_routes.py`)

```python
# Step 1: Verify EMQX secret (constant-time comparison)
if not hmac.compare_digest(header_secret, settings.MQTT_AUTH_SECRET):
    return {"result": "deny"}

# Step 2: Hash the incoming raw password
incoming_hash = hashlib.sha256(password.encode()).hexdigest()
# "xK9mP2nQ8rL4vW7yJ3hF6tB1cE5uD0sA" → "a3f8d2e1b4c96f0e7d5a2b8c1f3e9d4a..."

# Step 3: Redis fast path (hits ~99% of the time)
toy_id = await redis_client.get(f"toy_key:{incoming_hash}")

# Step 4: DB fallback (cache miss or expired TTL)
if toy_id is None:
    key_record = await db.execute(
        select(APIKey)
        .where(APIKey.key_hash == incoming_hash)
        .where(APIKey.revoked == False)
    )
    if key_record:
        toy_id = str(key_record.toy_id)
        # Self-heal: repopulate Redis
        await redis_client.set(f"toy_key:{incoming_hash}", toy_id, ex=86400)

# Step 5: Cross-verify username matches toy
toy = await db.get(Toy, toy_id)
if toy.factory_device_id != username:
    return {"result": "deny"}   # key stolen by different device

# Step 6: Return allow + inline per-device ACL
return {
    "result": "allow",
    "acl": [
        {"action": "publish",   "topic": f"boboloo/toy/TOY-A1B2C3/audio/in"},
        {"action": "subscribe", "topic": f"boboloo/toy/TOY-A1B2C3/audio/out"},
        {"action": "publish",   "topic": f"boboloo/toy/TOY-A1B2C3/status"},
        {"action": "subscribe", "topic": f"boboloo/toy/TOY-A1B2C3/cmd"}
    ]
}
```

The toy is now connected and restricted to only its own topics.

---

## Phase 5 — Complete State Table

Snapshot of all storage locations after the toy is fully operational:

### PostgreSQL

```sql
-- toys table
factory_device_id = "TOY-A1B2C3"
status            = "CLAIMED"
parent_id         = "parent_db_uuid_xyz"
active_child_id   = "child_db_uuid_abc"   ← set when parent activates a child profile

-- api_keys table
key_hash  = "a3f8d2e1b4c96f0e7d5a2b8c1f3e9d4a..."
toy_id    = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
revoked   = false
```

### Redis

```
toy_key:a3f8d2e1...   →   "f47ac10b-..."     TTL: 86400s
toy:f47ac10b-...      →   {online:1, ...}     TTL: 120s (heartbeat)
child:child_uuid      →   {name:..., age:...} TTL: 300s
settings:child_uuid   →   {complexity:...}    TTL: 300s
```

### ESP32 NVS Flash

```
factory namespace:
  factory_id   →  "TOY-A1B2C3"

boboloo namespace:
  ssid         →  "HomeNetwork_5G"
  pass         →  "mypassword123"
  api_key      →  "xK9mP2nQ8rL4vW7yJ3hF6tB1cE5uD0sA"   ← RAW
  provisioned  →  1
```

### What the Backend Never Has

The backend does not store, log, or cache the raw API key at any point after generating it in the claim handler. Once the claim HTTP response is sent, the backend only knows the SHA-256 hash.

---

## Phase 6 — Security Analysis

### Threat: PostgreSQL Database Leak

```
Attacker obtains:
  api_keys.key_hash = "a3f8d2e1b4c96f0e7d5a2b8c1f3e9d4a..."

To connect to MQTT, attacker needs:
  raw key = "xK9mP2nQ8rL4vW7yJ3hF6tB1cE5uD0sA"

SHA-256 is a one-way function. The attacker cannot reverse the hash.
Brute force: secrets.token_urlsafe(32) produces 256 bits of entropy.
At 10^18 guesses/second, breaking a single key takes ~10^58 years.

Result: DB leak exposes zero usable credentials.
```

### Threat: Redis Cache Leak

```
Attacker obtains:
  toy_key:a3f8d2e1...  →  "f47ac10b-..."

This is hash → toy_uuid mapping. The attacker has a hash and a UUID.
They still cannot derive the raw key from the hash.
They cannot use the UUID alone to authenticate to MQTT.

Result: Redis leak exposes only toy UUIDs (non-secret identifiers).
```

### Threat: Physical Flash Extraction

```
Attacker physically extracts the ESP32 flash chip with specialized hardware.
They read the NVS partition.
NVS encryption is disabled by default (Secure Storage optional feature).
They obtain:
  raw api_key = "xK9mP2nQ8rL4vW7yJ3hF6tB1cE5uD0sA"

They can now connect to MQTT as this toy and send audio messages.

Mitigation:
  1. Enable NVS encryption in production firmware (nvs_keys partition is in partitions.csv)
  2. Report stolen/lost endpoint → mark APIKey.revoked = true → toy denied immediately
  3. Impact is isolated to one toy. Other toys have different random keys.

Result: Physical attack is possible. Scoped to single device. Enable NVS encryption.
```

### Threat: Network Interception

```
MQTT connection uses TLS on port 8883.
TLS encrypts the CONNECT packet including the password field.
A network attacker sees encrypted bytes only.

Result: Raw key protected in transit by TLS.
```

### Attack Surface Summary

| Attack Vector | Raw Key Exposed? | Blast Radius | Mitigation |
|--------------|-----------------|--------------|------------|
| DB SQL injection | No (hash only) | None | Hash is irreversible |
| Redis dump | No (hash only) | None | Hash is irreversible |
| Flash extraction | Yes | Single toy | NVS encryption + stolen report |
| Network MITM | No | None | TLS 1.2+ |
| Claim response interception | Yes (one-time) | Single toy | HTTPS only |
| Backend memory dump | Brief (claim only) | Single toy | Ephemeral in memory |

---

## Phase 7 — Key Rotation Flow

Key rotation happens when: parent requests rotation, key is suspected compromised, or the toy is sold/transferred.

### 15-Step Rotation Sequence

```
Step 1:   Parent app calls POST /api/v1/toy/claim/rotate
Step 2:   Backend finds current APIKey for this toy
Step 3:   BEGIN TRANSACTION
Step 4:   SET api_keys.revoked = true WHERE toy_id = X AND revoked = false
Step 5:   Generate new raw_key = secrets.token_urlsafe(32)
Step 6:   Compute new_hash = sha256(new_raw_key)
Step 7:   INSERT INTO api_keys (key_hash=new_hash, toy_id=X, revoked=false)
Step 8:   COMMIT TRANSACTION
Step 9:   DELETE FROM Redis: toy_key:{old_hash}
Step 10:  SET Redis: toy_key:{new_hash} → toy_uuid  EX 86400
Step 11:  Return new_raw_key in HTTP response
Step 12:  Parent app connects to toy via BLE
Step 13:  Parent app writes new raw key to CHAR_API_KEY
Step 14:  Toy writes new raw key to NVS (overwrites old)
Step 15:  Toy reconnects to MQTT with new key → authenticated
```

### Offline Recovery (Toy Not Reachable via BLE)

```
Scenario: Key is revoked but BLE provisioning has not yet completed.

Toy boots → reads old raw key from NVS → sends to EMQX → backend hashes it
Backend: old hash exists in DB but revoked = true → DENY
EMQX: disconnects the toy

Toy firmware: on auth failure, re-enters BLE provisioning mode
Parent sees: "Toy offline — re-provision required"
Parent re-runs BLE provisioning with new key from the rotate endpoint
```

### Grace Period (Optional)

For smoother rotation, a grace period can be implemented:

```
When new key is created:
  old key: revoked = false → revoked_at = (now + 5 minutes)
  new key: immediately active

During grace period:
  both old and new hash resolve successfully in auth handler
  toy continues working while BLE re-provisioning is arranged

After grace period:
  old key: revoked = true
  only new key authenticates
```

This prevents connectivity gaps for toys that are mid-session during rotation.

---

## Data Transformation Summary

The same secret transforms across the lifecycle:

```
                    FORM                    LOCATION
─────────────────────────────────────────────────────────────────
Factory time        (nothing)               NVS: empty
                    (nothing)               DB: no api_keys row

Claim time          raw key generated       Backend memory only (ephemeral)
                    sha256(raw_key)         PostgreSQL api_keys.key_hash
                    sha256(raw_key)         Redis toy_key:{hash}
                    raw key returned        HTTP response body (HTTPS)

Parent app          raw key in memory       Phone RAM (transient)

BLE provisioning    raw key transmitted     BLE characteristic write (local)
                    raw key written         NVS api_key entry (flash)

Every MQTT boot     raw key read            NVS
                    raw key sent            MQTT CONNECT password (TLS)
                    sha256 computed         Backend auth handler (transient)
                    sha256 looked up        Redis (fast path)
                    sha256 matched          PostgreSQL (fallback)

Key rotation        old raw key             Replaced in NVS via BLE
                    old sha256              api_keys.revoked = true
                    old sha256              Deleted from Redis
                    new raw key             New NVS entry
                    new sha256              New api_keys row
                    new sha256              New Redis entry
─────────────────────────────────────────────────────────────────
```

**The invariant:** SHA-256 hash of the raw key is the only form that persists in server-side storage. The raw key exists only in ESP32 NVS flash and transiently in the parent app during BLE provisioning.

---

## Quick Reference

**"Does the backend ever store the raw key?"**
No. It is generated, returned in the claim response, and discarded. Only the SHA-256 hash is persisted.

**"Why does the toy store the raw key and not the hash?"**
MQTT authentication requires sending the original value as a password. The backend hashes what it receives. Storing a hash would cause double-hashing, and auth would always fail.

**"When can a key be used to connect?"**
Only when `api_keys.revoked = false` for the corresponding hash. Revocation is immediate — the next MQTT connect attempt is denied.

**"What happens if the toy is off when the key is rotated?"**
The toy will fail to connect on next boot (old key is revoked). It falls back to BLE provisioning mode. Parent opens the app, completes BLE re-provisioning with the new key. Normal operation resumes.

**"How many keys can a toy have?"**
Multiple rows in `api_keys` for the same `toy_id` are allowed (rotation history). Only rows with `revoked = false` grant access. After rotation, old rows are marked revoked but kept for audit trail.

---

*Boboloo Backend — Internal Technical Reference*
*Generated: 2026-05-25*
