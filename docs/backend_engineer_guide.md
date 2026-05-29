# Boboloo — Backend Engineer Guide

**Audience:** Backend engineers working on the Python/FastAPI codebase  
**Assumes:** Familiarity with FastAPI, SQLAlchemy, Redis, PostgreSQL, async Python  
**Version:** 1.0 | **Date:** 2026-05-29

---

## System Architecture

Four independent Docker containers communicate exclusively through Redis queues. No direct inter-process calls.

```
                         ┌────────────────────────────────────────────────┐
                         │                    REDIS                        │
                         │                                                  │
                         │  ai_interaction_queue     outbound_queue         │
                         │  ai_interaction_queue:processing                 │
                         │  ai_interaction_queue:failed                     │
                         │  toy_status_queue         toy_status_queue:*     │
                         │  toy_key:{hash}           child:{id}             │
                         │  settings:{id}            toy:{toy_id}           │
                         │  toy:status:{device_id}   rate:{...}             │
                         │  ai_worker:heartbeat      status_worker:heartbeat│
                         └────────┬───────────────────┬────────────────────┘
                                  │                   │
          ┌───────────────────────┼───────────────────┼──────────────────────────┐
          │                       │                   │                          │
   ┌──────▼──────┐        ┌───────▼──────┐    ┌───────▼──────┐    ┌─────────────▼────────┐
   │  Backend    │        │  AI Worker   │    │  Status      │    │  MQTT Gateway        │
   │  API :8080  │        │              │    │  Worker      │    │                      │
   │  (FastAPI)  │        │  handlers.py │    │              │    │  gateway.py          │
   └─────────────┘        └──────────────┘    └──────────────┘    └──────────────────────┘
         │                       │                   │                      │         ▲
    HTTP/WS                  OpenAI               DB writes             MQTT pub   MQTT sub
         │                       │                   │                      │         │
   [Parent App]           [api.openai.com]    [PostgreSQL]          [EMQX Broker]
   [Admin Panel]
   [Factory Floor]
   [ESP32 Toy]
```

### Entry Points

| Process | Command | File |
|---------|---------|------|
| API Server | `gunicorn -k uvicorn.workers.UvicornWorker app.main:app` | `app/main.py` |
| MQTT Gateway | `python -m app.mqtt_gateway` | `app/mqtt_gateway/gateway.py` |
| AI Worker | `python -m app.workers.worker` | `app/workers/worker.py` |
| Status Worker | `python -m app.workers.status_worker` | `app/workers/status_worker.py` |

---

## Authentication Architecture

Five separate authentication mechanisms for five different caller types.

### 1. Parent Authentication — Firebase JWT

**Used by:** `/api/v1/parent/*`, `/api/v1/toy/claim/*`, `/api/v1/analytics/*`  
**File:** `app/auth/firebase_auth.py` → `get_current_parent()`  
**Header:** `Authorization: Bearer <firebase_jwt>`

Flow:
```
Request arrives
  → firebase_admin.auth.verify_id_token(token, check_revoked=True)
  → Extract uid, email from decoded token
  → SELECT Parent WHERE firebase_uid = uid
  → If not found: INSERT Parent (auto-registration with IntegrityError retry)
  → Return Parent ORM object
```

Auto-registration handles first-time Firebase users transparently. The `IntegrityError` retry handles the race condition where two simultaneous requests try to create the same parent row.

### 2. Toy Machine Authentication — SHA-256 API Key

**Used by:** `/api/v1/toy/runtime/*`  
**File:** `app/auth/toy_key_validator.py` → `resolve_toy_by_key()`  
**Header:** `X-Toy-Key: <raw_key>`

Flow:
```
raw_key received
  → Validate length >= 20 chars
  → key_hash = SHA-256(raw_key)
  → Redis GET toy_key:{key_hash}
    → Hit: db.get(Toy, toy_id) → validate status=ACTIVE, is_active=True → return Toy
    → Miss: SELECT APIKey WHERE key_hash=hash AND revoked=false
      → Found: validate Toy → self-heal Redis (SET toy_key:{hash} = toy.id EX 86400) → return Toy
      → Not found: 401 "Invalid toy key"
```

