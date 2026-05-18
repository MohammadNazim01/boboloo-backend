# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Boboloo** is a FastAPI backend for an AI-powered children's toy. Physical toys communicate via MQTT, receive audio input, and get AI-generated child-appropriate responses published back via MQTT. The system runs two processes: a web API server and a background worker.

## Commands

### Development (local)
```bash
# Install dependencies
pip install -r requirements.txt

# Run API server (dev mode)
uvicorn app.main:app --reload --port 8080

# Run background worker (separate terminal)
python -m app.workers.worker
```

### Docker (full stack)
```bash
docker-compose up --build        # Start all services
docker-compose up -d             # Detached mode
docker-compose logs -f backend   # Tail backend logs
```

### Database migrations
```bash
alembic upgrade head                          # Apply all migrations
alembic revision --autogenerate -m "desc"     # Generate new migration
alembic downgrade -1                          # Roll back one step
```

### Utilities
```bash
python simulate_toys.py   # Simulate toy MQTT messages
python test_mqtt.py       # Test MQTT connectivity
python load_test.py       # Load test the API
python generate_qr.py     # Generate QR codes for toy claiming
```

## Required Environment Variables (`.env`)

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Async PostgreSQL URL (`postgresql+asyncpg://...`) |
| `REDIS_URL` | Redis connection URL |
| `OPENAI_API_KEY` | OpenAI API key for AI responses |
| `FIREBASE_CREDENTIALS_PATH` | Path to Firebase service account JSON |
| `FACTORY_SECRET_KEY` | Secret for factory provisioning endpoints |
| `INTERNAL_CRON_SECRET` | Secret for internal cron job endpoints |
| `ADMIN_INTERNAL_SECRET` | Secret for admin-internal endpoints |
| `ENVIRONMENT` | `development` or `production` |

## Architecture

### Request Flow
1. **MQTT** — toy sends audio text to `boboloo/toy/{toy_id}/audio/in`
2. `app/main.py` MQTT handler receives it, looks up the `Toy` by `factory_device_id`
3. `ToyRuntimeService.handle_question()` validates, saves the user `Message`, runs vocabulary analysis, then pushes a job to the Redis queue (`job_queue` list key)
4. **Worker** (`app/workers/worker.py`) polls Redis, dispatches to `handle_interaction()` in `app/workers/handlers.py`
5. Handler calls `AIService.generate_child_reply()` (OpenAI `gpt-4o-mini`), saves the assistant `Message`, publishes the reply to `boboloo/toy/{toy_id}/audio/out`

### Two-Process Architecture
- **API server** (`app.main:app`): handles all HTTP + MQTT ingestion; immediately queues work and returns
- **Worker** (`app.workers.worker`): long-polls Redis queue, does the heavy lifting (AI call, MQTT reply)
- Communication between them is a Redis list (`job_queue`) used as a simple FIFO queue via `JobQueue.push/pop`

### Authentication Layers
- `firebase_auth.py` — Firebase JWT for parent (app) users; in `ENVIRONMENT=development`, uses a hardcoded fake parent (`test_parent_001`)
- `machine_auth.py` — toys authenticate with `X-Toy-Key` header (SHA-256 hashed, stored as `APIKey`); Redis-cached for performance with DB fallback + self-heal
- `admin_auth.py` / `admin_internal.py` — secret-key-based for admin routes
- `factory_routes.py` — protected by `FACTORY_SECRET_KEY` for device provisioning

### Caching Strategy (Redis)
- `toy_key:{hash}` → toy UUID (24h TTL, self-healed on DB fallback)
- `child:{id}` → child payload (5 min TTL)
- `settings:{child_id}` → interaction settings (5 min TTL)
- `toy:{id}` → online status hash (120s TTL, heartbeat-refreshed)
- Rate limiting keys: `rate:{identifier}:{path}` with sliding window counters

### Rate Limiting
Three tiers in `app/middleware/rate_limiter.py`:
- Toy endpoints (`/api/v1/toy/runtime`): 20 req/60s per toy key
- Authenticated users: 60 req/60s per Firebase UID
- IP fallback: 100 req/60s

### Analytics Engine
`app/services/analytics_engine/vocabulary_service.py` uses **spaCy** (`en_core_web_sm`) to lemmatize child speech and track vocabulary growth. Called synchronously in the MQTT handler before queuing, then results are persisted as `ChildVocabularyMemory` records. The `ChildAnalytics` table stores aggregate scores: `fq`, `vq`, `cq`, `mq`, `gq` (frequency/vocabulary/curiosity/memory/growth quotients).

### Data Model Key Relationships
- `Parent` → (1:1) → `Child` → (1:1) → `InteractionSettings`
- `Child` → (many) → `Conversation` (one per day, enforced by unique constraint) → (many) → `Message`
- `Toy` → (many:1) → `Parent`; `Toy.active_child_id` → `Child` (the child currently using the toy)
- `Toy` → (many) → `APIKey` (SHA-256 hashed keys for machine auth)
- `Child` → (many) → `ChildVocabularyMemory` (one row per unique word ever spoken)

### Route Groups
- `/api/v1/parent/...` — parent app: profile, child CRUD, onboarding
- `/api/v1/toy/claim/...` — claiming a toy (QR-code flow)
- `/api/v1/toy/runtime/...` — toy machine endpoints (heartbeat, question) — auth via `X-Toy-Key`
- `/api/v1/factory/...` — provisioning new toys — auth via `FACTORY_SECRET_KEY`
- `/admin/...` — admin panel endpoints (currently separate router)
- Analytics and internal routes exist but are commented out in `app/routes/__init__.py`

### MQTT Topics
- Inbound: `boboloo/toy/{factory_device_id}/audio/in` — toy → backend
- Outbound: `boboloo/toy/{factory_device_id}/audio/out` — backend → toy
- Broker: `broker.hivemq.com:1883` (public HiveMQ, configured in `app/main.py`)
