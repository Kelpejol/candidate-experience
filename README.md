# Candidate Experience Automation

Backend for Dragnet Solutions' candidate-experience automation. It is **two
independent systems** that deliberately share no storage, knowledge base, or
conversation threads:

| System | Channel | System of record | What it does |
|---|---|---|---|
| **Calling Agent** | Voice (ElevenLabs) | our SQLite DB | Inbound support calls + outbound survey calls to non-responders |
| **Helpdesk** | WhatsApp / email (Zoho Desk) | Zoho Desk | AI classifies tickets, drafts replies, tags/routes for officers |

Two supporting flows sit alongside these: **Campaigns** (send a SurveyMonkey
survey to a candidate batch, then orchestrate outbound calls to non-responders)
and **CSAT** (a post-support-call satisfaction survey).

> New here? Read this file, then `docs/blocked-items-and-helpdesk-plan.md` for
> the current status, what's blocked on external setup, and the domain model.

---

## 1. Stack

- **Python 3.14**, **FastAPI** (`main.py`), **SQLModel** on **SQLite**
  (`candidate_experience.db`, tables auto-created on startup).
- **Redis + RQ** for background jobs; long-running/external work never happens
  inline on a request.
- **pydantic-settings** for config (`app/core/config.py`, loaded from `.env`).
- **Frontend**: a standalone Vite + React + TypeScript reference app in
  `frontend/` (see `frontend/README.md`). Not the production UI — a portable
  reference that consumes every endpoint.

**All LLM calls go through an internal inference gateway** (`INFERENCE_BASE_URL`)
— `POST /chat` for completions, `POST /embed` for embeddings. **Never import a
vendor SDK (OpenAI/Anthropic/etc.) for inference.** See `helpdesk_classifier.py`
and `helpdesk_kb_service.py` for the call pattern.

---

## 2. Getting started

```bash
# 1. Python env (repo root)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Config — create .env (see §5 for keys). Ask a maintainer for real secrets.

# 3. Redis (required for jobs)
redis-server            # or: brew services start redis

# 4. Run it (each in its own terminal)
PYTHONPATH=. .venv/bin/uvicorn main:app --reload --port 8000   # API
PYTHONPATH=. .venv/bin/python scripts/run_worker.py             # RQ worker

# 5. Tests
.venv/bin/python -m pytest -q
```

API docs (Swagger) at http://localhost:8000/docs. Health at `/health`.

**Frontend** (optional, separate terminal):

```bash
cd frontend && npm install && npm run dev    # http://localhost:5173
```

**Demo data**: `PYTHONPATH=. .venv/bin/python scripts/seed_demo_data.py`
wipes + reseeds the Calling Agent tables with lifelike data (leaves the real
Helpdesk data alone).

---

## 3. How the pieces run

A request-time API plus **background workers and schedulers**. In production
you run several processes:

```
uvicorn main:app ............... the HTTP API (webhooks, CRUD, enqueue endpoints)
run_worker.py .................. RQ worker (forking) — executes queued jobs (LLM/Zoho/SM/calls)
run_helpdesk_scheduler.py ...... enqueues the helpdesk pipeline on a cron
run_campaign_scheduler.py ...... enqueues campaign orchestration on a cron
```

Run more than one `run_worker.py` side by side to raise job throughput — each
pulls independently from the same queue. `run_simple_worker.py` (in-process,
no forking) still exists for local debugging or environments where forking
isn't available, but isn't the production choice — a crashing job takes the
whole process down with it.

Schedulers only *enqueue*; the worker does the work. A scheduler crash costs at
most a missed cycle. Cron cadences and behaviour flags live in config (§5).

**Job flow example (Helpdesk):** `run_helpdesk_scheduler` → enqueues
`run_helpdesk_pipeline_job` → worker syncs the Zoho ticket mirror, then
classifies/decides/drafts each new ticket. Zoho writes stay gated behind flags.

