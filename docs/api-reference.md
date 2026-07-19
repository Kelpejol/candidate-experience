# Candidate Experience Automation — API Reference

This document describes every HTTP endpoint currently exposed by the backend, in full detail — request shape, response shape, status codes, and what each field actually means — so a frontend/UI developer can build against it without reading the Python source.

## Before you start

**There is currently no authentication on any endpoint.** Every route below is open to anyone who can reach the server. This is fine for local development and internal testing, but **must be addressed (API keys, session auth, a reverse-proxy gate, etc.) before this is exposed to the public internet or to untrusted users.** Flagging this clearly so whoever builds the UI — and whoever deploys this — knows it's a known gap, not an oversight.

**Free interactive docs already exist.** FastAPI auto-generates a live, try-it-yourself API explorer for everything below:

- Swagger UI: `{base_url}/docs`
- ReDoc: `{base_url}/redoc`

Use those to actually click through and fire real requests while building. This document exists to explain the *business meaning* behind each field, the workflows that chain endpoints together, and the gotchas that the auto-generated docs don't explain (validation quirks, exact literal strings required, which fields are denormalized copies vs. authoritative, etc.).

## Base URL

Local development: `http://localhost:8000` (matches `PUBLIC_BASE_URL` in `.env`). Start the server with:

```bash
PYTHONPATH=. uvicorn main:app --reload
```

## System overview

This backend serves **two independent systems** per the platform's separation-of-concerns design — they intentionally share no data:

1. **Calling Agent / Campaigns** — outbound voice survey campaigns: upload candidates, send a SurveyMonkey survey, detect non-responders, call them, track outcomes. Routes: `/call-records`, `/voice`, `/webhooks/voice-agent`, `/campaigns`, `/jobs`.
2. **Helpdesk** — AI-assisted email/WhatsApp candidate support via Zoho Desk: mirrors tickets, classifies them, decides whether to draft a reply/route to a human/just tag it, and reports on how well the automation is doing. Routes: `/helpdesk/webhooks`, `/helpdesk/reports`. **Most of the Helpdesk's actual work happens in background jobs, not API calls** — there is currently no CRUD API over helpdesk tickets/actions themselves (see note at the end of the Helpdesk section on what a UI could add).

## Conventions used throughout

- All request/response bodies are JSON unless stated otherwise (file uploads use `multipart/form-data`; Twilio callbacks use `application/x-www-form-urlencoded`).
- All timestamps are ISO 8601 datetimes, UTC, e.g. `"2026-07-17T14:32:05.123456"`.
- IDs (`id`, `campaign_id`, `candidate_id`, etc.) are UUID strings, e.g. `"3f2a9e1c-7b4d-4a1e-9c3a-1234567890ab"` — generated server-side, never supplied by the client.
- A `404` always means "the resource named in the URL path doesn't exist" — check the ID.
- A `400` always means "the request was understood but rejected for a business reason" (e.g. missing configuration, invalid file) — the `detail` field explains why.
- A `422` is FastAPI's automatic validation error (wrong type, missing required field, failed a Pydantic validator) — the body is FastAPI's standard `{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}` shape, not something this backend constructs manually.
- Pagination, where supported, uses `limit`/`offset` query params, not cursors.

---

## Health

### `GET /health`

Basic liveness/readiness check. No auth, no params.

**Response `200`:**
```json
{ "status": "ok", "environment": "development" }
```

---

## Call Records

