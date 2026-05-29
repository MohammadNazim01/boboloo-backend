# Boboloo — Deployment Readiness Report

**Date:** 2026-05-29  
**Branch:** main  
**Reviewer:** Code analysis against live codebase

---

## Priority Definitions

| Priority | Meaning |
|----------|---------|
| **P0** | Must fix before production — security vulnerability or data loss risk |
| **P1** | Should fix before public launch — reliability, compliance, or significant UX impact |
| **P2** | Post-launch improvement — reduces technical debt or operational friction |

---

## P0 — Must Fix Before Production

### P0-1 — Factory Auth: Timing Attack Vulnerability
**Status:** Needs Work  
**File:** `app/routes/factory_routes.py` lines 37 and 87  
**Issue:** The factory secret is compared with `!=`:
```python
if factory_secret != settings.FACTORY_SECRET_KEY:  # VULNERABLE
```
This allows a timing attack — an attacker can measure response times to guess the secret character by character.

**Fix:** Replace both occurrences with:
```python
if not hmac.compare_digest(factory_secret, settings.FACTORY_SECRET_KEY):
```
Add `import hmac` at the top of the file.

**Why it matters:** The factory endpoint can create arbitrary device records. If the secret is guessed, an attacker can flood the database with fake provisioned toys.

---

### P0-2 — MQTT_AUTH_SECRET Defaults to Empty String
**Status:** Needs Work  
**File:** `app/core/config.py` line 46  
**Issue:**
```python
MQTT_AUTH_SECRET: str = ""
```
In `app/routes/mqtt_auth_routes.py`, if `MQTT_AUTH_SECRET` is empty, the endpoint rejects ALL EMQX auth requests:
```python
if not expected:
    logger.error("MQTT_AUTH_SECRET is not set — rejecting all EMQX auth requests")
    return False
```
If the variable is not set in production `.env`, every toy will be unable to connect via MQTT.

**Fix:** Make `MQTT_AUTH_SECRET` required (remove default), or add a startup check that fails loudly if empty when `ENVIRONMENT=production`.

---

### P0-3 — CORS Allows All Origins in Production
**Status:** Needs Work  
**File:** `app/core/config.py` line 71  
**Issue:**
```python
CORS_ORIGINS: str = "*"
```
In production, `"*"` means any website can make cross-origin requests to the API as authenticated users. This enables cross-site request forgery attacks against the parent app.

**Fix:** Set `CORS_ORIGINS` to specific domains in the production `.env`:
```
CORS_ORIGINS=https://app.boboloo.com,https://admin.boboloo.com
```
This is a configuration change, not a code change. But it must be done before launch.

---

## P1 — Should Fix Before Public Launch

### P1-1 — Interaction Settings Routes Have No Authentication
**Status:** Needs Work  
**File:** `app/routes/interaction_settings_routes.py`  
**Issue:** `GET` and `PUT /api/v1/interaction-settings/{child_id}` accept any request without an `Authorization` header. Any caller who knows (or guesses) a child UUID can read or modify that child's AI settings.

Child UUIDs are not secret — they appear in analytics responses accessible to authenticated parents. A parent from one account could modify another child's settings.

**Fix:** Add `get_current_parent` dependency to both routes and validate that the parent owns the child:
```python
@router.get("/{child_id}")
async def get_settings(
    child_id: uuid.UUID,
    parent = Depends(get_current_parent),
    db: AsyncSession = Depends(get_db),
):
    # Add: assert child.parent_id == parent.id
```

---

### P1-2 — Analytics Batch Fires N Times When API Scales
**Status:** Needs Work  
**File:** `app/main.py` lines 104–110  
**Issue:** `APScheduler` is initialized inside the FastAPI app startup event. If the API runs as multiple replicas (e.g. `--workers 2` or multiple containers), the `run_analytics_batch()` job fires N times simultaneously — once per process.