The Redis self-heal means that after one cache miss, all subsequent requests hit Redis. Keys expire after 24h and rebuild on next miss.

Key rotation (`app/services/toy_claim_service.py:rotate_key`): Old key hashes are explicitly deleted from Redis on rotation, providing immediate revocation. The DB flag `revoked=True` handles the DB fallback path.

### 3. Admin Authentication — Double Layer

**Used by:** `/sys/control/*`  
**Files:** `app/auth/admin_internal.py` + `app/auth/admin_auth.py`

Both checks must pass:

| Layer | Header | Validation |
|-------|--------|------------|
| Layer 1 | `X-Admin-Secret: <ADMIN_INTERNAL_SECRET>` | `hmac.compare_digest()` — timing-safe |
| Layer 2 | `Authorization: Bearer <firebase_jwt>` | Firebase verify + `role=admin` custom claim |

### 4. Factory Authentication — Secret Header

**Used by:** `/api/v1/factory/*`  
**File:** `app/routes/factory_routes.py` (inline check)  
**Header:** `factory-secret: <FACTORY_SECRET_KEY>`

**Known issue:** Current implementation uses `!=` comparison, not `hmac.compare_digest()`. This is a timing attack vulnerability. Fix before production.

### 5. MQTT Auth — EMQX HTTP Plugin

**Used by:** EMQX broker (not client-facing)  
**File:** `app/routes/mqtt_auth_routes.py`

EMQX calls `POST /internal/mqtt/auth` for every new toy MQTT connection. The endpoint:
1. Verifies `X-Mqtt-Auth-Secret` header via `hmac.compare_digest()` 
2. Receives `{clientid, username, password}` in body
3. Calls `resolve_toy_by_key(password, db)`
4. Cross-checks: `toy.factory_device_id == username`
5. Returns inline ACL limiting the toy to its own topic subtree

The gateway service account is authenticated via EMQX's built-in user database, not via this endpoint.

### Authentication Summary Table

| Route Group | Auth Dependency | Notes |
|-------------|----------------|-------|
| `/api/v1/parent/*` | `get_current_parent` | Firebase JWT, auto-registers parent |
| `/api/v1/toy/claim/*` | `get_current_parent` | Firebase JWT |
| `/api/v1/toy/runtime/*` | `verify_toy` | SHA-256 API key via Redis + DB |
| `/api/v1/analytics/*` | `analytics_ready_guard` | Firebase JWT + child/analytics check |
| `/api/v1/interaction-settings/*` | None | **Open — no auth** |
| `/api/v1/factory/*` | Inline header check | `factory-secret` header |
| `/sys/control/*` | `verify_admin_internal` + `get_current_admin` | Double auth |
| `/internal/mqtt/auth` | `_verify_emqx_secret` | EMQX shared secret |
| `/internal/run-analytics` | `verify_internal` | `X-Internal-Secret` header |

---

## Toy Claim Flow

The claim flow (`app/services/toy_claim_service.py:claim_toy`) is the most security-sensitive endpoint. A `SELECT FOR UPDATE` row lock prevents double-claiming.

```
POST /api/v1/toy/claim
  Body: {factory_device_id: "TOY-A1B2C3"}
  Auth: Firebase JWT

Step 1: SELECT Toy WHERE factory_device_id = "TOY-A1B2C3" FOR UPDATE
  → 404 if not found (toy not provisioned)

Step 2: Assert toy.status == PROVISIONED
  → 400 "Toy already claimed" if not PROVISIONED

Step 3: SELECT Child WHERE parent_id = parent.id AND is_deleted = false
  → 400 "Create child profile first" if no child

Step 4: Generate raw_api_key = secrets.token_urlsafe(32)
        key_hash = SHA-256(raw_api_key)

Step 5: INSERT APIKey(key_hash=key_hash, toy_id=toy.id, revoked=False)
        UPDATE Toy SET status=ACTIVE, owner_parent_id=..., active_child_id=..., claimed_at=now()

Step 6: COMMIT

Step 7: SET Redis toy_key:{key_hash} = toy.id EX 86400

Step 8: Return {toy_uuid, toy_api_key: raw_api_key, status: "claimed"}
        ↑ This is the ONLY time the raw key is returned. It is never stored.
```

