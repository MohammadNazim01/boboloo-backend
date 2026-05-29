# Boboloo Backend — Complete Technical Reference

**Version:** 1.0.0  
**Date:** 2026-05-25  
**Stack:** Python 3.11 · FastAPI · PostgreSQL · Redis · EMQX (MQTT) · OpenAI · Firebase · AWS S3

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Process Architecture](#2-process-architecture)
3. [Environment Variables](#3-environment-variables)
4. [Database Schema](#4-database-schema)
5. [Redis Key Reference](#5-redis-key-reference)
6. [Authentication System](#6-authentication-system)
7. [Complete API Reference](#7-complete-api-reference)
8. [MQTT System](#8-mqtt-system)
9. [Worker System](#9-worker-system)
10. [Complete Request Flows](#10-complete-request-flows)
11. [Analytics Engine](#11-analytics-engine)
12. [OTA Firmware Update Pipeline](#12-ota-firmware-update-pipeline)
13. [Manufacturing & Provisioning Flow](#13-manufacturing--provisioning-flow)
14. [Toy Claim & BLE Provisioning Flow](#14-toy-claim--ble-provisioning-flow)
15. [Rate Limiting](#15-rate-limiting)
16. [Interaction Settings](#16-interaction-settings)
17. [Deployment](#17-deployment)
18. [Developer Tools](#18-developer-tools)

---

## 1. System Overview

Boboloo is an AI-powered children's toy backend. A physical ESP32-based toy records a child's speech, converts it to text on-device, and sends it to the backend. The backend generates a child-appropriate AI response and sends it back to the toy over MQTT. Parents use a mobile app to manage child profiles, view vocabulary analytics, and configure AI behavior.

### High-Level Architecture

```
[ESP32 Toy]  ──MQTTS──►  [EMQX Broker]  ──HTTP Auth──►  [Backend API]
                │                                              │
                │                                         [Redis]
                │                                              │
                └──◄──MQTTS──  [MQTT Gateway]  ◄────────  [AI Worker]
                                                              │
                                                        [PostgreSQL]
                                                              │
                                                       [OpenAI GPT-4o-mini]
```

### Technology Stack

| Component | Technology |
|-----------|-----------|
| API Framework | FastAPI 0.111 + Uvicorn/Gunicorn |
| Database | PostgreSQL 15 + SQLAlchemy 2.0 (asyncpg) |
| Cache / Queue | Redis 7 (redis-py async) |
| MQTT Broker | EMQX Cloud (or self-hosted) |
| MQTT Client | gmqtt 0.6.13 |
| AI | OpenAI gpt-4o-mini (max_tokens=60) |
| Auth — Parent | Firebase Admin SDK (JWT verification) |
| Auth — Toy | SHA-256 API key + Redis cache |
| Auth — Admin | Firebase JWT with role=admin claim + X-Admin-Secret header |
| Auth — Factory | X-Factory-Secret header |
| Auth — MQTT | EMQX HTTP auth plugin → /internal/mqtt/auth |
| NLP | spaCy en_core_web_sm + VADER + wordfreq |
| Firmware Storage | AWS S3 (boto3) |
| Monitoring | Sentry SDK |
| Migrations | Alembic |

---

## 2. Process Architecture

The system runs as **four independent Docker containers** that communicate exclusively through Redis.

```
┌──────────────────────────────────────────────────────────────┐
│                         REDIS                                 │
│  ai_interaction_queue  toy_status_queue  outbound_queue       │
│  toy_key:{hash}  child:{id}  settings:{id}  toy:{id}         │
└──────────────────────────────────────────────────────────────┘
        ▲          │         ▲        │         ▲        │
        │          │         │        │         │        │
  ┌─────┴─────┐   │   ┌─────┴────┐  │   ┌─────┴──────┐ │
  │  Backend  │   │   │  AI      │  │   │  Status    │ │
  │  API      │   │   │  Worker  │  │   │  Worker    │ │
  │  :8080    │   │   │          │  │   │            │ │
  └───────────┘   │   └──────────┘  │   └────────────┘ │
                  │                 │                    │
             ┌────┴─────────────────┴────────────────────┴───┐
             │              MQTT Gateway                       │
             │      (sole EMQX connection owner)              │
             └─────────────────────────────────────────────────┘
                              │        ▲
                           publish    subscribe
                              ▼        │
                         [EMQX Broker]
                              │        ▲
                           MQTT TLS   MQTT TLS
                              ▼        │
                          [ESP32 Toy]
```

### Process 1 — Backend API (`app.main:app`)

- Handles all HTTP requests from parent app, admin, factory, and toy machines
- Runs database migrations (Alembic)
- Validates Firebase tokens, toy API keys, admin secrets
- Pushes work to `ai_interaction_queue` (never does AI itself)
- Runs APScheduler for nightly analytics batch (02:00 UTC)
- Entry point: `gunicorn -k uvicorn.workers.UvicornWorker app.main:app --bind 0.0.0.0:8080 --workers 1 --timeout 60`

### Process 2 — MQTT Gateway (`app.mqtt_gateway`)

- Sole owner of the persistent EMQX broker connection
- Subscribes to `boboloo/toy/+/audio/in` and `boboloo/toy/+/status`
- On `audio/in` message: pushes job to `ai_interaction_queue`
- On `status` message: pushes job to `toy_status_queue`
- Drains `outbound_queue` with `blpop` (zero CPU when idle) and publishes to toy topics
- Has no database access — all business logic is in workers
- Entry point: `python -m app.mqtt_gateway`

### Process 3 — AI Worker (`app.workers.worker`)

- Polls `ai_interaction_queue` with 1-second sleep when empty
- Dispatches to `handle_interaction()` in `handlers.py`
- Handles two paths: MQTT gateway path (device_id only) and HTTP runtime path (full IDs)
- Calls OpenAI gpt-4o-mini, saves reply to DB, pushes reply to `outbound_queue`
- Retries up to 3 times (MAX_ATTEMPTS=3), then moves to DLQ
- Writes heartbeat to Redis: `ai_worker:heartbeat` every 10 seconds (TTL 30s)
- Entry point: `python -m app.workers.worker`

### Process 4 — Status Worker (`app.workers.status_worker`)

- Polls `toy_status_queue` with 0.5-second sleep when empty
- Updates Redis presence hash `toy:status:{device_id}` (TTL 120s)
- On OTA status events: updates `toys.firmware_version` in PostgreSQL
- Retries up to 2 times (MAX_ATTEMPTS=2), then moves to DLQ
- Writes heartbeat to Redis: `status_worker:heartbeat` every 10 seconds (TTL 30s)
- Entry point: `python -m app.workers.status_worker`

---

## 3. Environment Variables

All variables are loaded from `.env` via pydantic-settings.

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `DATABASE_URL` | Yes | — | `postgresql+asyncpg://user:pass@host:5432/db` |
| `REDIS_URL` | Yes | — | `redis://host:6379` |
| `OPENAI_API_KEY` | Yes | — | OpenAI API key for gpt-4o-mini |
| `FIREBASE_CREDENTIALS_PATH` | No | None | Path to Firebase service account JSON |
| `FACTORY_SECRET_KEY` | Yes | — | Shared secret for factory provisioning endpoints |
| `INTERNAL_CRON_SECRET` | Yes | — | Shared secret for `/internal/run-analytics` |
| `ADMIN_INTERNAL_SECRET` | Yes | — | Second auth factor for all admin routes |
| `MQTT_HOST` | No | `broker.hivemq.com` | EMQX broker hostname |
| `MQTT_PORT` | No | `8883` | EMQX broker port (8883 = TLS) |
| `MQTT_USERNAME` | No | None | Gateway service-account username |
| `MQTT_PASSWORD` | No | None | Gateway service-account password |
| `MQTT_USE_TLS` | No | `True` | Enable TLS on MQTT connection |
| `MQTT_GATEWAY_CLIENT_ID` | No | `boboloo-gateway` | MQTT client ID for the gateway process |
| `MQTT_AUTH_SECRET` | No | `""` | Shared secret EMQX sends to /internal/mqtt/auth |
| `AWS_REGION` | No | `us-east-1` | AWS region for S3 |
| `AWS_ACCESS_KEY_ID` | No | None | AWS credentials (omit to use IAM role) |
| `AWS_SECRET_ACCESS_KEY` | No | None | AWS credentials (omit to use IAM role) |
| `S3_FIRMWARE_BUCKET` | No | `""` | S3 bucket name for firmware binaries |
| `S3_PRESIGN_EXPIRY` | No | `1800` | Pre-signed URL TTL in seconds (30 min) |
| `SENTRY_DSN` | No | None | Sentry DSN for error monitoring |
| `CORS_ORIGINS` | No | `"*"` | Comma-separated list of allowed CORS origins |
| `ENVIRONMENT` | No | `development` | `development` or `production` |
| `POSTGRES_USER` | Docker only | `postgres` | PostgreSQL username (docker-compose) |
| `POSTGRES_PASSWORD` | Docker only | required | PostgreSQL password (docker-compose) |
| `POSTGRES_DB` | Docker only | `boboloo` | PostgreSQL database name (docker-compose) |

### Development Mode Behavior

When `ENVIRONMENT=development`:
- Swagger UI available at `/docs`, `/redoc`, `/openapi.json`
- `/api/v1/factory/dev-issue-key` endpoint is enabled
- Firebase token verification uses a hardcoded fake parent in some flows

---

## 4. Database Schema

All tables use UUID primary keys. The database is accessed via async SQLAlchemy with asyncpg driver.

### Connection Configuration

```python
AsyncSessionLocal = async_sessionmaker(
    create_async_engine(
        settings.DATABASE_URL,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        echo=False,
    ),
    expire_on_commit=False,
    autoflush=False,
)
```

### Entity Relationship Overview

```
Parent ──1:many──► Child ──1:1──► InteractionSettings
  │                  │
  │                  ├──1:many──► Conversation ──1:many──► Message
  │                  ├──1:1────► ChildAnalytics
  │                  ├──1:many──► AnalyticsHistory
  │                  ├──1:1────► ChildStreak
  │                  └──1:many──► ChildVocabularyMemory
  │
  └──1:many──► Toy ──1:many──► APIKey
                │
                └── FirmwareRelease (global, not per-toy)

AuditLog (standalone — references parent_id, child_id by UUID without FK)
```

### Table: `parents`

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | UUID | PK, default uuid4 |
| `firebase_uid` | String | UNIQUE, NOT NULL, indexed |
| `email` | String | UNIQUE, indexed |
| `name` | String | indexed |
| `is_active` | Boolean | default True |

Relationships: `children` (cascade delete-orphan), `toys`

### Table: `children`

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | UUID | PK |
| `parent_id` | UUID FK→parents | NOT NULL, UNIQUE (1 child per parent), indexed |
| `name` | String | NOT NULL |
| `age` | Integer | NOT NULL |
| `birth_date` | Date | nullable, indexed |
| `guardian_name` | String | NOT NULL |
| `interests` | JSONB | NOT NULL, default [], GIN indexed |
| `keywords_filter` | JSONB | NOT NULL, default [], GIN indexed |
| `focus_topics` | JSONB | NOT NULL, default [] |
| `onboarding_completed` | Boolean | default False |
| `is_deleted` | Boolean | default False, indexed |
| `deleted_at` | DateTime | nullable |
| `created_at` | DateTime | indexed |
| `updated_at` | DateTime | on update |

### Table: `toys`

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | UUID | PK |
| `toy_uuid` | UUID | UNIQUE, NOT NULL, indexed |
| `factory_device_id` | String | UNIQUE, NOT NULL, indexed |
| `owner_parent_id` | UUID FK→parents | nullable, indexed |
| `active_child_id` | UUID FK→children | nullable, indexed |
| `status` | Enum(ToyStatus) | default PROVISIONED |
| `is_active` | Boolean | default True |
| `claimed_at` | DateTime(tz) | nullable |
| `last_seen` | DateTime(tz) | nullable |
| `manufactured_at` | DateTime(tz) | nullable |
| `firmware_version` | String | nullable |
| `factory_batch` | String | indexed, nullable |
| `hardware_revision` | String | nullable |
| `battery_level` | Integer | nullable |
| `wifi_signal` | Integer | nullable |
| `created_at` | DateTime(tz) | indexed |
| `updated_at` | DateTime(tz) | on update |

**ToyStatus enum values:** `PROVISIONED`, `ACTIVE`, `DISABLED`

### Table: `api_keys`

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | UUID | PK |
| `key_hash` | String | UNIQUE, NOT NULL, indexed |
| `toy_id` | UUID FK→toys | NOT NULL, indexed |
| `revoked` | Boolean | default False |

**Composite index:** `idx_api_key_hash_revoked` on (key_hash, revoked)

### Table: `conversations`

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | UUID | PK |
| `child_id` | UUID FK→children | NOT NULL, indexed |
| `conversation_date` | Date | NOT NULL, indexed |
| `started_at` | DateTime(tz) | — |
| `last_activity` | DateTime(tz) | — |

**Unique constraint:** `uq_child_daily_conversation` on (child_id, conversation_date) — one conversation per child per day  
**Composite index:** `idx_conversation_child_date` on (child_id, conversation_date)

Relationships: `messages` (cascade delete-orphan)

### Table: `messages`

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | UUID | PK |
| `conversation_id` | UUID FK→conversations | NOT NULL, indexed |
| `role` | Enum(MessageRole) | NOT NULL, indexed |
| `content` | Text | NOT NULL |
| `created_at` | DateTime | default now, indexed |

**MessageRole enum:** `user`, `assistant`  
**Composite index:** `idx_conversation_created` on (conversation_id, created_at)

### Table: `child_analytics`

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | UUID | PK |
| `child_id` | UUID FK→children | UNIQUE, NOT NULL, indexed |
| `breakdown_json` | JSONB | — |
| `updated_at` | DateTime | NOT NULL, indexed |

`breakdown_json` structure:
```json
{
  "vocabulary": {
    "TotalWordsCount": 42,
    "UniqueWordsCount": 18,
    "NewWordsCount": 5,
    "UniqueWordsList": ["apple", "dog", ...],
    "NewWordsList": ["elephant", ...]
  }
}
```

### Table: `analytics_history`

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | UUID | PK |
| `child_id` | UUID FK→children | NOT NULL, indexed |
| `analytics_date` | Date | indexed |
| `breakdown_json` | JSONB | — |
| `created_at` | DateTime | indexed |

**Unique constraint:** `uq_child_daily_analytics` on (child_id, analytics_date)  
One snapshot per child per day. Written by nightly analytics batch.

### Table: `child_streaks`

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | UUID | PK |
| `child_id` | UUID FK→children | UNIQUE, NOT NULL, indexed |
| `current_streak` | Integer | NOT NULL, default 0 |
| `longest_streak` | Integer | NOT NULL, default 0 |
| `last_conversation_date` | Date | nullable |
| `streak_started_at` | Date | nullable |

Streak status computed at read time from `last_conversation_date`:
- `last_conversation_date == today` → `active`
- `last_conversation_date == yesterday` → `at_risk`
- anything older → `broken`

### Table: `child_vocabulary_memory`

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | UUID | PK |
| `child_id` | UUID FK→children | NOT NULL, indexed |
| `word` | String(64) | NOT NULL |
| `first_seen` | Date | NOT NULL, indexed |
| `last_seen` | Date | NOT NULL, indexed |
| `usage_count` | Integer | NOT NULL, default 1 |

**Unique constraint:** `uq_child_word_memory` on (child_id, word)  
**Indexes:** `idx_child_vocab_child_word_lower` on (child_id, lower(word)), `idx_vocab_child_first_seen` on (child_id, first_seen)

### Table: `interaction_settings`

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | UUID | PK |
| `child_id` | UUID FK→children | UNIQUE, NOT NULL, indexed |
| `smart_adapt_mode` | Boolean | default True |
| `custom_tune` | Boolean | default False |
| `word_complexity` | Integer | default 3 (scale 1-5) |
| `speech_speed` | Integer | default 2 (scale 1-5) |
| `new_words_per_session` | Integer | default 3 |
| `question_frequency` | String | default "balanced" |
| `topic_focus` | Integer | default 3 |
| `command_steps` | Integer | default 2 |
| `patience_level` | Integer | default 3 |
| `created_at` | DateTime(tz) | — |
| `updated_at` | DateTime(tz) | on update |

### Table: `firmware_releases`

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | UUID | PK |
| `version` | String(32) | UNIQUE, NOT NULL, indexed |
| `s3_key` | String(512) | NOT NULL |
| `sha256` | String(64) | NOT NULL |
| `file_size` | Integer | nullable |
| `is_stable` | Boolean | NOT NULL, default False |
| `release_notes` | Text | nullable |
| `created_at` | DateTime(tz) | NOT NULL |
| `created_by` | String | nullable |

### Table: `audit_logs`

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | UUID | PK |
| `parent_id` | UUID | indexed (no FK — soft reference) |
| `child_id` | UUID | indexed (no FK — soft reference) |
| `action` | String | NOT NULL |
| `event_data` | JSONB | — |
| `created_at` | DateTime | indexed |

### Database Migrations

Managed by Alembic:
```bash
alembic upgrade head              # apply all migrations
alembic revision --autogenerate -m "description"  # generate new migration
alembic downgrade -1              # roll back one step
```

---

## 5. Redis Key Reference

Redis is used for three purposes: caching, queuing, and presence/rate-limiting.

### Cache Keys

| Key Pattern | Value | TTL | Written by | Read by |
|-------------|-------|-----|-----------|---------|
| `toy_key:{sha256_hash}` | toy UUID string | 86400s (24h) | toy_claim_service, toy_key_validator | toy_key_validator, rate_limiter |
| `child:{child_id}` | JSON (id, age, interests, onboarding_completed) | 300s (5 min) | toy_runtime_service | toy_runtime_service |
| `settings:{child_id}` | JSON (word_complexity, speech_speed, question_frequency) | 300s (5 min) | toy_runtime_service | toy_runtime_service |
| `toy:{toy_id}` | Hash (online, last_seen) | 120s | toy_runtime_service.heartbeat | — |
| `toy:status:{device_id}` | Hash (online, last_seen, battery_level, wifi_signal, firmware_version, ota_status) | 120s | status_worker handler | OTA status route |
| `ai_worker:heartbeat` | "1" | 30s | AI worker heartbeat loop | monitoring |
| `status_worker:heartbeat` | "1" | 30s | Status worker heartbeat loop | monitoring |

### Queue Keys (Redis Lists)

| Key | Type | Written by | Read by | Description |
|-----|------|-----------|---------|-------------|
| `ai_interaction_queue` | List | MQTT gateway, toy_runtime_service | AI worker | Child questions awaiting AI processing |
| `ai_interaction_queue:processing` | List | AI worker (lmove) | AI worker (ack/recover) | Jobs being processed — orphaned jobs recovered on restart |
| `ai_interaction_queue:failed` | List | AI worker | Manual inspection | Dead-letter queue after 3 failed attempts |
| `toy_status_queue` | List | MQTT gateway | Status worker | Toy telemetry/heartbeat events |
| `toy_status_queue:processing` | List | Status worker (lmove) | Status worker (ack/recover) | Jobs being processed |
| `toy_status_queue:failed` | List | Status worker | Manual inspection | Dead-letter queue after 2 failed attempts |
| `outbound_queue` | List | AI worker, ota_service | MQTT gateway | Messages to publish to toy topics |

### Rate Limiting Keys

| Key Pattern | Value | TTL | Description |
|-------------|-------|-----|-------------|
| `rate:toy:{toy_id}:{path}` | Integer counter | 60s | Per-toy rate limit (20 req/60s) |
| `rate:user:{firebase_uid}:{path}` | Integer counter | 60s | Per-user rate limit (60 req/60s) |
| `rate:ip:{ip_address}:{path}` | Integer counter | 60s | Per-IP fallback rate limit (100 req/60s) |

### Queue Message Formats

**ai_interaction_queue message — MQTT path:**
```json
{
  "type": "process_child_interaction",
  "payload": {
    "device_id": "TOY-A1B2C3",
    "question": "Why is the sky blue?"
  },
  "attempt": 1
}
```

**ai_interaction_queue message — HTTP path:**
```json
{
  "type": "process_child_interaction",
  "payload": {
    "toy_id": "TOY-A1B2C3",
    "child_id": "uuid",
    "conversation_id": "uuid",
    "question": "Why is the sky blue?",
    "settings": {"word_complexity": 3, "speech_speed": 2, "question_frequency": "balanced"}
  },
  "attempt": 1
}
```

**toy_status_queue message:**
```json
{
  "type": "process_toy_status",
  "payload": {
    "device_id": "TOY-A1B2C3",
    "data": {"battery_level": 80, "wifi_signal": -65, "firmware_version": "1.2.3"}
  },
  "attempt": 1
}
```

**outbound_queue message:**
```json
{
  "topic": "boboloo/toy/TOY-A1B2C3/audio/out",
  "payload": "The sky is blue because sunlight bounces off tiny air particles!",
  "qos": 1
}
```

---

## 6. Authentication System

The system has five distinct authentication layers for five different caller types.

### 6.1 Parent Authentication (Firebase JWT)

**Used by:** All `/api/v1/parent/*`, `/api/v1/toy/claim/*`, `/api/v1/analytics/*`, `/api/v1/interaction-settings/*` routes

**How it works:**
1. Parent app authenticates with Firebase (email/password or Google OAuth)
2. Firebase returns a JWT ID token
3. Parent sends token as `Authorization: Bearer <token>` header
4. Backend calls `firebase_admin.auth.verify_id_token(token, check_revoked=True)`
5. On first successful verification, a `Parent` record is created in PostgreSQL (or fetched if existing)
6. Returns the `Parent` ORM object for use in route handlers

**Key file:** `app/auth/firebase_auth.py` — `get_current_parent()`

**Auto-registration:** If the Firebase UID is seen for the first time, a new `Parent` is created automatically. Uses `IntegrityError` retry on race condition.

**Development bypass:** When `ENVIRONMENT=development`, Firebase token verification is real (no bypass). However, the `dev-issue-key` factory endpoint creates a hardcoded `dev_test_parent_001` parent for testing.

```python
# Request header required:
Authorization: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...

# Decoded token fields used:
{
  "uid": "firebase_uid_abc123",
  "email": "parent@example.com"
}
```

### 6.2 Toy Machine Authentication (X-Toy-Key Header)

**Used by:** All `/api/v1/toy/runtime/*` routes

**How it works:**
1. Toy sends raw API key in `X-Toy-Key` header
2. Backend computes `SHA-256(raw_key)` → `key_hash`
3. Redis fast path: `GET toy_key:{key_hash}` → toy UUID
4. If Redis miss: DB query on `api_keys` where `key_hash == hash AND revoked == false`
5. If DB hit: self-heal Redis cache for next request
6. Cross-validates: toy must be `status=ACTIVE` and `is_active=True`
7. Returns the `Toy` ORM object

**Key file:** `app/auth/toy_key_validator.py` — `resolve_toy_by_key()`  
**Wrapper:** `app/auth/machine_auth.py` — `verify_toy()`

```python
# Request header required:
X-Toy-Key: xK9mP2nQ8rL4vW7yJ3hF6tB1cE5uD0sA

# Error responses:
401  "Missing toy key"
401  "Invalid toy key format"  (key length < 20)
401  "Invalid toy key"  (not found in DB)
404  "Toy not found"
403  "Toy not active"
403  "Toy disabled"
```

### 6.3 Admin Authentication (Double Layer)

**Used by:** All `/sys/control/*` routes (admin dashboard, OTA management)

**Layer 1 — X-Admin-Secret header (constant-time comparison):**
```python
# Request header required:
X-Admin-Secret: <value of ADMIN_INTERNAL_SECRET env var>
```
Uses `hmac.compare_digest()` to prevent timing attacks. Returns 401 if missing, 403 if wrong.

**Layer 2 — Firebase JWT with role=admin claim:**
```python
# Request header also required:
Authorization: Bearer <firebase_jwt_with_role_admin>

# Firebase custom claim required:
{ "role": "admin" }
```
Returns 401 for invalid token, 403 for valid token without admin role.

**Key files:** `app/auth/admin_internal.py` — `verify_admin_internal()`, `app/auth/admin_auth.py` — `get_current_admin()`

Both checks must pass for any admin route to execute.

### 6.4 Factory Authentication (X-Factory-Secret Header)

**Used by:** All `/api/v1/factory/*` routes

```python
# Request header required:
factory-secret: <value of FACTORY_SECRET_KEY env var>
```

**Warning:** Current implementation uses `!=` for comparison (not `hmac.compare_digest`). This is a timing attack vulnerability that should be fixed before production.

**Key file:** `app/routes/factory_routes.py`

### 6.5 MQTT Auth (EMQX HTTP Plugin)

**Used by:** EMQX broker when any MQTT client attempts to connect

**Not a client-facing API** — called by EMQX Cloud internally.

See [Section 8.2](#82-mqtt-authentication-flow) for full details.

### 6.6 Internal Route Authentication

**Used by:** `/internal/run-analytics`

```python
# Request header required:
X-Internal-Secret: <value of INTERNAL_CRON_SECRET env var>
```

**Key file:** `app/auth/internal_auth.py`

### Authentication Summary Table

| Route Group | Auth Method | Header | Dependency |
|-------------|-------------|--------|------------|
| `/api/v1/parent/*` | Firebase JWT | `Authorization: Bearer <token>` | `get_current_parent` |
| `/api/v1/toy/claim/*` | Firebase JWT | `Authorization: Bearer <token>` | `get_current_parent` |
| `/api/v1/toy/runtime/*` | SHA-256 API Key | `X-Toy-Key: <raw_key>` | `verify_toy` |
| `/api/v1/analytics/*` | Firebase JWT | `Authorization: Bearer <token>` | `analytics_ready_guard` |
| `/api/v1/interaction-settings/*` | None (open) | — | — |
| `/api/v1/factory/*` | Secret header | `factory-secret: <key>` | inline check |
| `/sys/control/*` | Double: Secret + Firebase Admin | Both headers required | `verify_admin_internal` + `get_current_admin` |
| `/sys/control/ota/*` | Double: Secret + Firebase Admin | Both headers required | same |
| `/internal/mqtt/auth` | EMQX shared secret | `X-Mqtt-Auth-Secret: <secret>` | inline check |
| `/internal/run-analytics` | Internal secret | `X-Internal-Secret: <secret>` | `verify_internal` |

---

## 7. Complete API Reference

Base URL (development): `http://localhost:8080`

---

### 7.1 Parent Routes — `/api/v1/parent`

**Auth:** Firebase JWT on all routes

#### `POST /api/v1/parent/signup`

Creates a child profile for the authenticated parent. Required for onboarding.

**Request Body:**
```json
{
  "name": "Emma",
  "age": 5,
  "birth_date": "2020-03-15",
  "guardian_name": "Sarah Smith",
  "interests": ["dinosaurs", "space", "drawing"],
  "keywords_filter": ["violence", "scary"],
  "focus_topics": ["science", "math"]
}
```

**Response 200:**
```json
{
  "id": "uuid",
  "name": "Emma",
  "age": 5,
  "birth_date": "2020-03-15",
  "guardian_name": "Sarah Smith",
  "interests": ["dinosaurs", "space", "drawing"],
  "keywords_filter": ["violence", "scary"],
  "focus_topics": ["science", "math"],
  "onboarding_completed": false,
  "created_at": "2025-01-20T14:31:07"
}
```

**Error Responses:** 401 (invalid token), 400 (validation error)

---

#### `GET /api/v1/parent/login`

Fetches the child profile for the authenticated parent. Used as the app login/session restore.

**Response 200:** Same as signup response, or `null` if no child created yet.

---

#### `PUT /api/v1/parent/update`

Updates the child profile.

**Request Body:** Same fields as signup, all optional.

**Response 200:** Updated child profile.

---

### 7.2 Toy Claim Routes — `/api/v1/toy`

**Auth:** Firebase JWT on all routes

#### `POST /api/v1/toy/claim`

Claims a toy for the authenticated parent. Requires child profile to exist. Returns the raw API key — this is the **only time** the key is returned.

**Request Body:**
```json
{
  "factory_device_id": "TOY-A1B2C3"
}
```

**Response 200:**
```json
{
  "toy_uuid": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "toy_api_key": "xK9mP2nQ8rL4vW7yJ3hF6tB1cE5uD0sA",
  "status": "claimed"
}
```

**Error Responses:**
- 404: Toy not provisioned by factory
- 400: Toy already claimed or unavailable
- 400: Create child profile before claiming toy

**What this does internally:**
1. Fetches toy with `SELECT FOR UPDATE` row lock
2. Verifies `status == PROVISIONED`
3. Generates `raw_api_key = secrets.token_urlsafe(32)`
4. Computes `key_hash = sha256(raw_api_key)`
5. Stores only `key_hash` in `api_keys` table
6. Sets toy `status=ACTIVE`, links to parent and child
7. Caches `toy_key:{key_hash} → toy.id` in Redis (24h TTL)
8. Returns `raw_api_key` one time only

---

#### `POST /api/v1/toy/rotate-key/{toy_id}`

Rotates the API key for a toy. Used when a key is suspected compromised or after a factory reset.

**Path parameter:** `toy_id` — the toy's UUID

**Response 200:**
```json
{
  "toy_api_key": "newRawKey...",
  "status": "rotated"
}
```

**What this does internally:**
1. Verifies parent owns this toy
2. Fetches all non-revoked key hashes
3. Marks all existing keys `revoked=True`
4. Deletes old keys from Redis (immediate revocation)
5. Generates new key, stores hash in DB and Redis
6. Returns new raw key

---

### 7.3 Toy Runtime Routes — `/api/v1/toy/runtime`

**Auth:** X-Toy-Key header on all routes

#### `POST /api/v1/toy/runtime/ask`

Main question endpoint. Toy sends child's speech text, backend queues AI processing.

**Request Body:**
```json
{
  "question": "Why do elephants have big ears?",
  "battery_level": 80,
  "wifi_signal": -65
}
```

**Response 200:**
```json
{
  "conversation_id": "uuid",
  "status": "processing"
}
```

**What this does:**
1. Validates toy status, question length (max 500 chars)
2. Loads child profile from Redis cache (or DB, 5min TTL)
3. Loads interaction settings from Redis cache (or DB, 5min TTL)
4. Gets or creates today's `Conversation` record
5. Saves user `Message` to DB
6. Pushes job to `ai_interaction_queue`
7. Commits DB transaction
8. Returns `conversation_id` immediately (non-blocking)

The AI response is delivered separately via MQTT to `boboloo/toy/{device_id}/audio/out`.

**Error Responses:**
- 401/403: Authentication failure
- 403: Toy not active
- 400: Question required / Question too long
- 400: No active child set
- 400: Complete onboarding first
- 404: Child not found
- 500: Internal server error

---

#### `POST /api/v1/toy/runtime/heartbeat`

Toy sends this periodically (every ~60s) to maintain presence status.

**Response 200:**
```json
{ "status": "alive" }
```

**What this does:**
1. Validates toy is active
2. If >60 seconds since last DB write: updates `toy.last_seen` in PostgreSQL
3. Always: sets Redis hash `toy:{toy.id}` with `{online: 1, last_seen: <iso>}` (TTL 120s)

---

#### `GET /api/v1/toy/runtime/latest-answer/{conversation_id}`

Polling endpoint for toys that use HTTP instead of MQTT for receiving replies.

**Path parameter:** `conversation_id`

**Response 200:**
```json
{
  "conversation_id": "uuid",
  "answer": "Elephants have big ears to stay cool in hot weather!",
  "ready": true
}
```

---

### 7.4 Analytics Routes — `/api/v1/analytics`

**Auth:** Firebase JWT via `analytics_ready_guard` (also verifies child has analytics data)

#### `GET /api/v1/analytics/overview`

Returns a single natural-language insight about the child's vocabulary development.

**Response 200:**
```json
{
  "weekly_focus": {
    "focus_area": "Word Diversity",
    "insight": "Your child spoke 42 words today but only 18 were unique...",
    "recommended_action": "Ask open-ended questions about things around the house...",
    "vocabulary_growth": "Your child learned 35 new words this week..."
  }
}
```

**Focus area logic (priority order):**
1. `Word Diversity` — if diversity ratio < 25% and child spoke today
2. `Vocabulary Expansion` — if trend is declining vs yesterday
3. `Word Retention` — if retention rate < 30% and 10+ lifetime words
4. `Strong Growth Day` — if 10+ new words today
5. `Consistent Learning` — default

---

#### `GET /api/v1/analytics/vocabulary`

Returns detailed vocabulary statistics with charts and streak data.

**Response 200:**
```json
{
  "summary": {
    "total_words": 42,
    "unique_words": 18,
    "new_words": 5,
    "trend": "improving"
  },
  "comparison": {
    "today": {"total_words": 42, "unique_words": 18, "new_words": 5},
    "yesterday": {"total_words": 35, "unique_words": 14, "new_words": 3},
    "today_vs_yesterday": {"total_words": "+7", "unique_words": "+4", "new_words": "+2"}
  },
  "weekly_graph": [
    {"date": "2025-01-14", "total_words": 30, "unique_words": 12, "new_words": 3},
    ...7 days...
  ],
  "monthly_graph": [
    {"week": 1, "total_words": 150, "unique_words": 60, "new_words": 18},
    {"week": 2, "total_words": 180, "unique_words": 72, "new_words": 22},
    {"week": 3, "total_words": 210, "unique_words": 85, "new_words": 25},
    {"week": 4, "total_words": 240, "unique_words": 95, "new_words": 30}
  ],
  "conversation_streak": {
    "current_streak": 7,
    "longest_streak": 14,
    "last_conversation_date": "2025-01-20",
    "streak_started_at": "2025-01-14",
    "status": "active"
  }
}
```

---

### 7.5 Interaction Settings Routes — `/api/v1/interaction-settings`

**Auth:** None (currently open — relies on child_id being known)

#### `GET /api/v1/interaction-settings/{child_id}`

Returns interaction settings for a child. Auto-creates defaults if none exist.

**Response 200:**
```json
{
  "id": "uuid",
  "child_id": "uuid",
  "smart_adapt_mode": true,
  "custom_tune": false,
  "word_complexity": 3,
  "speech_speed": 2,
  "new_words_per_session": 3,
  "question_frequency": "balanced",
  "topic_focus": 3,
  "command_steps": 2,
  "patience_level": 3
}
```

---

#### `PUT /api/v1/interaction-settings/{child_id}`

Updates interaction settings. The AI worker reads these when generating replies.

**Request Body:** Same fields as response, all optional.

---

### 7.6 Factory Routes — `/api/v1/factory`

**Auth:** `factory-secret` header matching `FACTORY_SECRET_KEY`

#### `POST /api/v1/factory/provision`

Registers a single toy device in the backend database.

**Request Body:**
```json
{
  "factory_device_id": "TOY-A1B2C3",
  "firmware_version": "1.0.0",
  "hardware_revision": "REV-B",
  "batch_id": "BATCH-2025-001"
}
```

**Response 200:**
```json
{
  "toy_uuid": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "status": "PROVISIONED"
}
```

**Idempotent:** If the device_id already exists, returns the existing record without error.

---

#### `POST /api/v1/factory/provision-batch`

Registers multiple toy devices in one request.

**Request Body:**
```json
{
  "device_ids": ["TOY-A1B2C3", "TOY-D4E5F6", "TOY-G7H8I9"],
  "firmware_version": "1.0.0",
  "hardware_revision": "REV-B",
  "batch_id": "BATCH-2025-001"
}
```

**Response 200:**
```json
{
  "batch_id": "BATCH-2025-001",
  "requested": 3,
  "created": 2,
  "duplicates": 1
}
```

---

#### `POST /api/v1/factory/dev-issue-key` *(development only)*

Creates a dev parent/child pair and issues an API key for testing. Only available when `ENVIRONMENT=development`.

**Query parameter:** `factory_device_id`

**Response 200:**
```json
{
  "toy_uuid": "uuid",
  "toy_api_key": "rawKeyForTesting",
  "device_id": "TOY-A1B2C3",
  "status": "active",
  "note": "DEV ONLY — this endpoint does not exist in production"
}
```

---

### 7.7 Admin Routes — `/sys/control`

**Auth:** Double auth — `X-Admin-Secret` header + Firebase Bearer token with `role=admin` claim  
**Note:** These routes are hidden from Swagger docs (`include_in_schema=False`)

#### `GET /sys/control/dashboard`

Returns system-wide statistics.

**Response 200:**
```json
{
  "total_parents": 1250,
  "total_children": 1198,
  "total_toys": 1050,
  "active_toys": 987,
  "total_conversations": 45230,
  "total_messages": 189000
}
```

---

#### `GET /sys/control/parents?page=1&limit=20`

Paginated list of all parent accounts.

---

#### `GET /sys/control/toys`

List of all toys with status and last seen timestamp.

---

#### `GET /sys/control/conversations/{child_id}?limit=50`

All conversations for a specific child, with messages.

---

#### `GET /sys/control/analytics/{child_id}`

Analytics data for a specific child.

---

### 7.8 OTA Routes — `/sys/control/ota`

**Auth:** Same double auth as admin routes  
**Note:** Hidden from Swagger (`include_in_schema=False`)

#### `POST /sys/control/ota/releases`

Register a new firmware release. Binary must already be in S3.

**Request Body:**
```json
{
  "version": "1.2.3",
  "s3_key": "releases/1.2.3/boboloo-1.2.3-signed.bin",
  "sha256": "a3f8d2e1b4c96f0e7d5a2b8c1f3e9d4a...",
  "file_size": 1048576,
  "is_stable": false,
  "release_notes": "Fixed BLE provisioning timeout"
}
```

**Response 201:** FirmwareRelease object

---

#### `GET /sys/control/ota/releases`

List all firmware releases, newest first.

---

#### `GET /sys/control/ota/releases/{version}`

Get a specific firmware release by version string.

---

#### `POST /sys/control/ota/releases/{version}/stable`

Mark a firmware version as stable (safe for production batch rollout).

---

#### `POST /sys/control/ota/push`

Push OTA to a single toy or a version-based batch.

**Single toy push:**
```json
{
  "target": "single",
  "device_id": "TOY-A1B2C3",
  "version": "1.2.3"
}
```

**Batch push (all toys on version X):**
```json
{
  "target": "batch",
  "from_version": "1.1.0",
  "version": "1.2.3"
}
```

**Response 200:**
```json
{
  "version": "1.2.3",
  "results": [
    {"device_id": "TOY-A1B2C3", "status": "queued"},
    {"device_id": "TOY-D4E5F6", "status": "skipped", "reason": "already_on_1.2.3"}
  ],
  "queued": 1,
  "skipped": 1,
  "errors": 0
}
```

---

#### `GET /sys/control/ota/toys/{device_id}/status`

Returns near-realtime OTA status for a toy from Redis.

**Response 200:**
```json
{
  "device_id": "TOY-A1B2C3",
  "online": true,
  "last_seen": "2025-01-20T14:31:07+00:00",
  "firmware_version": "1.1.0",
  "ota_status": "downloading",
  "battery_level": "80",
  "wifi_signal": "-65"
}
```

---

### 7.9 Internal Routes — `/internal`

**Auth:** `X-Internal-Secret` header matching `INTERNAL_CRON_SECRET`

#### `POST /internal/run-analytics`

Manually trigger the analytics batch (normally runs on APScheduler at 02:00 UTC).

**Response 200:**
```json
{ "status": "analytics batch executed" }
```

---

### 7.10 MQTT Auth Routes — `/internal/mqtt`

**Not client-facing.** Called by EMQX broker only.

#### `POST /internal/mqtt/auth`

Called by EMQX HTTP auth plugin for every new MQTT connection.

**Request (from EMQX):**
```json
{
  "clientid": "TOY-A1B2C3",
  "username": "TOY-A1B2C3",
  "password": "rawApiKey..."
}
```

**Response (to EMQX) — allow:**
```json
{
  "result": "allow",
  "acl": [
    {"permission": "allow", "action": "publish", "topic": "boboloo/toy/TOY-A1B2C3/audio/in"},
    {"permission": "allow", "action": "publish", "topic": "boboloo/toy/TOY-A1B2C3/status"},
    {"permission": "allow", "action": "subscribe", "topic": "boboloo/toy/TOY-A1B2C3/audio/out"},
    {"permission": "allow", "action": "subscribe", "topic": "boboloo/toy/TOY-A1B2C3/cmd"},
    {"permission": "deny", "action": "all", "topic": "#"}
  ]
}
```

**Response (to EMQX) — deny:**
```json
{ "result": "deny" }
```

---

#### `POST /internal/mqtt/acl`

Fallback ACL check if EMQX is configured for separate auth/authz calls.

---

### 7.11 System Routes

#### `GET /`

```json
{ "status": "Boboloo Backend Running" }
```

#### `GET /health`

```json
{ "status": "ok" }
```

Used by Docker healthcheck, load balancer health probes, and Kubernetes liveness probes.

---

## 8. MQTT System

### 8.1 Topic Hierarchy

```
boboloo/
  toy/
    {factory_device_id}/
      audio/in      ← toy PUBLISHES child's speech text  
      audio/out     ← toy SUBSCRIBES for AI replies
      status        ← toy PUBLISHES telemetry/heartbeat
      cmd           ← toy SUBSCRIBES for OTA commands
```

### 8.2 MQTT Authentication Flow

Every time a toy boots and attempts to connect to EMQX:

```
1. Toy reads raw_api_key from NVS flash
2. Toy sends MQTT CONNECT:
   - Client ID: "TOY-A1B2C3"
   - Username:  "TOY-A1B2C3"
   - Password:  "rawApiKey..."
   - Port: 8883 (TLS)

3. EMQX intercepts the CONNECT
4. EMQX POSTs to /internal/mqtt/auth with credentials

5. Backend:
   a. Verifies X-Mqtt-Auth-Secret header (constant-time)
   b. Computes SHA-256 of the password
   c. Checks Redis: GET toy_key:{hash} → toy_id (fast path, ~99%)
   d. If miss: queries api_keys table (DB fallback)
   e. If DB hit: self-heals Redis cache
   f. Cross-verifies: toy.factory_device_id == username
   g. Returns allow + inline ACL, or deny

6. EMQX enforces the inline ACL:
   - Toy can only publish/subscribe to its own topics
   - Catch-all deny on "#" prevents any other topic access

7. MQTT Gateway (which is already connected via service account):
   - Subscribes to boboloo/toy/+/audio/in
   - Subscribes to boboloo/toy/+/status
   - Receives all toy messages
```

### 8.3 MQTT Message Formats

**audio/in (toy → backend):**
```json
{
  "text": "Why is the sky blue?"
}
```

**audio/out (backend → toy):**
```
The sky is blue because sunlight bounces off tiny air molecules in the air!
```
Plain text string (not JSON).

**status (toy → backend):**
```json
{
  "battery_level": 80,
  "wifi_signal": -65,
  "firmware_version": "1.1.0",
  "ota_status": "success"
}
```

**cmd (backend → toy) — OTA command:**
```json
{
  "type": "ota",
  "version": "1.2.3",
  "url": "https://s3.amazonaws.com/bucket/releases/1.2.3/...?X-Amz-Signature=...",
  "sha256": "a3f8d2e1b4c96f0e7d5a2b8c1f3e9d4a...",
  "size": 1048576
}
```

### 8.4 Gateway Process Internals

The MQTT gateway (`app/mqtt_gateway/gateway.py`) is the only process with a connection to EMQX. It authenticates using the gateway service account credentials (`MQTT_USERNAME`/`MQTT_PASSWORD`), not via the HTTP auth endpoint.

**Inbound processing:**
- `audio/in` messages → validated (length check) → pushed to `ai_interaction_queue`
- `status` messages → pushed to `toy_status_queue`
- Unknown topic suffixes → logged and dropped

**Outbound processing:**
- Runs `_drain_outbound()` as an asyncio task
- Uses `blpop` on `outbound_queue` (blocks up to 2 seconds waiting for messages)
- Publishes each message to the specified MQTT topic with specified QoS
- Zero CPU consumption when no outbound messages are queued

**Shutdown:** Listens for SIGTERM/SIGINT, cancels drain task, disconnects cleanly.

---

## 9. Worker System

### 9.1 AI Worker (`app.workers.worker`)

**Job queue:** `ai_interaction_queue`  
**Job type handled:** `process_child_interaction`  
**Max retries:** 3  
**DLQ:** `ai_interaction_queue:failed`

**Processing loop:**
```
1. On startup: recover_stuck_jobs() — moves orphaned processing-queue jobs back to main queue
2. Loop:
   a. lmove ai_interaction_queue → ai_interaction_queue:processing (atomic)
   b. If job: dispatch to handle_interaction(payload)
   c. If success: lrem from processing queue (ack)
   d. If failure and attempt < 3: increment attempt, rpush to main queue (retry)
   e. If failure and attempt == 3: rpush to DLQ
   f. If no job: asyncio.sleep(1)
3. Heartbeat: SET ai_worker:heartbeat "1" EX 30  (every 10 seconds)
```

**Two execution paths in `handle_interaction()`:**

**Path A — MQTT Gateway path** (payload has `device_id` key):
1. DB: look up `Toy` by `device_id`
2. DB: verify toy is active and has `active_child_id`
3. DB: load `InteractionSettings`
4. DB: get or create today's `Conversation`
5. DB: save user `Message`
6. Call `_run_ai_interaction()`

**Path B — HTTP Runtime path** (payload has `toy_id`, `child_id`, `conversation_id`):
- Skip steps 1-5 (already done by `ToyRuntimeService`)
- Call `_run_ai_interaction()` directly

**`_run_ai_interaction()` (shared core):**
1. DB: load `Child` by child_id
2. DB: load `Conversation` by conversation_id
3. DB: load all messages for conversation, take last 10
4. Remove the last message from history (it's the user message just saved)
5. Call `AIService.generate_child_reply()` → OpenAI gpt-4o-mini
6. DB: save assistant `Message`
7. `OutboundQueue.push(topic, reply, qos=1)` → MQTT gateway publishes to toy

### 9.2 Status Worker (`app.workers.status_worker`)

**Job queue:** `toy_status_queue`  
**Job type handled:** `process_toy_status`  
**Max retries:** 2  
**DLQ:** `toy_status_queue:failed`

**Two actions per job:**

**Action 1 — Redis presence (always):**
```
HSET toy:status:{device_id} {
  online: "1",
  last_seen: "<iso_timestamp>",
  battery_level: "80",     (if present)
  wifi_signal: "-65",      (if present)
  firmware_version: "1.1.0", (if present)
  ota_status: "success"    (if present)
}
EXPIRE toy:status:{device_id} 120
```

**Action 2 — DB firmware_version update (only on OTA events):**
- Only runs if `ota_status` field is present in the status payload
- On `ota_status == "success"`: updates `toys.firmware_version` in PostgreSQL
- On `ota_status in ("failed", "rollback")`: logs warning only

### 9.3 AI Service — OpenAI Integration

**File:** `app/services/ai/ai_service.py`

**Model:** `gpt-4o-mini`  
**Max tokens:** 60  
**Temperature:** 0.7

**System prompt template:**
```
You are Boboloo, a friendly AI toy for children.
Child age: {child_age}
Child interests: {interests_text}
Speech speed level: {speech_speed}
Word complexity level: {complexity}

Rules:
- Use very simple language
- Maximum 2 sentences
- Be playful and encouraging
- Speak appropriately for the child's age
```

**Message history:** Last 10 messages from today's conversation (excluding the current question)

**On error:** Returns `"Sorry buddy, Boboloo is sleeping right now!"` and logs the error

---

## 10. Complete Request Flows

### 10.1 Child Asks a Question (MQTT Path)

This is the primary flow — used by physical toys.

```
Step 1   [ESP32]
         Child speaks → microphone → on-device STT
         Toy reads raw_api_key from NVS
         Publishes: MQTT → boboloo/toy/TOY-A1B2C3/audio/in
         Payload: {"text": "Why do birds fly south?"}

Step 2   [EMQX Broker]
         Broker receives publish from toy
         Verifies toy's subscription/publish ACL (already granted at connect time)
         Forwards to MQTT Gateway (which is subscribed to boboloo/toy/+/audio/in)

Step 3   [MQTT Gateway]
         on_message() called
         Extracts device_id = "TOY-A1B2C3" from topic
         Validates: question not empty, length ≤ 500 chars
         Pushes to ai_interaction_queue:
           {type: "process_child_interaction", payload: {device_id, question}, attempt: 1}

Step 4   [AI Worker]
         lmove job from ai_interaction_queue → ai_interaction_queue:processing
         Dispatches to handle_interaction({device_id: "TOY-A1B2C3", question: "..."})
         Path A (MQTT gateway path) because payload has "device_id" key

Step 5   [AI Worker — DB setup]
         SELECT Toy WHERE factory_device_id = "TOY-A1B2C3"
         Verify: status=ACTIVE, is_active=True, active_child_id IS NOT NULL
         SELECT InteractionSettings WHERE child_id = child.id
         SELECT Conversation WHERE child_id = X AND conversation_date = today
           → if not found: INSERT new Conversation
         INSERT Message(role=user, content="Why do birds fly south?")
         UPDATE toys SET last_seen = now WHERE id = toy.id
         COMMIT

Step 6   [AI Worker — AI call]
         Load Child from DB
         SELECT last 10 Messages for this conversation
         Build system prompt with child.age, child.interests, interaction_settings
         POST https://api.openai.com/v1/chat/completions
           model: gpt-4o-mini
           messages: [system_prompt, ...history[-9:], {role:user, content:question}]
           temperature: 0.7, max_tokens: 60
         Receives: "Birds fly south because it gets cold and they need warm weather to find food!"

Step 7   [AI Worker — Save and publish]
         INSERT Message(role=assistant, content="Birds fly south because...")
         COMMIT
         RPUSH outbound_queue: {topic: "boboloo/toy/TOY-A1B2C3/audio/out", payload: "Birds fly south..."}
         lrem ai_interaction_queue:processing 1 <raw_job>  (ack)

Step 8   [MQTT Gateway — outbound drain]
         blpop outbound_queue (was waiting)
         client.publish("boboloo/toy/TOY-A1B2C3/audio/out", "Birds fly south...", qos=1)

Step 9   [EMQX Broker]
         Delivers message to all subscribers of boboloo/toy/TOY-A1B2C3/audio/out

Step 10  [ESP32]
         on_message callback fires
         Toy reads the reply text
         Sends to on-device TTS
         Child hears the answer

Total latency: typically 2-4 seconds (dominated by OpenAI call ~1-2s)
```

### 10.2 Child Asks a Question (HTTP Path)

Used by simulators and future device types. Less common than MQTT path.

```
Step 1   Toy sends: POST /api/v1/toy/runtime/ask
         Headers: X-Toy-Key: rawApiKey
         Body: {question: "...", battery_level: 80, wifi_signal: -65}

Step 2   [Backend API]
         verify_toy(): SHA256 key → Redis lookup → DB fallback
         ToyRuntimeService.handle_question():
           Load child from cache
           Load settings from cache
           Get/create Conversation
           Save user Message
           Push job to ai_interaction_queue with full IDs:
             {toy_id, child_id, conversation_id, question, settings}
           COMMIT
         Return: {conversation_id: "uuid", status: "processing"}

Step 3   [AI Worker]
         Dispatches to handle_interaction({toy_id, child_id, conversation_id, ...})
         Path B (HTTP path) — skips DB setup, goes straight to _run_ai_interaction()
         OpenAI call → save reply → push to outbound_queue

Step 4   [MQTT Gateway]
         Publishes reply to boboloo/toy/TOY-A1B2C3/audio/out

Step 5   Toy polls GET /api/v1/toy/runtime/latest-answer/{conversation_id}
         OR
         Receives reply via MQTT subscription
```

### 10.3 Parent App Login Flow

```
Step 1   Parent opens Boboloo app
Step 2   App authenticates with Firebase (email/password or Google)
Step 3   Firebase returns JWT ID token
Step 4   App calls GET /api/v1/parent/login
         Header: Authorization: Bearer <firebase_jwt>
Step 5   Backend:
           verify_firebase_token(token) → {uid, email}
           SELECT Parent WHERE firebase_uid = uid
           If not found: INSERT Parent (auto-registration)
           SELECT Child WHERE parent_id = parent.id AND is_deleted = false
           Return child profile (or null if onboarding not done)
Step 6   If child profile null: redirect to onboarding flow
         If child profile present: app is ready
```

### 10.4 Nightly Analytics Batch

```
Schedule: 02:00 UTC daily (APScheduler CronTrigger)
Also triggered manually: POST /internal/run-analytics

Flow:
  1. SELECT all children WHERE is_deleted = false
  2. For each child (max 10 concurrent via Semaphore):

     a. SELECT Conversation WHERE child_id = X AND conversation_date = today
     b. update_conversation_streak():
        - If talked today: extend or reset streak based on yesterday
        - Update ChildStreak record

     c. If <3 user messages today: COMMIT (streak only) and skip vocabulary

     d. SELECT all Messages for today (user role only)
     e. SELECT existing ChildVocabularyMemory for child

     f. generate_analytics(text_list, existing_words):
        - spaCy lemmatization
        - Count total words, unique words, new words
        - Compute diversity ratio

     g. Upsert ChildVocabularyMemory:
        - New words: INSERT (first_seen=today, usage_count=1)
        - Existing words: UPDATE (usage_count += 1, last_seen = today)

     h. UPSERT ChildAnalytics (breakdown_json with vocabulary data)

     i. UPSERT AnalyticsHistory (same data, keyed by child_id + analytics_date)

     j. COMMIT
```

---

## 11. Analytics Engine

### NLP Pipeline

**File:** `app/services/analytics_engine/engine.py`  
**Library:** spaCy `en_core_web_sm` model (loaded at module import time)

**`generate_analytics(text_list, existing_words)` output:**
```json
{
  "vocabulary": {
    "TotalWordsCount": 42,
    "UniqueWordsCount": 18,
    "NewWordsCount": 5,
    "UniqueWordsList": ["apple", "dog", "sky", ...],
    "NewWordsList": ["elephant", "migrate", ...]
  }
}
```

**Processing steps:**
1. Concatenate all user messages for the day
2. spaCy tokenization + lemmatization
3. Filter to content words only (nouns, verbs, adjectives, adverbs)
4. Count total words, unique lemmas, new lemmas (not in `existing_words`)
5. Return counts and word lists

### Vocabulary Memory System

`ChildVocabularyMemory` tracks every unique content word a child has ever spoken:
- `first_seen`: when the word was first used
- `last_seen`: most recent use
- `usage_count`: total number of sessions where word appeared

Words to revisit: `usage_count == 1 AND last_seen` more than 2 days ago.

### Streak System

- Written by: nightly analytics batch
- Read by: `GET /api/v1/analytics/vocabulary`
- Streak status computed at **read time** (no nightly reset writes needed):
  - `last_conversation_date == today` → `active`
  - `last_conversation_date == yesterday` → `at_risk`
  - Older → `broken` (current_streak displayed as 0)

---

## 12. OTA Firmware Update Pipeline

### Admin Workflow

```
Step 1   CI/CD pipeline builds firmware binary
         Signs with ECDSA P-256: espsecure.py sign_data --version 2
         Computes sha256: openssl dgst -sha256 firmware.bin
         Uploads signed binary to S3: s3://bucket/releases/1.2.3/boboloo-1.2.3-signed.bin

Step 2   Admin registers release:
         POST /sys/control/ota/releases
         {version, s3_key, sha256, file_size, is_stable: false, release_notes}
         Backend verifies S3 object exists (head_object call)

Step 3   QA validates the build on test devices
         Admin marks stable:
         POST /sys/control/ota/releases/1.2.3/stable

Step 4   Admin pushes OTA:
         Single device: POST /sys/control/ota/push {target: "single", device_id, version}
         Batch: POST /sys/control/ota/push {target: "batch", from_version, version}

Step 5   Backend:
         Generates 30-minute pre-signed S3 URL for the firmware binary
         Builds OTA command: {type, version, url, sha256, size}
         Pushes to outbound_queue

Step 6   MQTT Gateway:
         Reads from outbound_queue
         Publishes to boboloo/toy/TOY-A1B2C3/cmd

Step 7   ESP32:
         Receives OTA command on /cmd topic
         Downloads firmware from pre-signed URL via HTTPS
         Verifies SHA-256 incrementally during download
         Calls esp_ota_end() → writes to inactive OTA partition
         Calls esp_ota_set_boot_partition()
         Reboots

Step 8   ESP32 on reboot:
         Boots from new firmware partition
         If MQTT connect succeeds: calls ota_mark_valid()
         If boot fails or MQTT fails: esp-idf rolls back to previous partition

Step 9   ESP32 sends status report:
         MQTT → boboloo/toy/TOY-A1B2C3/status
         {"ota_status": "success", "firmware_version": "1.2.3"}

Step 10  Status Worker:
         Processes toy_status_queue job
         Updates toys.firmware_version = "1.2.3" in PostgreSQL
         Updates Redis toy:status:{device_id}
```

### OTA Status Values

| `ota_status` value | Meaning |
|-------------------|---------|
| `downloading` | Toy is downloading the firmware |
| `verifying` | Toy is checking SHA-256 |
| `flashing` | Toy is writing to OTA partition |
| `success` | OTA completed, new firmware running |
| `failed` | OTA failed (download or verify error) |
| `rollback` | Boot validation failed, old firmware restored |

---

## 13. Manufacturing & Provisioning Flow

### Physical Manufacturing Steps

```
1. PCB assembly + ESP32 chip soldering
2. Flash firmware binary at factory:
   esptool.py write_flash 0x0 firmware.bin
   (includes bootloader, partition table, factory recovery, ota_0)

3. Run factory provisioning script (provision_toy.py or equivalent):
   POST /api/v1/factory/provision
   Headers: factory-secret: <FACTORY_SECRET_KEY>
   Body: {factory_device_id: "TOY-A1B2C3", firmware_version, hardware_revision, batch_id}

4. Backend creates Toy record:
   id = new UUID
   toy_uuid = new UUID
   factory_device_id = "TOY-A1B2C3"
   status = PROVISIONED
   owner_parent_id = NULL
   active_child_id = NULL
   manufactured_at = NOW()

5. Ship to customer
   Note: No QR code required — parent app reads factory_device_id directly from the toy over BLE.
```

### NVS Flash Contents at Time of Shipping

```
Namespace "factory":
  factory_id = "TOY-A1B2C3"   ← permanent, never changed

Namespace "boboloo":
  (empty)   ← filled during BLE provisioning
```

### ESP32 Firmware Partition Layout (8MB)

| Partition | Type | Size | Purpose |
|-----------|------|------|---------|
| `nvs` | data | 24 KB | Non-volatile key-value storage |
| `otadata` | data | 8 KB | Tracks which OTA partition is active |
| `phy_init` | data | 4 KB | RF calibration data |
| `factory` | app | 512 KB | Recovery partition (original firmware) |
| `ota_0` | app | 2 MB | First OTA slot |
| `ota_1` | app | 2 MB | Second OTA slot |
| `nvs_keys` | data | 4 KB | NVS encryption keys (optional) |

---

## 14. Toy Claim & BLE Provisioning Flow

### Step 1 — Parent Claims the Toy (App → Backend)

```
Parent opens app → app connects to toy over BLE → reads factory_device_id = "TOY-A1B2C3" from CHAR_DEVICE_INFO

POST /api/v1/toy/claim
Authorization: Bearer <firebase_jwt>
{factory_device_id: "TOY-A1B2C3"}

Backend:
  1. SELECT Toy WHERE factory_device_id = "TOY-A1B2C3" FOR UPDATE
  2. Assert status == PROVISIONED
  3. SELECT Child WHERE parent_id = parent.id AND is_deleted = false
  4. Assert child exists (onboarding must be done before claiming)
  5. raw_api_key = secrets.token_urlsafe(32)
  6. key_hash = sha256(raw_api_key)
  7. INSERT api_keys(key_hash, toy_id, revoked=false)
  8. UPDATE toys SET status=ACTIVE, owner_parent_id=parent.id, active_child_id=child.id
  9. COMMIT
  10. SET Redis: toy_key:{key_hash} = toy.id  EX 86400
  11. Return: {toy_uuid, toy_api_key: raw_api_key, status: "claimed"}

→ Parent app receives raw_api_key (only time this is returned)
```

### Step 2 — BLE Provisioning (Phone → Toy, No Internet)

```
Parent app connects to toy over Bluetooth (NimBLE GATT)

GATT Server on toy:
  Service UUID: custom
  Characteristics:
    CHAR_DEVICE_INFO  (Read)  → returns factory_device_id
    CHAR_WIFI_SSID    (Write) ← phone sends WiFi SSID
    CHAR_WIFI_PASS    (Write) ← phone sends WiFi password
    CHAR_API_KEY      (Write) ← phone sends raw_api_key from claim response
    CHAR_PROV_CMD     (Write) ← phone sends "COMMIT"
    CHAR_PROV_STATUS  (Read/Notify) → toy sends provisioning state

State machine:
  UNPROVISIONED → [writes complete] → CREDS_LOADED → [COMMIT] →
  COMMITTING → [NVS write] → WIFI_CONNECTING → [connected] →
  MQTT_CONNECTING → [connected] → VALIDATING → [auth OK] → READY

NVS write order (power-loss safe):
  1. Write api_key to NVS boboloo namespace
  2. Write ssid
  3. Write pass
  4. Write provisioned=1  ← atomic flag, last

On READY: toy notifies CHAR_PROV_STATUS = "READY"
Parent app shows "Toy connected" confirmation
BLE timeout: 10 minutes (toy goes back to advertising if not provisioned)
```

### Step 3 — MQTT Connection (Toy → EMQX)

```
Toy reads from NVS:
  factory_id = "TOY-A1B2C3"
  api_key = "rawKeyValue..."

MQTT CONNECT:
  Host: MQTT_HOST:8883 (TLS)
  Client ID: "TOY-A1B2C3"
  Username: "TOY-A1B2C3"
  Password: "rawKeyValue..."

EMQX calls POST /internal/mqtt/auth
  Backend validates key, returns allow + per-device ACL

Toy subscribes: boboloo/toy/TOY-A1B2C3/audio/out
Toy subscribes: boboloo/toy/TOY-A1B2C3/cmd
Toy publishes heartbeat: boboloo/toy/TOY-A1B2C3/status  {"battery_level": 90}

Connection is persistent. Toy reconnects automatically on disconnect.
```

---

## 15. Rate Limiting

Three-tier rate limiting enforced in `app/middleware/rate_limiter.py`.

### Limits

| Client Type | Limit | Window | Identifier |
|-------------|-------|--------|------------|
| Toy (runtime endpoints) | 20 requests | 60 seconds | toy UUID from Redis lookup |
| Authenticated user | 60 requests | 60 seconds | Firebase UID |
| IP fallback | 100 requests | 60 seconds | client IP |

### Exemptions

- `GET /` (root)
- `GET /health` (health check)

### Implementation

```
1. Determine identifier (toy → user → IP fallback)
2. INCR rate:{identifier}:{path}
3. If count == 1: EXPIRE rate:{identifier}:{path} WINDOW_SECONDS
4. If count > LIMIT: return 429 {"detail": "Too many requests. Please slow down."}
5. Else: add X-RateLimit-Remaining header and continue
```

**Note:** There is a known race condition between INCR and EXPIRE (if the process crashes between the two commands, the key has no TTL and permanently limits that client). Fix: use a Lua script to make INCR+EXPIRE atomic.

### Response Headers

```
X-RateLimit-Remaining: 17
```

### Error Response (429)

```json
{
  "detail": "Too many requests. Please slow down."
}
```

---

## 16. Interaction Settings

Settings are used by the AI service to tailor responses to the child's learning level.

### Settings Fields

| Field | Type | Range/Options | Description |
|-------|------|---------------|-------------|
| `smart_adapt_mode` | Boolean | true/false | AI auto-adjusts based on child's vocabulary level |
| `custom_tune` | Boolean | true/false | Parent manually controls all settings below |
| `word_complexity` | Integer | 1-5 | 1=very simple, 5=complex |
| `speech_speed` | Integer | 1-5 | 1=very slow, 5=fast |
| `new_words_per_session` | Integer | 1-10 | Target new words per conversation |
| `question_frequency` | String | "low"/"balanced"/"high" | How often AI asks questions back |
| `topic_focus` | Integer | 1-5 | 1=follows child's lead, 5=stays on assigned topics |
| `command_steps` | Integer | 1-5 | Multi-step instruction complexity |
| `patience_level` | Integer | 1-5 | How long AI waits before re-prompting |

### Default Values

All defaults are set when a child completes onboarding. Auto-created on first GET if missing.

```json
{
  "smart_adapt_mode": true,
  "custom_tune": false,
  "word_complexity": 3,
  "speech_speed": 2,
  "new_words_per_session": 3,
  "question_frequency": "balanced",
  "topic_focus": 3,
  "command_steps": 2,
  "patience_level": 3
}
```

### How Settings Affect the AI

The AI worker reads these settings and includes `word_complexity` and `speech_speed` in the OpenAI system prompt. `question_frequency` is stored but not yet fully wired into the prompt logic.

### Caching

Settings are cached in Redis at `settings:{child_id}` with a 5-minute TTL. The cache is populated on first DB read and served on subsequent requests within the TTL window.

---

## 17. Deployment

### Docker Compose (Full Stack)

```yaml
Services:
  backend         → Port 8080, Dockerfile, depends on postgres+redis
  mqtt_gateway    → Dockerfile, python -m app.mqtt_gateway, depends on redis
  worker          → Dockerfile, python -m app.workers.worker, depends on postgres+redis
  status_worker   → Dockerfile, python -m app.workers.status_worker, depends on postgres+redis
  redis           → redis:7-alpine, port 6379, appendonly yes
  postgres        → postgres:15-alpine, port 5432

Volumes:
  postgres_data   → /var/lib/postgresql/data
  redis_data      → /data
```

### Commands

```bash
# Build and start all services
docker-compose up --build

# Start in background
docker-compose up -d

# View logs
docker-compose logs -f backend
docker-compose logs -f worker
docker-compose logs -f mqtt_gateway
docker-compose logs -f status_worker

# Stop everything
docker-compose down

# Stop and remove volumes (DESTRUCTIVE — deletes all data)
docker-compose down -v
```

### Dockerfile

```dockerfile
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y gcc libpq-dev curl
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN adduser --disabled-password appuser
RUN chown -R appuser:appuser /app
USER appuser
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD curl --fail http://localhost:8080/health
CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "app.main:app",
     "--bind", "0.0.0.0:8080", "--workers", "1", "--timeout", "60"]
```

### Healthchecks

| Service | Check | Interval |
|---------|-------|----------|
| `backend` | `curl http://localhost:8080/health` | 30s |
| `redis` | `redis-cli ping` | 10s |
| `postgres` | `pg_isready` | 10s |
| `mqtt_gateway` | disabled | — |
| `worker` | disabled | — |
| `status_worker` | disabled | — |

### Database Migrations

Always run migrations before deploying a new backend version:

```bash
# In the running backend container
docker-compose exec backend alembic upgrade head

# Or run before starting backend (one-shot container)
docker-compose run --rm backend alembic upgrade head
```

### Local Development (No Docker)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Create .env file with all required variables
cp .env.example .env  # edit as needed

# 3. Apply DB migrations
alembic upgrade head

# 4. Start API server
uvicorn app.main:app --reload --port 8080

# 5. Start AI worker (separate terminal)
python -m app.workers.worker

# 6. Start MQTT gateway (separate terminal)
python -m app.mqtt_gateway

# 7. Start status worker (separate terminal)
python -m app.workers.status_worker
```

### Scaling Considerations

- **Backend API:** Can scale horizontally (stateless). However, APScheduler will run on every replica — analytics batch fires N times. Fix: move analytics batch to a dedicated cron container.
- **MQTT Gateway:** Must run as a **single instance**. Multiple gateways would cause duplicate message processing.
- **AI Worker:** Can scale horizontally. Redis queue distributes jobs across multiple workers.
- **Status Worker:** Can scale horizontally. Redis queue handles distribution.
- **Database:** Single PostgreSQL instance. pool_size=5 per process, max_overflow=10. Increase pool size before adding more replicas.
- **Redis:** Single instance. Can upgrade to Redis Cluster for high availability.

### Production Security Checklist

| Item | Status |
|------|--------|
| `CORS_ORIGINS` set to specific domains (not `"*"`) | Configure |
| `MQTT_AUTH_SECRET` set to a strong random value | Required |
| `FACTORY_SECRET_KEY` — use `hmac.compare_digest` in factory_routes.py | Fix needed |
| `ENVIRONMENT=production` (disables Swagger, dev endpoints) | Configure |
| HTTPS only for all API endpoints | Infrastructure |
| MQTT TLS enabled (`MQTT_USE_TLS=True`, port 8883) | Default |
| Firebase `check_revoked=True` (already enabled) | Done |
| Sentry DSN configured for error monitoring | Recommended |
| NVS encryption enabled on ESP32 firmware | Firmware team |

---

## 18. Developer Tools

### Simulation Scripts

| Script | Purpose |
|--------|---------|
| `dev/toy_simulator.py` | Simulate toy MQTT messages |
| `dev/multi_toy_runner.py` | Run multiple toy simulators simultaneously |
| `dev/provision_toy.py` | Factory provision a toy via API |
| `dev/queue_inspector.py` | Inspect Redis queue contents |
| `dev/test_backend.py` | End-to-end backend API tests |
| `dev/test_failure_scenarios.py` | Test error handling paths |
| `dev/test_ota_flow.py` | Test OTA push and status flow |
| `simulate_toys.py` | Root-level toy simulator |
| `test_mqtt.py` | Test MQTT broker connectivity |
| `load_test.py` | HTTP load testing |
| `generate_qr.py` | Orphaned dev utility — QR codes are not used in the current onboarding flow |
| `test_vq_pipeline.py` | Test vocabulary analytics pipeline |

### Useful Redis Commands

```bash
# Connect to Redis in Docker
docker-compose exec redis redis-cli

# View all queues
LLEN ai_interaction_queue
LLEN ai_interaction_queue:processing
LLEN ai_interaction_queue:failed
LLEN toy_status_queue
LLEN outbound_queue

# View a job in the queue
LINDEX ai_interaction_queue 0

# Check toy key cache
GET toy_key:<hash>

# Check toy presence
HGETALL toy:status:TOY-A1B2C3

# Check worker heartbeats
GET ai_worker:heartbeat
GET status_worker:heartbeat

# Clear a queue (CAUTION: loses jobs)
DEL ai_interaction_queue
```

### Logging

Structured logging via `app/core/app_logging.py`. Logger names:
- `main` — API server startup/shutdown
- `ai_worker` — AI worker job processing
- `status_worker` — status worker
- `mqtt_gateway` — MQTT gateway
- `mqtt_auth` — MQTT auth endpoint
- `machine_auth` — toy key validation
- `toy_key_validator` — key resolution
- `rate_limit` — rate limiting
- `request_logging` — HTTP request logs

### API Documentation (Development Only)

When `ENVIRONMENT=development`:
- Swagger UI: `http://localhost:8080/docs`
- ReDoc: `http://localhost:8080/redoc`
- OpenAPI JSON: `http://localhost:8080/openapi.json`

Note: Admin and OTA routes are hidden from Swagger (`include_in_schema=False`) regardless of environment.

---

*Boboloo Backend — Complete Technical Reference*  
*Generated 2026-05-25 from source code analysis*  
*All flows, schemas, and API contracts reflect the current codebase state*