This causes:
- Duplicate ChildAnalytics writes (safe, idempotent due to UPSERT)
- Duplicate ChildVocabularyMemory inserts that hit the UNIQUE constraint and raise IntegrityErrors
- N × DB load at 02:00 UTC

**Fix:** Move the analytics scheduler out of the API process into a standalone cron container:
```yaml
# docker-compose.yml addition
analytics_cron:
  build: .
  command: python -m app.workers.analytics_cron  # new entry point
  ...
```

This is not a blocking issue for single-instance deployment but must be fixed before horizontal scaling.

---

### P1-3 — No Worker Health Monitoring or Alerting
**Status:** Needs Work  
**Files:** `docker-compose.yml` (worker and status_worker services)  
**Issue:** The AI worker and status worker containers have `healthcheck: disable: true`. If either process crashes silently:
- Toys send questions that are never answered
- No alert is triggered
- The only indication is the absence of Redis heartbeat keys (`ai_worker:heartbeat`, `status_worker:heartbeat`)

**Fix:** Add external monitoring that alerts when these Redis keys are missing:
```bash
# Simple check (run every 60s from monitoring service):
redis-cli GET ai_worker:heartbeat     # empty = worker down
redis-cli GET status_worker:heartbeat # empty = worker down
```
Configure Sentry, Datadog, or a simple cron alert to notify on-call if either key is absent for >60 seconds.

---

### P1-4 — MQTT Gateway Has No Health Check
**Status:** Needs Work  
**File:** `docker-compose.yml`  
**Issue:** `mqtt_gateway` has `healthcheck: disable: true`. If the gateway goes down, all toy MQTT messages stop flowing. There is no automatic detection.

**Fix:** The gateway writes `ai_worker:heartbeat` is already in the workers. Add a similar heartbeat to the gateway:
```python
# In gateway.py, add a heartbeat coroutine:
async def heartbeat_loop():
    while True:
        await redis_client.set("mqtt_gateway:heartbeat", "1", ex=30)
        await asyncio.sleep(10)
```
Then monitor `mqtt_gateway:heartbeat` in Redis.

---

### P1-5 — No Data Retention or Account Deletion Flow
**Status:** Needs Work  
**Issue:** All child conversation data is stored indefinitely. There is no:
- Automated data expiry (e.g. delete conversations older than 1 year)
- Account deletion endpoint that removes all child data
- GDPR/COPPA "right to be forgotten" compliance

This is a legal compliance requirement if serving users in the EU (GDPR) or users under 13 in the US (COPPA).

**Fix required:**
1. `DELETE /api/v1/parent/account` endpoint that hard-deletes parent, children, conversations, and messages
2. Background job to purge old conversation data per configurable retention window
3. Privacy policy that discloses retention period

---

### P1-6 — APScheduler Uses Deprecated FastAPI Lifecycle Events
**Status:** Needs Work  
**File:** `app/main.py` lines 94, 114  
**Issue:**
```python
@app.on_event("startup")    # deprecated in FastAPI 0.93+
@app.on_event("shutdown")   # deprecated in FastAPI 0.93+
```
These still work but will be removed in a future FastAPI version.

**Fix:** Migrate to `lifespan` context manager:
```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    scheduler.start()
    yield
    # shutdown
    scheduler.shutdown(wait=False)

app = FastAPI(lifespan=lifespan, ...)
```

---

### P1-7 — Sentry Not Configured in Any Environment
**Status:** Needs Work  
**File:** `app/main.py` line 29  
**Issue:** `SENTRY_DSN` is optional and defaults to `None`. Sentry is never initialized in practice, meaning unhandled exceptions in the API, workers, and gateway are silently swallowed — no error tracking.

**Fix:** Create a Sentry project, set `SENTRY_DSN` in the production `.env`. Free tier covers initial volume.

---

### P1-8 — PostgreSQL Connection Pool May Exhaust Under Load
**Status:** Needs Work  
**File:** `app/database/database.py`  
**Issue:** `pool_size=5, max_overflow=10` (15 total connections per process). With 4 processes (API + AI worker + status worker + any future processes), total connections = 60. PostgreSQL default `max_connections=100`, leaving 40 for migrations and admin tooling.