---

## 4. Repository layout

```
main.py                     FastAPI app: builds app, registers routers, creates tables
app/
  api/routes/               HTTP endpoints (one file per area)
  services/                 business logic — the meat; framework-free, unit-tested
  models/                   SQLModel tables
  schemas/                  pydantic request/response shapes
  jobs/                     RQ job entry points (thin; open their own DB session)
  integrations/             external API clients (elevenlabs, surveymonkey, zoho, sharepoint)
  core/                     config, database, redis, queue, vocabulary/taxonomy
scripts/                    operational scripts (see §6)
tests/                      pytest suite (mirrors services; ~427 tests)
evals/                       KB quality eval support (golden set, DeepEval
                             gateway model) — see scripts/eval_voice_kb.py
kb/                         Helpdesk knowledge base (markdown; source for the vector index)
kb_voice/                   Calling Agent (voice) knowledge base — general scope
chroma_data/                ChromaDB vector store (helpdesk + voice_kb collections)
docs/                       plans, API reference, blocked-items tracker
frontend/                   standalone reference UI (its own README)
```

**Where logic lives:** routes and jobs stay thin; real logic is in `services/`
so it's testable without HTTP or a network. Follow that when adding features.

Key services by domain:
- **Calling Agent**: `call_record_service`, `outbound_call_execution_service`,
  `outbound_retry_service`, `elevenlabs_webhook_mapper`, `twiml_service`.
- **Campaigns**: `campaign_service`, `campaign_orchestration_service`,
  `surveymonkey_*` (template / distribution / sync).
- **Calling Agent brain (voice RAG + campaign IVR)**: `voice_kb_store` (Chroma
  seam) → `voice_kb_ingest` (chunk/embed/index, campaign-tagged with a reserved
  `general` scope) → `voice_kb_retrieval` (grounding) → `campaign_scope_service`
  (resolve a spoken campaign name to an active KB scope). **Three KB tiers**:
  general (universal) + tool (FOT/Test Haven/Scholastica — resolved silently
  from the campaign's `tool_name`, never asked of the caller, via
  `campaign_scope_service.campaign_tool_scope`) + campaign (client-specific).
  A `/voice-agent/kb/query` call passes the tool scope as `extra_scopes` to
  `retrieve_for_answer`/`voice_kb_store.query`, so one query matches all three
  tiers at once. Tool-scoped content is indexed the same way as everything
  else (`reindex_campaign`, keyed by the tool's own scope tag instead of a
  campaign id) via `scripts/reindex_voice_kb_tool.py` (local dir) or
  `scripts/reindex_voice_kb_tool_from_sharepoint.py` (SharePoint folder) —
  there's no Campaign row for a tool, so these are scripts, not routes.
  Routes under `/voice-agent/*` (`kb/query` with a campaign, `campaign/resolve`,
  `collect`) and `campaign_admin` (`/campaigns/{id}/inbound`,
  `/campaigns/{id}/kb/reindex` for a local directory,
  `/campaigns/{id}/kb/reindex-from-sharepoint` for real content —
  `sharepoint_kb_loader` lists/downloads a SharePoint folder's `.docx` files
  via `sharepoint_client` and converts each through
  `docx_kb_loader.docx_to_markdown`, which enforces the KB authoring standard
  — Heading 1/2 in Word, one question per heading, no bare section titles —
  before handing off to the same `reindex_campaign`; a bad file or a
  malformed heading is skipped with a warning, never fails the whole batch).
  The **outbound survey agent** has its own tools under `/voice-agent/outbound/*`
  (`outbound_survey_service`): `questions` serves a campaign's SurveyMonkey
  survey as spoken questions, `answer` records each response, `opt-out` honors
  "don't call me" — the last two are terminal, and the post-call webhook
  preserves them rather than overwriting.
  The agent's LLM reaches our Azure model via the `llm_proxy` route
  (`/llm/v1/chat/completions`), which forwards to the inference gateway
  server-side — a workaround for Cloudflare blocking ElevenLabs' direct calls.