The raw key is returned to the parent app exactly once. The app sends it to the toy via BLE. After this point, the key is only ever seen as its SHA-256 hash.

---

## API Key Lifecycle

```
Factory provisions toy
  → Toy record created: status=PROVISIONED, no api_keys row

Parent claims toy
  → raw_key generated with secrets.token_urlsafe(32)
  → SHA-256 hash stored in api_keys table
  → Raw key cached in Redis: toy_key:{hash} → toy.id  (24h TTL)
  → Raw key returned to parent app ONE TIME ONLY

Toy uses key
  → HTTP path: X-Toy-Key header → SHA-256 → Redis lookup → toy resolved
  → MQTT path: password field → SHA-256 → Redis lookup → toy resolved

Redis key expires (24h)
  → Next request hits DB fallback → self-heals Redis
  → No interruption to toy operation

Parent rotates key
  → SELECT all non-revoked key_hashes for this toy
  → DELETE toy_key:{hash} from Redis for each old hash (immediate revocation)
  → UPDATE APIKey SET revoked=True for all existing keys
  → Generate new raw_key → new hash → INSERT new APIKey
  → Cache new hash in Redis
  → Return new raw_key to parent app (once only)
  → Toy's next MQTT connect fails with rc=4 → toy enters BLE provisioning mode

Factory reset (not yet implemented)
  → Parent must rotate key and re-provision toy via BLE
```

---

## Redis Queue System

All queues are implemented in `app/core/job_queue.py` using the `_BaseQueue` base class.

### Queue Pattern: Reliable At-Least-Once Delivery

```
Producer:                         Consumer (worker):
  RPUSH main_queue job   →   LMOVE main_queue → processing_queue (atomic)
                                  process job
                                  success → LREM processing_queue job (ack)
                                  failure (attempt < max) → LREM + RPUSH main_queue (retry)
                                  failure (attempt >= max) → LREM + RPUSH failed_queue (DLQ)

On worker restart:
  LMOVE processing_queue → main_queue (recover stuck jobs)
```

The `LMOVE` operation is atomic. If a worker crashes mid-processing, the job stays in `:processing` and is recovered on the next worker start.

### Queue Reference

| Queue Key | Written By | Read By | Max Retries |
|-----------|-----------|---------|-------------|
| `ai_interaction_queue` | MQTT Gateway, ToyRuntimeService | AI Worker | 3 |
| `ai_interaction_queue:processing` | AI Worker (LMOVE) | AI Worker (ack/recover) | — |
| `ai_interaction_queue:failed` | AI Worker | Manual inspection | — |
| `toy_status_queue` | MQTT Gateway | Status Worker | 2 |
| `toy_status_queue:processing` | Status Worker (LMOVE) | Status Worker | — |
| `toy_status_queue:failed` | Status Worker | Manual inspection | — |
| `outbound_queue` | AI Worker, OTA Service | MQTT Gateway | — |

### Job Payload Formats

**ai_interaction_queue — MQTT Gateway path:**
```json
{"type": "process_child_interaction", "payload": {"device_id": "TOY-A1B2C3", "question": "..."}, "attempt": 1}
```

**ai_interaction_queue — HTTP runtime path:**
```json
{"type": "process_child_interaction", "payload": {"toy_id": "TOY-A1B2C3", "child_id": "uuid", "conversation_id": "uuid", "question": "...", "settings": {...}}, "attempt": 1}
```

**toy_status_queue:**
```json
{"type": "process_toy_status", "payload": {"device_id": "TOY-A1B2C3", "data": {"battery_level": 80}}, "attempt": 1}
```