If the AI worker scales to multiple instances, each adds 15 connections. At 7 instances: 105 connections → PostgreSQL starts refusing connections.

**Fix:** Plan connection budget before scaling. Consider PgBouncer as a connection pooler, or reduce `pool_size` per process.

---

## P2 — Post-Launch Improvements

### P2-1 — Rate Limiter INCR/EXPIRE Race Condition (Non-Atomic)
**Status:** Optional  
**File:** `app/middleware/rate_limiter.py` lines 84–87  
**Issue:** After `INCR key`, if the process crashes before `EXPIRE key`, the key has no TTL and permanently rate-limits that client identifier. This is a very rare edge case but can cause a "ghost ban" for a specific toy or user.

**Fix:** Atomic Lua script:
```python
LUA_RATE_LIMIT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return count
"""
new_value = await redis_client.eval(LUA_RATE_LIMIT, 1, key, str(WINDOW))
```

---

### P2-2 — Analytics Guard Returns Blank Analytics with updated_at=None
**Status:** Optional  
**File:** `app/auth/analytics_guard.py` lines 38–42  
**Issue:** If a child has never had analytics computed (new account), the guard returns a `ChildAnalytics` object with `breakdown_json={}` and `updated_at=None`. Downstream route handlers must handle `None` gracefully or they'll crash.

**Fix:** Either return 404 with a clear message ("Analytics not yet available — check back after your child's first conversation"), or ensure analytics are initialized on child creation.

---

### P2-3 — `question_frequency` Setting Not Fully Wired Into AI Prompt
**Status:** Optional  
**File:** `app/services/ai/ai_service.py`  
**Issue:** `InteractionSettings.question_frequency` is stored and cached but is not yet included in the OpenAI system prompt. Only `word_complexity` and `speech_speed` affect the AI's behaviour.

**Fix:** Add `question_frequency` handling to the system prompt template:
```python
if settings.get("question_frequency") == "high":
    prompt += "\nAlways end your response with a question."
elif settings.get("question_frequency") == "low":
    prompt += "\nDo not ask questions unless the child asks one first."
```

---

### P2-4 — No Toy Online/Offline Notification to Parent App
**Status:** Optional  
**Issue:** The backend tracks toy presence in Redis (`toy:status:{device_id}` with 120s TTL), but there is no push notification to the parent app when a toy goes offline or comes back online. Parents have no way to know if the toy has lost Wi-Fi.

**Fix:** Add a Firebase Cloud Messaging (FCM) push notification when toy presence status changes from online to offline for >5 minutes.

---

### P2-5 — Multi-Toy per Parent Not Supported
**Status:** Optional  
**Issue:** The `children` table has `parent_id` as UNIQUE (one child per parent). The `toys` table allows `owner_parent_id` → parent (many toys per parent), but there is no UI or API flow for managing multiple toys under one account. Families with multiple children or multiple toys cannot currently use the system.

**Fix:** Remove the `UNIQUE` constraint on `children.parent_id`, update the child CRUD API to support listing/managing multiple children, and update the claim flow to support assigning different toys to different children.

---

### P2-6 — No Deduplication on MQTT Messages
**Status:** Optional  
**Issue:** MQTT QoS 1 guarantees at-least-once delivery. If the broker re-delivers a message (e.g. during a brief connection hiccup), the AI worker will process it twice and generate two AI responses for one question. The child hears the toy answer the same question twice.

**Fix:** Add a deduplication check in the MQTT gateway using a Redis key per message (hash of device_id + question + timestamp, TTL 30s). Reject duplicates before pushing to the queue.

---

### P2-7 — No Conversation History Cleanup
**Status:** Optional  
**Issue:** The AI worker loads the last 10 messages from the current day's conversation on every request. As conversations grow longer (10+ exchanges), this SELECT becomes slightly more expensive. There is no archive or pagination strategy for very active users.

