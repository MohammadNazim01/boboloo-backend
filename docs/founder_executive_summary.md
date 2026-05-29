# Boboloo — Founder & Executive Summary

**Audience:** Founders, investors, product leaders, non-technical stakeholders  
**Purpose:** Understand how the product works, what it costs to run, what the risks are, and whether it is ready for production  
**Version:** 1.0 | **Date:** 2026-05-29

---

## What Boboloo Is, End to End

Boboloo is an AI-powered children's toy backed by a cloud system. A child talks to a physical stuffed toy. The toy listens, sends the child's words to the cloud, and an AI answers back in age-appropriate language. The parent monitors vocabulary growth and conversation history through a phone app.

Here is the complete product lifecycle in order:

---

## The Complete Product Journey

### Stage 1 — Manufacturing

A factory assembles the ESP32-based toy hardware and flashes our firmware onto each device. Each toy gets a unique Device ID (e.g. `TOY-A1B2C3`) permanently written into its memory chip.

Before the toy ships, the factory calls our cloud API once per toy to register it. This creates a database record for each physical unit. Status at this point: **PROVISIONED** — registered but not yet owned by anyone.

The Device ID is stored permanently in the toy's memory chip and is readable by the parent app over Bluetooth — no printed labels or QR codes are required.

---

### Stage 2 — Customer Unboxing

The parent downloads the Boboloo app and creates an account (using Google or email via Firebase). The backend automatically creates their account on first login — no manual setup required.

The parent creates a child profile: name, age, interests. This profile drives how the AI talks to their child.

---

### Stage 3 — Claiming the Toy

The parent opens the Boboloo app, turns on the toy, and the app connects to the toy over Bluetooth. The app reads the Device ID directly from the toy — no QR code or manual entry needed.

Behind the scenes:
- The app sends the Device ID (read from the toy over Bluetooth) to our cloud
- Our cloud verifies the toy exists and hasn't been claimed before
- The cloud generates a secret API key tied to this toy
- The key is sent back to the parent's app
- The toy's status changes from **PROVISIONED** to **ACTIVE**

The raw API key is shown to the app exactly once and never stored in our cloud. This is by design — even if our database were compromised, no one could extract the toy's credentials.

---

### Stage 4 — BLE Setup

The parent's phone connects to the toy via Bluetooth (short-range, no internet needed).

The phone securely sends three things to the toy:
1. Wi-Fi network name
2. Wi-Fi password
3. The secret API key generated in Stage 3

The toy saves these to its internal memory and reboots. This is the only time Bluetooth is used — everything else happens over Wi-Fi.

---

### Stage 5 — The Toy Comes Online

The toy connects to the home Wi-Fi and then to our cloud message broker (EMQX) over an encrypted TLS connection. Authentication happens automatically — the toy identifies itself with its API key.

The cloud broker verifies the key, then restricts the toy to its own private communication channel. The toy cannot see or interfere with any other toy's messages.

At this point: the toy is live. It will remain connected to the cloud as long as it has power and Wi-Fi.

---

### Stage 6 — The Child Has a Conversation

```
Child speaks
    ↓
Toy's microphone records audio
    ↓
On-device chip converts speech to text
    ↓
Text published to cloud over MQTT: "Why is the sky blue?"
    ↓
Cloud receives the question
    ↓
AI generates a child-appropriate answer using:
  • Child's age (from parent profile)
  • Child's interests (from parent profile)
  • Word complexity settings
  • Conversation history from today
    ↓
AI answer sent back to toy over MQTT: "The sky is blue because sunlight bounces off tiny air molecules!"
    ↓
Toy's speaker plays the answer as audio
    ↓
Answer saved to database → available for parent analytics
```

Total time: typically 2–4 seconds. The bottleneck is the OpenAI API call (~1–2 seconds).

---

### Stage 7 — Parent Analytics

Every night at 2 AM, the cloud analyses that day's conversations for every child.