**outbound_queue:**
```json
{"topic": "boboloo/toy/TOY-A1B2C3/audio/out", "payload": "The sky is blue...", "qos": 1}
```

---

## Worker System

### AI Worker (`app/workers/worker.py`)

Polls `ai_interaction_queue`. Dispatches to `handle_interaction()` in `handlers.py`.

Two code paths in `handlers.py`:

**MQTT Gateway path** (`payload` has `device_id` key):
```
_handle_from_device():
  1. SELECT Toy WHERE factory_device_id = device_id
  2. Validate: status=ACTIVE, is_active=True, active_child_id set
  3. SELECT InteractionSettings WHERE child_id = toy.active_child_id
  4. GET or CREATE Conversation for today
  5. INSERT Message(role=user, content=question)
  6. COMMIT
  7. → _run_ai_interaction()
```

**HTTP runtime path** (`payload` has `toy_id`, `child_id`, `conversation_id` keys):
```
_handle_from_payload():
  Steps 1-5 already done by ToyRuntimeService before enqueueing
  → _run_ai_interaction() directly
```

**Shared AI core `_run_ai_interaction()`:**
```
1. db.get(Child, child_id)
2. db.get(Conversation, conversation_id)
3. SELECT last 10 Messages for conversation (ordered by created_at)
4. Strip last message from history (it's the user question — passed separately)
5. AIService.generate_child_reply(question, child_age, interests, settings, history)
6. INSERT Message(role=assistant, content=answer)
7. COMMIT
8. OutboundQueue.push(topic="boboloo/toy/{device_id}/audio/out", payload=answer, qos=1)
```

**Heartbeat:** `SET ai_worker:heartbeat "1" EX 30` every 10 seconds. If this key disappears, the worker is down.

### Status Worker (`app/workers/status_worker.py`)

Polls `toy_status_queue`. Dispatches to `handle_toy_status()` in `handlers.py`.

```
handle_toy_status():
  1. HSET toy:status:{device_id} {online, last_seen, battery_level?, wifi_signal?, firmware_version?, ota_status?}
  2. EXPIRE toy:status:{device_id} 120

  If ota_status present:
    If ota_status == "success" AND firmware_version present:
      UPDATE toys SET firmware_version = new_version WHERE factory_device_id = device_id
    If ota_status in ("failed", "rollback"):
      Log warning only — no DB write
```

Note: Regular heartbeat status messages (no `ota_status`) do NOT write to the database. Only the HTTP `/heartbeat` endpoint does a DB write (throttled to once per 60s).

---

## MQTT Gateway

**File:** `app/mqtt_gateway/gateway.py`  
**Design principle:** No database access. Stateless message router.

The gateway is the single process that owns the EMQX connection. All other processes communicate with it via Redis.

```
Startup:
  client = gmqtt.Client("boboloo-gateway")
  client.set_auth_credentials(MQTT_USERNAME, MQTT_PASSWORD)
  client.connect(MQTT_HOST, MQTT_PORT, ssl=MQTT_USE_TLS, keepalive=60)
  client.subscribe("boboloo/toy/+/audio/in", qos=1)
  client.subscribe("boboloo/toy/+/status", qos=1)
  asyncio.create_task(_drain_outbound(client))

Inbound on_message():
  Parse topic → extract device_id, suffix
  "audio/in" → validate JSON, extract text, push to ai_interaction_queue
  "status"   → validate JSON, push to toy_status_queue

Outbound _drain_outbound():
  loop:
    item = await OutboundQueue.pop(timeout=2)  ← blocking, zero CPU when idle
    client.publish(item["topic"], item["payload"], qos=item["qos"])

Shutdown (SIGTERM/SIGINT):
  _stop_event.set()
  drain_task.cancel()
  client.disconnect()
```

**Important:** The gateway must run as a single instance. Multiple gateway instances would each receive MQTT messages (all subscribed to the same wildcard), causing duplicate jobs in the queue.

---

## PostgreSQL Schema Overview

All PKs are UUIDs. Async SQLAlchemy with asyncpg driver.