**Fix:** For now, the 10-message window cap in `handlers.py` (line 265–267) keeps OpenAI context manageable. Long-term: archive conversations older than 90 days to cold storage.

---

### P2-8 — Batch OTA Does Not Respect Device Online Status
**Status:** Optional  
**File:** `app/services/ota_service.py:push_ota_batch()`  
**Issue:** The batch OTA push sends the command to all matching toys regardless of whether they are currently online. Commands sent to offline toys sit in the MQTT broker's persistent session queue and are delivered when the toy reconnects — but the pre-signed S3 download URL (30-minute TTL) will have expired by then.

**Fix:** Check Redis `toy:status:{device_id}` before pushing. Mark offline toys for re-delivery. Alternatively, generate the S3 URL on demand when the toy actually receives the command (requires a different architecture).

---

## Summary Dashboard

| ID | Priority | Status | Description |
|----|----------|--------|-------------|
| P0-1 | P0 | Needs Work | Factory auth timing attack (1-line fix) |
| P0-2 | P0 | Needs Work | MQTT_AUTH_SECRET defaults to empty → all toys blocked |
| P0-3 | P0 | Needs Work | CORS allows all origins in production |
| P1-1 | P1 | Needs Work | Interaction settings API has no authentication |
| P1-2 | P1 | Needs Work | Analytics batch fires N times when API scales |
| P1-3 | P1 | Needs Work | No worker health monitoring or alerting |
| P1-4 | P1 | Needs Work | MQTT gateway has no health check |
| P1-5 | P1 | Needs Work | No data retention or account deletion (COPPA/GDPR) |
| P1-6 | P1 | Needs Work | Deprecated FastAPI lifecycle events |
| P1-7 | P1 | Needs Work | Sentry DSN not configured |
| P1-8 | P1 | Needs Work | DB connection pool not sized for scale |
| P2-1 | P2 | Optional | Rate limiter INCR/EXPIRE race condition |
| P2-2 | P2 | Optional | Analytics guard returns blank with updated_at=None |
| P2-3 | P2 | Optional | question_frequency not wired into AI prompt |
| P2-4 | P2 | Optional | No toy online/offline push notification |
| P2-5 | P2 | Optional | Multi-child / multi-toy per parent not supported |
| P2-6 | P2 | Optional | No MQTT message deduplication |
| P2-7 | P2 | Optional | No conversation history cleanup/archiving |
| P2-8 | P2 | Optional | Batch OTA ignores device online status |

---

## What Is Production Ready Today

These areas are fully implemented, tested by code review, and require no changes before launch:

| Area | Evidence |
|------|---------|
| Core conversation flow (MQTT path) | End-to-end: gateway → queue → worker → OpenAI → reply → toy |
| Core conversation flow (HTTP path) | Toy runtime routes with Redis cache + DB fallback |
| Firebase parent auth | Auto-registration, check_revoked=True, IntegrityError retry |
| Toy key auth | SHA-256 hash, Redis cache, DB fallback, self-heal |
| Admin auth | Double layer: hmac.compare_digest + Firebase admin claim |
| Toy claim flow | SELECT FOR UPDATE row lock, one-time key reveal |
| Key rotation | Immediate Redis revocation + DB revoked flag |
| OTA pipeline | Register → stable → push → S3 presign → MQTT → toy → status report |
| Analytics pipeline | spaCy NLP, vocabulary memory, daily streak, history snapshots |
| Factory provisioning | Single + batch, idempotent |
| MQTT auth + per-device ACL | hmac.compare_digest on shared secret, inline ACL enforcement |
| Rate limiting | Three-tier (toy / user / IP) with Redis counters |
| Docker Compose stack | All 6 services with healthchecks on infrastructure |
| Database schema | 12 tables, proper indexes, UUID PKs, JSONB for flexible data |
| Redis queue reliability | LMOVE atomic dequeue, processing queue, DLQ, crash recovery |