The analytics engine:
- Counts total words the child spoke
- Identifies new words the child used for the first time
- Tracks vocabulary growth over time
- Calculates a "conversation streak" (like a Duolingo streak for kids)

Parents see this in the app as:
- Weekly vocabulary growth charts
- New words learned this week
- Current streak ("Emma has talked to Boboloo for 7 days in a row!")
- Personalized insight: "Emma's word diversity is low — try asking open-ended questions"

---

### Stage 8 — Firmware Updates

When we release a new firmware version for the toy:
1. We upload the signed firmware file to Amazon S3
2. An admin registers the new version in our backend
3. QA approves it
4. Admin clicks "push" — either for a single toy or an entire batch of toys on an old version

The update travels to the toy over Wi-Fi. The toy downloads it, verifies it (SHA-256 checksum), installs it to a spare memory slot, and reboots. If the update fails or causes problems, the toy automatically reverts to the previous version. No manual intervention required.

---

## How the Cloud System Works (Non-Technical Summary)

The backend is split into four independent services:

| Service | What It Does |
|---------|-------------|
| **API Server** | Handles the parent app, admin panel, factory registration, and toy runtime requests |
| **MQTT Gateway** | Manages the live connections to all toys simultaneously |
| **AI Worker** | Processes child questions: calls OpenAI, saves the reply, sends it back to the toy |
| **Status Worker** | Tracks each toy's battery level, Wi-Fi signal, and firmware version in real-time |

These services communicate through **Redis** (a fast in-memory database used as a message queue). The API Server never talks directly to the AI Worker — it just drops a job in the queue, and the worker picks it up. This means the parent app always gets a fast response even if the AI is slow.

---

## Security Model

| What We Protect | How |
|----------------|-----|
| Parent accounts | Firebase (Google-managed) JWT tokens, revocable |
| Toy identity | SHA-256 hashed API keys — raw key never stored in our database |
| Toy-to-cloud communication | TLS encrypted, per-device MQTT topic isolation |
| Admin panel | Dual authentication: secret key + Firebase admin role |
| Factory API | Shared secret key (improvement needed — see risks below) |
| Child data | Isolated by parent account, soft-delete only (no immediate erasure) |

**Each toy can only read and write to its own messages.** Even if an attacker obtained one toy's credentials, they could not access any other toy's data.

---

## Scalability Model

| Component | Current State | Scales How |
|-----------|--------------|------------|
| API Server | Single instance | Horizontal (add more copies) |
| AI Worker | Single instance | Horizontal (multiple workers share the queue) |
| Status Worker | Single instance | Horizontal |
| MQTT Gateway | Single instance | Must remain single instance (architectural constraint) |
| PostgreSQL | Single instance | Read replicas for analytics queries; sharding for large scale |
| Redis | Single instance | Redis Cluster for high availability |

**For the first 10,000 toys:** The current architecture is adequate.  
**For 100,000+ toys:** The MQTT Gateway becomes the bottleneck. This requires redesigning to use multiple broker subscribers with message deduplication.

---

## Operational Costs (Approximate)

These are rough estimates per month for a production deployment.

| Component | Service | Estimated Monthly Cost |
|-----------|---------|----------------------|
| Cloud hosting (all 4 services) | AWS ECS / Fly.io / DigitalOcean | $50–200 |
| PostgreSQL (managed) | AWS RDS / Supabase | $25–100 |
| Redis (managed) | AWS ElastiCache / Upstash | $15–50 |
| MQTT Broker | EMQX Cloud | $0–200 (depends on connections) |
| OpenAI API | Pay-per-use (gpt-4o-mini) | ~$0.15 per 1,000 interactions |
| AWS S3 | Firmware storage | <$5 |
| Firebase | Auth | Free tier (up to 50,000 MAU) |
| Sentry | Error monitoring | $0 (free tier) / $26+ (team) |