- **Helpdesk**: `helpdesk_classifier` → `helpdesk_decision` →
  `helpdesk_kb_service` (grounding) → `helpdesk_draft_service` →
  `helpdesk_executor_service` (Zoho writes), orchestrated by
  `helpdesk_ai_service`; plus `helpdesk_ticket_mirror_service`,
  `helpdesk_reporting_service`. **Channel-aware**: Email drafts a reply for
  an officer to review and send (`action_type: draft_reply`); WhatsApp has no
  draft step — a grounded answer is generated in a shorter, conversational
  tone (`helpdesk_draft_service.generate_whatsapp_reply`) and, if
  `HELPDESK_WHATSAPP_AUTO_REPLY_EXECUTE` is on, sent immediately
  (`action_type: auto_reply`); a candidate explicitly asking for a person
  (`helpdesk_decision.detect_human_request`) always escalates first, before
  any generation is attempted. `ZohoDeskClient.send_whatsapp_reply`'s
  delivery mechanism is unverified against a real WhatsApp ticket — confirm
  before enabling. KB reindex is script-only (no HTTP route):
  `scripts/sync_helpdesk_kb.py` from the local `kb/*.md` folder, or
  `scripts/sync_helpdesk_kb_from_sharepoint.py "<folder>"` for real SharePoint
  content — both KBs' SharePoint content shares one authoring rule (Heading
  1/2 in Word, either is a question) via the shared
  `app/services/markdown_kb_chunker.py`, used by both `voice_kb_ingest` and
  `helpdesk_kb_service.chunks_from_markdown`.
- **CSAT**: `csat_service`.

---

## 5. Configuration (`.env`)

Full schema + defaults are in `app/core/config.py`. Groups:

- **App/DB**: `ENV`, `DATABASE_URL`, `JOB_TIMEZONE`, `PUBLIC_BASE_URL`,
  `CORS_ALLOW_ORIGINS`.
- **Inference gateway**: `INFERENCE_BASE_URL`, `INFERENCE_API_KEY`.
- **Redis/RQ**: `REDIS_URL`, `RQ_DEFAULT_QUEUE`.
- **ElevenLabs (voice)**: `ELEVENLABS_API_KEY`, `ELEVENLABS_WEBHOOK_SECRET`,
  outbound agent/phone/concurrency ids.
- **SurveyMonkey**: `SURVEYMONKEY_ACCESS_TOKEN`, campaign + CSAT survey/collector
  ids, template ids.
- **Zoho Desk**: `ZOHO_CLIENT_ID/SECRET/REFRESH_TOKEN`, `ZOHO_ORG_ID`,
  `ZOHO_DEPARTMENT_ID`, `ZOHO_WEBHOOK_TOKEN`.
- **SharePoint KB reader**: `KB_READER_TENANT_ID/CLIENT_ID/SECRET_VALUE`.
- **KB**: `KB_DIR`, `CHROMA_DIR`, `KB_GROUNDING_THRESHOLD`.
- **Voice agent**: `VOICE_KB_TOOL_TOKEN` (the bearer the ElevenLabs tools send
  to `/voice-agent/*`), and the LLM proxy — `LLM_GATEWAY_CHAT_URL`,
  `LLM_GATEWAY_KEY`, `LLM_PROXY_TOKEN` — the OpenAI-compatible endpoint that
  fronts the inference gateway for ElevenLabs' Custom LLM.

### Safety flags — important

These gate live, outward-facing writes. **Default off; flip deliberately.**