```
parents ──────────────────────────────────────────────────────────┐
  id (PK)                                                          │
  firebase_uid (UNIQUE, indexed)                                   │
  email, name, is_active                                           │
        │                                                          │
        │ 1:1 (unique FK)                                          │ 1:many
        ▼                                                          ▼
children                                                          toys
  id (PK)                                                           id (PK)
  parent_id → parents                                               factory_device_id (UNIQUE)
  name, age, birth_date, guardian_name                              owner_parent_id → parents
  interests (JSONB, GIN)                                            active_child_id → children
  keywords_filter (JSONB, GIN)                                      status (PROVISIONED/ACTIVE/DISABLED)
  focus_topics (JSONB)                                              firmware_version, hardware_revision
  onboarding_completed                                              claimed_at, last_seen, manufactured_at
  is_deleted, deleted_at                                            factory_batch
        │                                                               │
        ├── 1:1 → interaction_settings                                  └── 1:many → api_keys
        ├── 1:1 → child_analytics                                           id, key_hash (UNIQUE), revoked
        ├── 1:1 → child_streaks
        ├── 1:many → analytics_history
        ├── 1:many → child_vocabulary_memory
        │     word (UNIQUE per child), first_seen, last_seen, usage_count
        └── 1:many → conversations
              id, conversation_date (UNIQUE per child per day)
                  └── 1:many → messages
                        role (user/assistant), content, created_at
```

**Standalone tables:**
- `firmware_releases`: version, s3_key, sha256, file_size, is_stable
- `audit_logs`: soft-reference parent_id, child_id (no FK constraints)

**Key constraints:**
- `children.parent_id` is UNIQUE — one child per parent
- `conversations (child_id, conversation_date)` is UNIQUE — one conversation per child per day
- `child_vocabulary_memory (child_id, word)` is UNIQUE
- `api_keys` has composite index on `(key_hash, revoked)` for fast validated lookups

---

## Redis Cache Reference

| Key Pattern | Value | TTL | Purpose |
|-------------|-------|-----|---------|
| `toy_key:{sha256_hash}` | toy.id (string) | 86400s | API key → toy mapping |
| `child:{child_id}` | JSON payload | 300s | Child profile cache |
| `settings:{child_id}` | JSON payload | 300s | Interaction settings cache |
| `toy:{toy_id}` | Hash: online, last_seen | 120s | Online presence (HTTP heartbeat) |
| `toy:status:{device_id}` | Hash: full telemetry | 120s | Online presence (MQTT heartbeat) |
| `ai_worker:heartbeat` | "1" | 30s | Worker liveness check |
| `status_worker:heartbeat` | "1" | 30s | Worker liveness check |
| `rate:{type}:{id}:{path}` | Integer counter | 60s | Rate limiting |

---

## Analytics Pipeline

### Nightly Batch — `app/services/analytics_batch_service.py`

Scheduled via APScheduler in `app/main.py` at 02:00 UTC daily. Also triggerable via `POST /internal/run-analytics`.

**Known issue:** APScheduler runs in the API process. If the API scales to multiple replicas, the batch runs N times simultaneously. Fix: move the scheduler to a dedicated cron container.

```
run_analytics_batch():
  SELECT all children WHERE is_deleted = false

  For each child (max 10 concurrent via asyncio.Semaphore):
    process_child(child):

      1. SELECT Conversation WHERE child_id = X AND conversation_date = today
      2. update_conversation_streak(talked_today = conversation exists)
            If talked today:
              If streak is None: create with current_streak=1
              If last_conversation_date == yesterday: current_streak += 1
              If older: reset current_streak = 1, new streak_started_at
              Update longest_streak = max(longest, current)
      3. COMMIT (streak only if < 3 messages)

      4. SELECT user Messages for today's conversation
      5. If len(user_messages) < 3: skip vocabulary analytics

      6. SELECT existing ChildVocabularyMemory words for child
      7. generate_analytics(text_list, existing_words)
            spaCy lemmatization → filter to content words (nouns, verbs, adj, adv)
            Returns: {TotalWordsCount, UniqueWordsCount, NewWordsCount, UniqueWordsList, NewWordsList}

      8. UPSERT ChildVocabularyMemory:
            New words: INSERT (first_seen=today, usage_count=1)
            Existing: UPDATE (usage_count += 1, last_seen = today)

      9. UPSERT ChildAnalytics (breakdown_json with vocabulary data)

      10. UPSERT AnalyticsHistory (same data keyed by child + date)

      11. COMMIT
```