**AI cost is the dominant variable:** At 10 interactions per child per day, 1,000 active children = 10,000 daily calls = ~$1.50/day in AI costs. At 100,000 children: ~$150/day.

---

## Risks and Mitigations

### Risk 1 — Security: Factory API Timing Attack
**What:** The factory API compares secret keys with `!=` instead of the secure `hmac.compare_digest()`. A sophisticated attacker could use timing differences to guess the key.  
**Impact:** Medium — if exploited, attacker could register fake toys  
**Mitigation:** One-line code fix. Must be done before production launch.  
**Status:** Known, not yet fixed

### Risk 2 — Security: CORS Allows All Origins
**What:** By default, the API accepts requests from any website (`"*"`).  
**Impact:** In production, malicious websites could make requests as authenticated users  
**Mitigation:** Set `CORS_ORIGINS` to your specific app domains before launch  
**Status:** Config change only, easy to fix

### Risk 3 — Security: Interaction Settings API Has No Authentication
**What:** Any child ID can be used to read or change settings without authentication  
**Impact:** Anyone who knows a child ID (UUID format) could change AI settings  
**Mitigation:** Add Firebase JWT authentication to these routes  
**Status:** Known, needs development work

### Risk 4 — Reliability: MQTT Gateway Single Point of Failure
**What:** If the MQTT gateway crashes, no toys can send or receive messages until it restarts  
**Impact:** All toys go silent until container restart (~5–15 seconds with Docker restart policy)  
**Mitigation:** Docker `restart: always` provides auto-recovery. For zero-downtime requirement, needs architectural work.  
**Status:** Acceptable for launch, watch for production incidents

### Risk 5 — Reliability: Analytics Runs on API Server
**What:** The nightly analytics batch runs inside the API server process. If multiple API servers are running, analytics runs multiple times simultaneously.  
**Impact:** Data duplication, database conflicts  
**Mitigation:** Move analytics to a dedicated cron container before scaling API horizontally  
**Status:** No impact while API runs as single instance

### Risk 6 — Reliability: No Worker Health Monitoring
**What:** The AI and Status workers have no external health check. If they crash silently, toys receive no responses and the failure is not immediately visible.  
**Impact:** Users experience "toy not responding" with no alert to the team  
**Mitigation:** Add monitoring that checks Redis heartbeat keys (`ai_worker:heartbeat`, `status_worker:heartbeat`) and pages on-call if missing  
**Status:** Needs monitoring setup before production

### Risk 7 — Privacy: Child Data Retention
**What:** No automated data retention/deletion policy is implemented. All conversations are stored indefinitely.  
**Impact:** COPPA/GDPR compliance risk. Children's data requires stricter handling.  
**Mitigation:** Implement data retention policies (e.g. auto-delete conversations older than 1 year) and proper account deletion flow  
**Status:** Required for regulatory compliance

---

## Production Readiness Assessment

| Area | Status | Summary |
|------|--------|---------|
| Core functionality | Ready | End-to-end flow works: provision → claim → question → AI → answer |
| Security (critical paths) | Mostly ready | Two small code fixes needed |
| Security (non-critical) | Needs work | Interaction settings auth, CORS config |
| Scalability | Ready for launch | Handles initial volume; scaling plan needed for growth |
| Observability | Needs work | No worker monitoring, no alerting configured |
| Privacy/compliance | Not ready | No data retention policy, no proper account deletion |
| OTA updates | Ready | Full end-to-end OTA pipeline implemented |
| Analytics | Ready | Nightly batch working, vocabulary tracking live |
| Admin panel | Ready | Dashboard, toy management, OTA push all implemented |
| Error monitoring | Ready | Sentry integration built-in, just needs DSN configured |

**Recommendation:** The product is functionally complete and could serve early customers today. Three things should be fixed before broad public launch:
1. Two security fixes (factory auth comparison, interaction settings auth)
2. Worker health monitoring and alerting
3. Basic data retention policy for child conversation data