| Flag | Off (default) | On |
|---|---|---|
| `HELPDESK_DRAFT_EXECUTE` | drafts recorded in our audit log only | AI places unsent draft replies on real Zoho tickets |
| `HELPDESK_TAG_EXECUTE` | tags/routing recorded only | AI writes tags/priority/assignee to real Zoho tickets |
| `HELPDESK_WHATSAPP_AUTO_REPLY_EXECUTE` | WhatsApp answers generated + recorded only | AI **sends** a WhatsApp reply immediately, no human review — separate and more cautious than the email draft flag; do not enable until `ZohoDeskClient.send_whatsapp_reply`'s delivery mechanism is confirmed against a real WhatsApp ticket |
| `CSAT_AUTO_CREATE` | CSAT invitations created on demand only | inbound voice webhook auto-creates them per eligible call |

Cron cadence: `HELPDESK_PIPELINE_CRON`, `CAMPAIGN_ORCHESTRATION_CRON`,
`CAMPAIGN_OUTBOUND_CONCURRENCY`. Outbound auto-retry: `CAMPAIGN_OUTBOUND_MAX_ATTEMPTS`
(default 3) — the orchestrator re-queues retryable outcomes (no_answer/busy/
voicemail/failed) up to this many attempts per candidate before completing;
set to 1 to disable auto-retry.

---

## 6. Scripts (`scripts/`)

Naming conventions:

- **`check_*`** — read-only diagnostics against a live integration
  (`check_zoho_auth`, `check_surveymonkey`, `check_sharepoint_access`,
  `check_kb_search`, …). Safe to run; they verify access and print state.
- **`enqueue_*`** — push a single job onto the queue (for a system cron or a
  one-off run).
- **`run_*`** — long-lived processes (worker, the two schedulers).
- **one-offs** — `setup_csat_survey` (creates the standing CSAT survey),
  `exchange_zoho_grant_code` (mint a Zoho refresh token), `seed_demo_data`,
  `sync_helpdesk_kb` (rebuild the KB vector index), `backfill_zoho_ticket_mirror`.
- **`migrate_*`** — one-shot, idempotent schema migrations for an existing DB
  (`create_all` never alters existing tables): `migrate_outbound_attempt_unique`
  (unique index guarding against duplicate call attempts),
  `migrate_campaign_outbound_fields` (the outbound call-context columns).

Run everything with `PYTHONPATH=. .venv/bin/python scripts/<name>.py`.

---

## 7. Testing

- `.venv/bin/python -m pytest -q` — full suite.
- `tests/conftest.py` provides an **in-memory SQLite** engine + a `session`
  fixture and a FastAPI `client` fixture (with `get_session` overridden).
- Convention: put logic in a service and test it directly; keep external calls
  (LLM/Zoho/SurveyMonkey/ElevenLabs) behind a function you can monkeypatch or a
  fake client. Pure decision logic (e.g. `decide_campaign_next_step`,
  `apply_grounding_gate`) is tested with no I/O at all.
- When a test relies on a safety flag, set it explicitly with
  `monkeypatch.setenv(...)` + `get_settings.cache_clear()` — don't rely on the
  ambient `.env`.

---

## 8. Further docs

- `docs/blocked-items-and-helpdesk-plan.md` — **current status, external
  blockers, and the full Helpdesk domain model.** Start here for context.
- `docs/api-reference.md` — endpoint reference.
- `docs/campaign-survey-outbound-csat-plan.md` — campaign + CSAT design.
- `docs/didww-elevenlabs-telephony.md` — telephony/SIP options (still blocked).
- `docs/frontend-plan.md` + `frontend/README.md` — the reference UI.

---

## 9. Contributing / keeping this README honest

This README is the front door — **keep it current as the app changes.** When a
change alters any of the following, update the relevant section in the same PR:

- a new top-level area, service group, or integration → §4,
- a new env var or safety flag → §5,
- a new script or a changed run command → §2 / §3 / §6,
- a new external dependency or a change to the inference-gateway contract → §1.

Prefer editing over appending, and keep it accurate over exhaustive — a wrong
instruction is worse than a missing one.