### Analytics Guard (`app/auth/analytics_guard.py`)

Used on analytics routes. Verifies parent has a child and loads the current analytics record. If `ChildAnalytics` doesn't exist yet, returns a blank record with `updated_at=None` rather than 404.

---

## OTA Backend Flow

```
1. CI/CD builds firmware.bin and signs it:
   espsecure.py sign_data --version 2 firmware.bin

2. Upload signed binary to S3:
   s3://{S3_FIRMWARE_BUCKET}/releases/1.2.3/boboloo-1.2.3-signed.bin

3. Admin registers release:
   POST /sys/control/ota/releases
   {version, s3_key, sha256, file_size, is_stable: false}
   → Backend HEAD requests S3 to verify object exists
   → INSERT FirmwareRelease

4. QA validates on test devices

5. Admin marks stable:
   POST /sys/control/ota/releases/1.2.3/stable
   → UPDATE FirmwareRelease SET is_stable = True

6. Admin pushes OTA:
   Single: POST /sys/control/ota/push {target: "single", device_id, version}
   Batch:  POST /sys/control/ota/push {target: "batch", from_version, to_version}

7. ota_service.push_ota_single() / push_ota_batch():
   → Verify toy exists and is ACTIVE
   → Skip if already on target version
   → _generate_presigned_url(release.s3_key, expiry=1800s)
   → _enqueue_ota_command(): RPUSH outbound_queue {topic: .../cmd, payload: OTA JSON}

8. MQTT Gateway drains outbound_queue:
   → client.publish("boboloo/toy/TOY-A1B2C3/cmd", ota_command_json, qos=1)

9. Toy downloads, flashes, reboots

10. Toy publishes status report to boboloo/toy/TOY-A1B2C3/status:
    {"ota_status": "success", "firmware_version": "1.2.3"}

11. Status Worker processes toy_status_queue:
    handle_toy_status() → UPDATE toys SET firmware_version = "1.2.3"
```

---

## Production Deployment Architecture

### Docker Compose (Current)

```yaml
backend      → port 8080, gunicorn + uvicorn, 1 worker
mqtt_gateway → python -m app.mqtt_gateway
worker       → python -m app.workers.worker
status_worker → python -m app.workers.status_worker
redis        → redis:7-alpine, appendonly yes, appendfsync everysec
postgres     → postgres:15-alpine
```

### Scaling Rules

| Process | Can Scale? | Notes |
|---------|-----------|-------|
| `backend` | Yes (stateless) | APScheduler runs on every replica — fix before scaling |
| `mqtt_gateway` | **No — single instance only** | Multiple instances = duplicate jobs |
| `worker` (AI) | Yes | Redis queue distributes jobs |
| `status_worker` | Yes | Redis queue distributes jobs |
| `postgres` | Requires read replicas setup | pool_size=5 per process |
| `redis` | Requires Redis Cluster | Currently single instance |

### Environment Variables