Call records are the log of every voice call (inbound support call or outbound campaign call) handled by the ElevenLabs voice agent, keyed by an `external_call_id` supplied by the calling system (ElevenLabs' conversation ID). This is the Calling Agent's system of record — no ticket is created in Zoho for these.

### `POST /call-records`

Create a new call record. Normally you won't call this directly — it's what the webhook endpoints below call internally — but it's exposed for manual/testing use.

**Request body** (`CallRecordCreate`):

| Field | Type | Required | Notes |
|---|---|---|---|
| `external_call_id` | string | ✅ | Non-empty. The idempotency key — creating a second record with the same value returns `409`. |
| `direction` | `"inbound"` \| `"outbound"` | ✅ | |
| `candidate_phone` | string \| null | | |
| `candidate_name` | string \| null | | |
| `tool_name` | string \| null | | Must be `"FOT"` or `"Scholastica"` if provided — any other value is a `422`. |
| `campaign_name` | string \| null | | |
| `disposition` | one of the literal below | ✅ | |
| `issue_summary` | string \| null | | |
| `transcription` | string \| null | | |
| `recording_url` | string \| null | | |
| `handoff_call_sid` | string \| null | | Twilio call SID of the human-handoff leg, if any |
| `handoff_status` | string \| null | | |
| `handoff_duration` | integer \| null | | Seconds |
| `handoff_bridged` | boolean \| null | | Whether the handoff leg actually connected |
| `call_start_time` | datetime \| null | | |
| `call_end_time` | datetime \| null | | |

`disposition` must be one of: `answered_by_ai`, `handed_off_to_human`, `handoff_failed`, `missed`, `voicemail`, `no_answer`, `opted_out`, `failed`.

**Response `201`** (`CallRecordResponse`):
```json
{ "accepted": true, "external_call_id": "conv_abc123", "message": "Call record created successfully" }
```

**Errors:** `409` if `external_call_id` already exists.

### `GET /call-records`

List call records with optional filters and pagination.

**Query params:**

| Param | Type | Default | Notes |
|---|---|---|---|
| `direction` | `"inbound"` \| `"outbound"` \| omit | — | |
| `disposition` | one of the disposition literals \| omit | — | |
| `tool_name` | string \| omit | — | |
| `campaign_name` | string \| omit | — | |
| `limit` | integer | 50 | 1–100 |
| `offset` | integer | 0 | ≥0 |

**Response `200`** — array of `CallRecordRead`:
```json
[
  {
    "id": "3f2a9e1c-...",
    "external_call_id": "conv_abc123",
    "direction": "inbound",
    "candidate_phone": "+2348012345678",
    "candidate_name": "Jane Doe",
    "tool_name": "FOT",
    "campaign_name": "Graduate Aptitude Test",
    "disposition": "handed_off_to_human",
    "issue_summary": "Candidate could not access test link",
    "transcription": "...",
    "recording_url": "https://...",
    "call_start_time": "2026-07-17T09:00:00",
    "call_end_time": "2026-07-17T09:04:12",
    "created_at": "2026-07-17T09:04:15",
    "handoff_call_sid": "CA...",
    "handoff_status": "completed",
    "handoff_duration": 45,
    "handoff_bridged": true
  }
]
```

### `GET /call-records/{external_call_id}`

Fetch one call record. `404` if not found.

---

## Voice (Twilio handoff)

These two endpoints are **not meant to be called by a UI** — Twilio calls them directly as part of the live-call handoff flow. Documented for completeness/understanding, not as UI-integration points.

### `POST /voice/handoff?external_call_id={id}`

Called by Twilio (or the voice agent) mid-call when a candidate needs to be bridged to a human officer. Returns **TwiML** (`application/xml`), not JSON — plays the configured hold message, then dials the configured handoff phone number, with a status callback pointed back at `/voice/handoff/status`.

### `POST /voice/handoff/status`

Twilio's status callback once the `<Dial>` leg completes. Form-encoded body with Twilio's own field names (`DialCallStatus`, `DialCallSid`, `DialCallDuration`, `DialBridged`, `RecordingUrl`), plus `external_call_id` as a query param. Updates the matching call record's handoff fields. `404` if the call record doesn't exist.

**Response `200`:**
```json
{
  "received": true,
  "external_call_id": "conv_abc123",
  "dial_call_status": "completed",
  "dial_call_sid": "CA...",
  "dial_call_duration": "45",
  "dial_bridged": true,
  "recording_url": "https://..."
}
```

---

## Voice Agent Webhooks

Also **provider-facing, not UI-facing** — these are the URLs configured in ElevenLabs' dashboard, not something a frontend calls.

### `POST /webhooks/voice-agent/call-ended`

Simple webhook variant: accepts the same body as `POST /call-records` (`CallRecordCreate`). Idempotent — if `external_call_id` already exists, returns the existing record instead of erroring.

### `POST /webhooks/voice-agent/raw`

Debug-only passthrough. Logs whatever JSON body it receives to the server console and returns `{"received": true}`. Does not persist anything. Useful when integrating a new webhook payload shape for the first time.

### `POST /webhooks/voice-agent/elevenlabs`

The real, signature-verified ElevenLabs webhook. Verifies the `elevenlabs-signature` header against the raw request body using `ELEVENLABS_WEBHOOK_SECRET`. Only processes `post_call_transcription` event types; other event types are acknowledged and ignored. Idempotent on `external_call_id`.

**Response `200` (transcription processed):**
```json
{ "received": true, "message": "Call record created successfully", "stored": true, "external_call_id": "conv_abc123" }
```

**Errors:** `401` if the signature doesn't verify; `500` if `ELEVENLABS_WEBHOOK_SECRET` isn't configured.

---

## Campaigns

The Calling Agent's core workflow. A **Campaign** groups a batch of candidates who get sent a survey (via SurveyMonkey), and — after a configurable wait — non-responders get an outbound voice call.

### Campaign lifecycle

`status` moves through (not strictly enforced by the API — this is the intended flow):

```
draft → uploaded → survey_sending → survey_sent → waiting_for_responses
      → non_response_checking → outbound_ready → outbound_calling → completed | failed
```

### `POST /campaigns`

Create a campaign.

**Request body** (`CampaignCreate`):

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | string | ✅ | Non-empty |
| `tool_name` | string \| null | | `"FOT"` or `"Scholastica"` only |
| `survey_id` | string \| null | | SurveyMonkey survey ID, if already known |
| `surveymonkey_collector_id` | string \| null | | |
| `response_wait_hours` | integer | | Default 24. Range 1–336 (14 days). How long to wait after the survey is sent before checking for non-responders. |

**Response `201`** (`CampaignRead`):
```json
{
  "id": "c1a2b3...",
  "name": "Dangote PRP Feedback",
  "tool_name": "FOT",
  "survey_id": null,
  "surveymonkey_collector_id": null,
  "status": "draft",
  "response_wait_hours": 24,
  "created_at": "2026-07-17T10:00:00",
  "survey_sent_at": null,
  "non_responder_checked_at": null
}
```

### `GET /campaigns`

List campaigns. Query: `limit` (1–100, default 50), `offset` (default 0). Response: array of `CampaignRead`.

### `GET /campaigns/survey/templates`

List SurveyMonkey surveys usable as templates (titles prefixed `TEMPLATE -` in SurveyMonkey). **Note the path** — this is registered *before* `/campaigns/{campaign_id}` in the router so `"templates"` doesn't get mistaken for a campaign ID.

**Response `200`** — array of `SurveyMonkeyTemplateRead`:
```json
[{ "id": "422991748", "title": "TEMPLATE - Campaign Feedback", "nickname": null }]
```

**Errors:** `500` if the SurveyMonkey API call fails (e.g. bad token).

### `GET /campaigns/{campaign_id}`

Fetch one campaign. `404` if not found.

### `PATCH /campaigns/{campaign_id}/status`

Transition a campaign's lifecycle status directly.

**Request body:** `{ "status": "outbound_ready" }` — must be one of the lifecycle literals above.

**Response `200`:** the updated `CampaignRead`. `404` if campaign not found.

### `GET /campaigns/{campaign_id}/summary`

Aggregate counts for a campaign's candidates.

**Response `200`** (`CampaignSummaryRead`):
```json
{
  "campaign_id": "c1a2b3...",
  "total_candidates": 240,
  "survey_statuses": { "responded": 120, "non_responder": 90, "sent": 30 },
  "call_statuses": { "not_queued": 90, "queued": 0, "completed": 0 },
  "outbound_attempts": { "answered": 40, "no_answer": 30, "failed": 20 }
}
```
The three `*_statuses`/`outbound_attempts` objects are dynamic — keys are whatever status values actually occur, so don't hardcode the key set client-side.

### `POST /campaigns/{campaign_id}/candidates`

Add candidates directly via JSON (as opposed to file upload — see next endpoint).

**Request body:** array of `CampaignCandidateCreate`:

| Field | Type | Required | Notes |
|---|---|---|---|
| `candidate_name` | string \| null | | |
| `email` | valid email \| null | | Validated as an email format |
| `phone` | string \| null | | Not validated/normalized here (only the upload path normalizes Nigerian numbers — see below) |
| `tool_name` | string \| null | | `"FOT"` / `"Scholastica"` only |
| `campaign_name` | string \| null | | |
| `external_candidate_id` | string \| null | | ID from the source ATS, if imported |
| `opted_out_call` | boolean | | Default `false` |
| `opted_out_email` | boolean | | Default `false` |

**Response `201`:** array of `CampaignCandidateRead` (see shape under the list endpoint below). `404` if campaign not found.

### `POST /campaigns/{campaign_id}/candidates/upload`

Bulk-add candidates via a `.csv` or `.xlsx` file upload (`multipart/form-data`, field name `file`).

**File format:**
- Required column: **`email`** (case/spacing-insensitive — headers are lowercased and spaces become underscores, so `"Email"`, `"E-mail "` etc. won't match; use exactly `email`).
- Optional columns: `candidate_name`, `phone`, `tool_name`, `campaign_name`, `external_candidate_id`.
- `tool_name`/`campaign_name`, if blank in a row, fall back to the parent campaign's own `tool_name`/`name`.
- **Phone numbers are auto-normalized to Nigerian E.164-ish format**: a value starting with `0` (e.g. `0803...`) becomes `+234803...`; a value starting with `234` gets a `+` prefixed; a value already starting with `+` is left as-is. Tell users uploading files this happens automatically — they don't need to pre-format phone numbers, but non-Nigerian numbers won't be recognized/reformatted correctly.

**Response `201`:** array of `CampaignCandidateRead`. **Errors:** `404` campaign not found; `400` if the file extension isn't `.csv`/`.xlsx` or the `email` column is missing.

### `GET /campaigns/{campaign_id}/candidates`

List a campaign's candidates. Query: `limit` (1–500, default 100), `offset`.

**Response `200`** — array of `CampaignCandidateRead`:
```json
{
  "id": "cand_1...",
  "campaign_id": "c1a2b3...",
  "candidate_name": "Jane Doe",
  "email": "jane@example.com",
  "phone": "+2348012345678",
  "tool_name": "FOT",
  "campaign_name": "Dangote PRP Feedback",
  "external_candidate_id": "ATS-991",
  "surveymonkey_recipient_id": "rcpt_123",
  "surveymonkey_response_id": null,
  "surveymonkey_response_status": null,
  "survey_responded_at": null,
  "survey_status": "sent",
  "call_status": "not_queued",
  "opted_out_call": false,
  "opted_out_email": false,
  "created_at": "2026-07-17T10:05:00",
  "updated_at": "2026-07-17T10:05:00"
}
```

`survey_status` is one of: `not_sent`, `sent`, `responded`, `partial_response`, `non_responder`, `excluded_opt_out`, `failed`.
`call_status` is one of: `not_queued`, `queued`, `calling`, `answered`, `responded_by_call`, `no_answer`, `busy`, `voicemail`, `failed`, `opted_out`, `handed_off_to_human`.

### `PATCH /campaigns/{campaign_id}/candidates/{candidate_id}/survey-status`

Manually set a candidate's survey status. Body: `{ "survey_status": "non_responder" }`. `404` if campaign or candidate not found.

### `GET /campaigns/{campaign_id}/non-responders`

List candidates eligible for outbound follow-up (haven't responded to the survey, not opted out). Query: `limit` (1–500, default 100), `offset`. Response: array of `CampaignCandidateRead`.

### Survey distribution workflow (SurveyMonkey)

These four endpoints are meant to be called **in sequence** to actually send a survey:

**1. `POST /campaigns/{campaign_id}/survey/create-from-template`**
Clones the configured (or given) SurveyMonkey template survey into a campaign-specific copy, interpolating placeholders like `{{campaign_name}}`. Body (optional): `{ "template_survey_id": "422991748" }` — omit to use the `SURVEYMONKEY_CAMPAIGN_TEMPLATE_SURVEY_ID` default. Response: the updated `CampaignRead` (now with `survey_id` populated). `404` campaign not found; `500` on SurveyMonkey API failure.

**2. `POST /campaigns/{campaign_id}/survey/message`**
Creates an **unsent** SurveyMonkey collector + message draft.

Request body (`CampaignSurveyMessageCreate`):
| Field | Type | Required | Notes |
|---|---|---|---|
| `collector_id` | string \| null | | Omit to create a new collector |
| `collector_name` | string \| null | | |
| `subject` | string | ✅ | Non-empty |
| `body` | string \| null | | See placeholder requirement below |

**If `body` is provided, it must include all four of these placeholder tokens** (either bracket or double-brace form), or the request is rejected with `422`:
- A survey link: `[SurveyLink]`, `{{SurveyLink}}`, `[FirstQuestion]`, or `{{FirstQuestion}}`
- An opt-out link: `[OptOutLink]` or `{{OptOutLink}}`
- A footer link: `[FooterLink]` or `{{FooterLink}}`
- A privacy link: `[PrivacyLink]` or `{{PrivacyLink}}`

Omit `body` entirely to let SurveyMonkey use its own default email design (recommended — the backend only overrides the subject in that case).

Response `201` (`CampaignSurveyMessageCreateResponse`):
```json
{ "campaign_id": "c1...", "survey_id": "423001176", "collector_id": "440321814", "message_id": "msg_1", "subject": "Share your assessment experience" }
```
`404` campaign not found; `400` business-rule failure (e.g. campaign has no survey yet); `500` SurveyMonkey API error.

**3. `POST /campaigns/{campaign_id}/survey/recipients/prepare`**
Adds eligible campaign candidates as recipients on the message — **does not send anything yet**.

Request: `{ "collector_id": "440321814", "message_id": "msg_1" }` (`collector_id` optional if only one exists).

Response (`CampaignSurveyRecipientsPrepareResponse`):
```json
{ "campaign_id": "c1...", "collector_id": "440321814", "message_id": "msg_1", "eligible_candidates": 238, "skipped_candidates": 2, "prepared_recipients": 238 }
```

**4. `POST /campaigns/{campaign_id}/survey/message/send`**
Sends the prepared message — **irreversible**. Requires an exact confirmation string as a deliberate safety gate.

Request:
```json
{ "collector_id": "440321814", "message_id": "msg_1", "confirm_send": "SEND_SURVEY_EMAILS" }
```
`confirm_send` must be **exactly** the literal string `SEND_SURVEY_EMAILS` — anything else raises `400`. **The UI should surface a real "are you sure" confirmation before firing this request**, since it triggers real emails to real candidates.

Response (`CampaignSurveyMessageSendResponse`):
```json
{ "campaign_id": "c1...", "collector_id": "440321814", "message_id": "msg_1", "sent": true, "updated_candidates": 238 }
```

### `POST /campaigns/{campaign_id}/survey/sync-responses`

**Synchronous** SurveyMonkey response sync — call this to pull in survey responses and update candidate statuses right now (blocks until done; can be slow for large campaigns — prefer the job-queue variant below for anything beyond quick testing).

Response (`CampaignSurveySyncResponse`):
```json
{ "total_recipients": 240, "completed": 150, "partial": 10, "non_responders": 80, "updated_candidates": 240 }
```
`400` if the campaign lacks `survey_id`/`surveymonkey_collector_id`; `500` on SurveyMonkey API failure.

### `POST /campaigns/{campaign_id}/survey/sync-responses/jobs`

**Asynchronous** version — enqueues the same sync as a background RQ job instead of blocking the request. Prefer this for the UI. If a sync job for this campaign is already queued/running, this reuses it rather than double-enqueuing.

**Response `202`** (`JobQueuedRead`):
```json
{ "job_id": "a1b2c3-...", "status": "queued" }
```
Poll `GET /jobs/{job_id}` (below) for the result. `404` campaign not found; `400` if survey/collector not configured.

### Outbound call queue

### `POST /campaigns/{campaign_id}/outbound/build-queue`

Builds the initial outbound-call queue: creates one `OutboundCallAttempt` (status `queued`) per eligible non-responder. Response `201`: array of `OutboundCallAttemptRead` (shape below). `404` campaign not found.

### `POST /campaigns/{campaign_id}/outbound/retry-queue?max_attempts={n}`

Builds a retry queue for candidates whose previous attempts failed/went unanswered, up to `max_attempts` tries each (query param, 1–10, default 3). Response `201`: array of `OutboundCallAttemptRead`.

### `GET /campaigns/{campaign_id}/outbound/attempts`

List outbound call attempts. Query: `limit` (1–500, default 100), `offset`.

**Response `200`** — array of `OutboundCallAttemptRead`:
```json
{
  "id": "att_1...",
  "campaign_id": "c1...",
  "candidate_id": "cand_1...",
  "candidate_name": "Jane Doe",
  "candidate_email": "jane@example.com",
  "campaign_name": "Dangote PRP Feedback",
  "tool_name": "FOT",
  "phone": "+2348012345678",
  "attempt_number": 1,
  "status": "answered",
  "disposition": "responded_by_call",
  "elevenlabs_conversation_id": "conv_xyz",
  "transcript": "...",
  "summary": "Candidate rated experience 4/5",
  "recording_url": "https://...",
  "started_at": "2026-07-17T11:00:00",
  "ended_at": "2026-07-17T11:03:30",
  "created_at": "2026-07-17T10:59:00"
}
```
`status` (`OutboundCallAttemptStatus`) is one of: `queued`, `calling`, `answered`, `responded_by_call`, `no_answer`, `busy`, `voicemail`, `failed`, `opted_out`, `handed_off_to_human`.

### `GET /campaigns/{campaign_id}/outbound/attempts/next`

Fetch the single next queued attempt (used internally by the outbound-call worker to know what to dial next). `404` if no attempt is queued, or campaign doesn't exist. Response: one `OutboundCallAttemptRead`.

### `PATCH /campaigns/{campaign_id}/outbound/attempts/{attempt_id}/status`

Update an attempt's outcome (typically called by a provider callback after a call completes).

Request (`OutboundCallAttemptStatusUpdate`):
```json
{ "status": "answered", "disposition": "responded_by_call", "elevenlabs_conversation_id": "conv_xyz", "transcript": "...", "summary": "...", "recording_url": "https://..." }
```
Only `status` is required; the rest are optional. Response: updated `OutboundCallAttemptRead`. `404` campaign or attempt not found.

### `POST /campaigns/{campaign_id}/outbound/execute-next/jobs`

Enqueues a background job to place the next queued outbound call. De-duplicated per campaign (a second call while one's already running reuses the existing job).

**Response `202`** (`JobQueuedRead`): `{ "job_id": "...", "status": "queued" }`. `404` campaign not found.

---

## Jobs

Generic polling endpoint for any background job enqueued by the endpoints above.

### `GET /jobs/{job_id}`

**Response `200`** (`JobStatusRead`):
```json
{
  "job_id": "a1b2c3-...",
  "status": "finished",
  "created_at": "2026-07-17T10:00:00",
  "enqueued_at": "2026-07-17T10:00:00",
  "started_at": "2026-07-17T10:00:02",
  "ended_at": "2026-07-17T10:00:45",
  "result": { "total_recipients": 240, "completed": 150, "partial": 10, "non_responders": 80, "updated_candidates": 240 },
  "error": null,
  "error_traceback": null
}
```
- `status` is one of RQ's job states: `queued`, `started`, `deferred`, `scheduled`, `finished`, `failed`, `stopped`, `canceled` (or `unknown` if it can't be determined).
- `result` is only populated once `status` is `finished` — its shape depends on which job type was enqueued (matches the corresponding synchronous endpoint's response shape, e.g. `CampaignSurveySyncResponse`'s fields for a survey-sync job).
- `error`/`error_traceback` are only populated if `status` is `failed` — `error` is a short one-line message, `error_traceback` is the full Python traceback (useful for debugging, probably don't show raw to end users).
- **`404`** if the job ID is unknown or its result has expired from Redis (jobs have a `result_ttl`, typically 3600s = 1 hour, after which they're gone — poll promptly after enqueueing).

**Recommended UI pattern:** after any `202` response, poll this endpoint every 1–2 seconds until `status` is `finished` or `failed`.

---

## Helpdesk Webhooks

### `POST /helpdesk/webhooks/zoho/ticket`

Zoho Desk calls this (once the pending workflow-rule setup is complete — see `docs/blocked-items-and-helpdesk-plan.md`) whenever a ticket is created/updated. **Not a UI endpoint.**

Auth: a shared token, either as header `X-Webhook-Token: <token>` or query param `?token=<token>`, checked against `ZOHO_WEBHOOK_TOKEN`.

Request body: `{ "ticketId": "<zoho ticket id>" }` (also accepts `"id"` as a fallback key).

**Response `200`:**
```json
{ "status": "ok", "zoho_ticket_id": "1010551000054574796", "ai_job_id": "b7e1-..." }
```
The handler upserts the ticket into our local mirror, then enqueues `process_single_ticket_job` (classify → decide → maybe draft) in the background — the HTTP response returns immediately, before AI processing completes. `401` invalid/missing token; `422` no ticket ID in payload.

---

## Helpdesk Reports

### `GET /helpdesk/reports/summary`

The one read endpoint currently exposed over the Helpdesk's AI decision audit trail (`HelpdeskAIAction`). Genuinely useful for a UI dashboard.

**Response `200`:**
```json
{
  "total_tickets": 50,
  "action_counts": { "route_to_human": 14, "tag_only": 27, "draft_reply": 9 },
  "rule_counts": {
    "low_confidence": 3,
    "confirmation_no_reply_needed": 3,
    "answerable_draft_first": 9,
    "sensitive_never_automated": 4,
    "draft_writer_escalated": 3,
    "complaint_keyword_backstop": 1,
    "spam_claim_on_reply_backstop": 2,
    "negative_sentiment_backstop": 1,
    "spam_no_reply": 1,
    "system_bounce_notification": 23
  },
  "issue_category_counts": { "general_enquiry": 3, "technical_issue": 5, "test_invitation_issue": 4 },
  "confidence_counts": { "low": 3, "high": 22, "medium": 2 },
  "kb_gap_count": 0,
  "sensitive_count": 4,
  "drafts_awaiting_execution": 9,
  "drafts_placed_on_zoho": 0,
  "automation_rate": 0.72,
  "category_action_mix": {
    "test_invitation_issue": { "draft_reply": 4 },
    "technical_issue": { "draft_reply": 2, "route_to_human": 3 }
  }
}
```

Field meanings:

| Field | Meaning |
|---|---|
| `total_tickets` | Distinct tickets processed, **collapsed to the latest decision per ticket** (a ticket re-processed after a new candidate reply is counted once, using its most recent outcome) |
| `action_counts` | How many tickets landed on each of the three possible actions: `draft_reply` (AI wrote a reply), `route_to_human` (an officer must handle it), `tag_only` (tagged, no reply needed — confirmations, spam) |
| `rule_counts` | Which specific decision rule fired for each ticket — see `app/services/helpdesk_decision.py` for what each rule name means; useful for auditing *why* the AI did what it did |
| `issue_category_counts` | Tally by classified category (the 14-category taxonomy in `app/core/helpdesk_taxonomy.py` — see below) |
| `confidence_counts` | Classifier's self-reported confidence: `high` / `medium` / `low` |
| `kb_gap_count` | Tickets where the knowledge base had nothing relevant enough to draft from — a content gap to go fill, not a bug |
| `sensitive_count` | Tickets flagged sensitive (never auto-drafted, always routed to a human) |
| `drafts_awaiting_execution` | Drafts written by the AI but not yet placed on the real Zoho ticket (gated by `HELPDESK_DRAFT_EXECUTE`) |
| `drafts_placed_on_zoho` | Drafts actually written onto the live Zoho ticket |
| `automation_rate` | `(draft_reply + tag_only) / total_tickets`, rounded to 3 decimals — the share of tickets the AI handled without needing a human decision |
| `category_action_mix` | Per-category breakdown of which action was taken — the evidence base for deciding which categories are safe to promote to full auto-send later |

### What a richer Helpdesk UI would need that doesn't exist yet

If whoever's building the UI wants more than the summary dashboard above — e.g. a ticket list/detail view, or a "review this AI draft" screen — **those endpoints don't exist yet.** The data is all there in the database (`HelpdeskTicketMirror` and `HelpdeskAIAction` tables), just not exposed over HTTP yet. Worth flagging back so we can prioritize building:
- `GET /helpdesk/tickets` — list mirrored tickets with their AI disposition
- `GET /helpdesk/tickets/{id}` — one ticket + its full `HelpdeskAIAction` history
- Something to let an officer review/edit/approve a pending draft from a UI instead of Zoho directly (not currently planned — Zoho Desk itself is the intended review surface)

---

## Reference: controlled vocabularies

Useful for building dropdowns/selects that match what the backend actually accepts.

**Assessment tools** (`tool_name` field, wherever it appears): `FOT`, `Scholastica`. Any other value is rejected with `422`.

**Helpdesk issue categories** (read-only reporting values, not user-selectable input anywhere currently): `availability_confirmation`, `test_invitation_issue`, `technical_issue`, `reschedule_request`, `result_question` *(sensitive)*, `payment_issue` *(sensitive)*, `identity_verification` *(sensitive)*, `complaint` *(sensitive)*, `legal_or_privacy` *(sensitive)*, `application_enquiry`, `scholarship_training_enquiry`, `business_or_partnership`, `general_enquiry`, `spam_or_irrelevant`. *(sensitive)* categories are never auto-drafted, always routed to a human.