All loaded from `.env` via pydantic-settings (`app/core/config.py`):

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `DATABASE_URL` | Yes | — | `postgresql+asyncpg://...` |
| `REDIS_URL` | Yes | — | `redis://...` |
| `OPENAI_API_KEY` | Yes | — | |
| `FIREBASE_CREDENTIALS_PATH` | No | None | Path to service account JSON |
| `FACTORY_SECRET_KEY` | Yes | — | Header auth for factory routes |
| `INTERNAL_CRON_SECRET` | Yes | — | Header auth for /internal routes |
| `ADMIN_INTERNAL_SECRET` | Yes | — | Double-auth for admin routes |
| `MQTT_HOST` | No | `broker.hivemq.com` | EMQX/HiveMQ broker |
| `MQTT_PORT` | No | `8883` | TLS port |
| `MQTT_USERNAME` | No | None | Gateway service account |
| `MQTT_PASSWORD` | No | None | Gateway service account |
| `MQTT_USE_TLS` | No | `True` | Must be True in production |
| `MQTT_AUTH_SECRET` | No | `""` | **Must be set** — empty string rejects ALL EMQX auth requests |
| `MQTT_GATEWAY_CLIENT_ID` | No | `boboloo-gateway` | |
| `S3_FIRMWARE_BUCKET` | No | `""` | Required for OTA |
| `S3_PRESIGN_EXPIRY` | No | `1800` | Pre-signed URL TTL in seconds |
| `AWS_REGION` | No | `us-east-1` | |
| `AWS_ACCESS_KEY_ID` | No | None | Leave unset to use IAM role |
| `AWS_SECRET_ACCESS_KEY` | No | None | Leave unset to use IAM role |
| `SENTRY_DSN` | No | None | |
| `CORS_ORIGINS` | No | `"*"` | **Must be set** to specific domains in production |
| `ENVIRONMENT` | No | `development` | `production` disables Swagger + dev endpoints |

### Database Migrations

Always run migrations before deploying a new backend version:

```bash
# Apply all pending migrations
alembic upgrade head

# Generate new migration after model changes
alembic revision --autogenerate -m "description"

# Roll back one step
alembic downgrade -1
```

### Healthchecks

| Process | Check | Interval |
|---------|-------|----------|
| `backend` | `GET /health` → `{"status": "ok"}` | 30s |
| `redis` | `redis-cli ping` | 10s |
| `postgres` | `pg_isready` | 10s |
| `mqtt_gateway` | **None configured** — gap to address |
| `worker` | **None configured** — check `ai_worker:heartbeat` in Redis |
| `status_worker` | **None configured** — check `status_worker:heartbeat` in Redis |

Worker liveness can be verified by checking Redis:
```bash
redis-cli GET ai_worker:heartbeat      # "1" if alive
redis-cli GET status_worker:heartbeat  # "1" if alive
```

---

## Rate Limiting

Implemented as middleware in `app/middleware/rate_limiter.py`.

| Client Type | Limit | Window | Identifier |
|-------------|-------|--------|------------|
| Toy runtime routes | 20 req | 60s | toy UUID from Redis |
| Authenticated user | 60 req | 60s | Firebase UID |
| IP fallback | 100 req | 60s | client IP |

Exempt: `GET /` and `GET /health`

**Known issue:** There is a race condition between `INCR` and `EXPIRE`. If the process crashes between these two commands, the key has no TTL and permanently rate-limits that client. The current code mitigates the most common case (two concurrent requests) by using INCR atomically, but the crash window exists.

Fix: replace INCR + EXPIRE with a Lua script:
```lua
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return count
```

---

## Known Issues and Technical Debt

| Issue | Location | Severity |
|-------|----------|---------|
| Factory route uses `!=` instead of `hmac.compare_digest()` | `factory_routes.py:37,87` | Security — timing attack |
| APScheduler runs in API process — fires N times if API scales | `main.py:104-110` | Architecture |
| MQTT gateway has no healthcheck | `docker-compose.yml` | Observability |
| AI/Status workers have no healthcheck | `docker-compose.yml` | Observability |
| `/api/v1/interaction-settings/*` has no auth | `interaction_settings_routes.py` | Security |
| `CORS_ORIGINS = "*"` by default | `config.py:71` | Security |
| `MQTT_AUTH_SECRET` defaults to empty string | `config.py:46` | Security |
| Rate limit INCR/EXPIRE not atomic on crash | `rate_limiter.py:84-87` | Reliability |
| Analytics guard returns blank analytics with `updated_at=None` | `analytics_guard.py:38-42` | Data quality |
