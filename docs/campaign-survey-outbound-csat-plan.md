# Campaign Survey, Outbound Calling, and CSAT Plan

This plan covers the PSA Phase 2 calling-agent work:

- upload a campaign candidate list
- send campaign surveys through SurveyMonkey
- detect candidates who do not respond
- call non-responders with an outbound ElevenLabs agent
- merge email survey responses and phone survey responses into one campaign result
- send post-call CSAT after inbound conversations

It deliberately does not cover the Helpdesk/Zoho flow. This remains part of the Calling Agent system, not the Helpdesk.

## PSA Anchor

The relevant PSA flow is UC-02 and FR-07 to FR-10:

```text
Campaign sheet uploaded
-> survey emailed to all candidates
-> wait window passes
-> non-responders detected from SurveyMonkey response status
-> opt-out filter applied
-> remaining candidates are called by the voice agent
-> call outcomes and phone survey responses merge with email responses
```

The PSA also states that SurveyMonkey is used for:

```text
Campaign surveys
Post-resolution CSAT
Delivered by email
```

## Main Design Decision

Use two different ElevenLabs agent configurations:

1. **Inbound Support Agent**
   - answers candidate support questions
   - can hand off to 3CX
   - sends CSAT after the call

2. **Outbound Survey Agent**
   - calls candidates who did not answer the email survey
   - collects survey answers by voice
   - handles opt-out requests
   - avoids turning into a full support call
   - can escalate/handoff only when the candidate raises a real support issue

The agents can share some approved company language, but they should not share the same prompt/goal. The outbound survey agent has a much narrower job.

## End-To-End Flows

### Flow A: Campaign Upload and Survey Dispatch

```text
Candidate Experience team uploads campaign list
        |
        v
Backend creates campaign record
        |
        v
Backend normalizes candidate rows
        |
        v
Backend sends/links campaign survey through SurveyMonkey
        |
        v
Backend stores SurveyMonkey survey/collector/message IDs
        |
        v
Campaign status becomes "survey_sent"
```

For now, we should not build complicated document parsing. Start with a simple list shape:

```json
[
  {
    "candidate_name": "Ada Lovelace",
    "email": "ada@example.com",
    "phone": "+2348012345678",
    "tool_name": "FOT",
    "campaign_name": "Graduate Aptitude Test"
  }
]
```

Later, support real uploads:

- CSV
- XLSX
- Google Sheet export
- maybe DOCX/PDF only if the business truly uses them

For unknown document types, the app should eventually parse into a preview table and ask a human to map columns before dispatch. Do not try to silently guess important fields in production.

### Flow B: Non-Responder Detection

```text
Wait configured response window
        |
        v
Fetch SurveyMonkey response status in bulk
        |
        v
Compare SurveyMonkey respondents with campaign candidate list
        |
        v
Mark candidates as responded or non_responder
        |
        v
Filter non-responders against opt-out records
        |
        v
Build outbound call queue
```

The PSA specifically says bulk read-back should be used to respect API quota. Avoid per-candidate polling.

Initial wait-window examples:

```text
24 hours after survey sent
48 hours after survey sent
custom per campaign
```

### Flow C: Outbound Survey Calls

```text
Outbound queue ready
        |
        v
Backend submits candidate calls to ElevenLabs
        |
        v
ElevenLabs calls through DIDWW outbound trunk
        |
        v
Outbound Survey Agent collects voice answers
        |
        v
ElevenLabs post-call webhook reaches backend
        |
        v
Backend stores call record and survey response
        |
        v
Campaign result updates
```

Call outcomes to track:

- answered
- answered_by_ai
- responded_by_call
- no_answer
- busy
- voicemail
- failed
- opted_out
- handed_off_to_human

Retry policy should be explicit. Example:

```text
No answer: retry up to 2 times
Busy: retry later
Voicemail: do not collect response; optionally leave short message
Failed: log carrier/agent error
Opted out: stop all future outbound calls for that channel
```

### Flow D: Merge Campaign Results

```text
SurveyMonkey email responses
        |
        v
Campaign result table
        ^
        |
Phone survey responses from outbound calls
```

The campaign dashboard should eventually show:

- total uploaded
- survey sent
- responded by email
- non-responders
- excluded by opt-out
- called
- responded by phone
- unreached
- failed
- handed off
- final response rate

### Flow E: Inbound Post-Call CSAT

```text
Inbound support call ends
        |
        v
ElevenLabs webhook creates call record
        |
        v
Backend decides if CSAT is eligible
        |
        v
Backend sends SurveyMonkey CSAT email
        |
        v
Candidate submits CSAT
        |
        v
Backend receives/fetches CSAT response
        |
        v
Call record is updated with satisfaction result
```

CSAT should be sent only when:

- candidate identity/contact is known
- candidate has not opted out of email surveys
- the call was not a test/internal call
- the candidate was not abusive/fraud/spam, if such flags exist later

If inbound call only gives us a phone number and no email, we need a lookup from the existing app:

```text
candidate_phone -> candidate profile -> email
```

Until that exists, CSAT can be marked:

```text
csat_status = "pending_contact_lookup"
```

## SurveyMonkey Role

SurveyMonkey should remain the survey engine, not our backend.

SurveyMonkey owns:

- survey questions
- collectors
- email survey delivery, if using SurveyMonkey email collectors
- response collection
- response status
- CSAT survey forms

Our backend owns:

- campaign import
- candidate normalization
- campaign status
- opt-out gating
- reconciliation
- outbound call queue
- ElevenLabs call records
- merged campaign reporting

Official SurveyMonkey API docs are at:

```text
https://developer.surveymonkey.com/api/v3/
```

Integration details to confirm when implementation starts:

- OAuth/API token setup
- how surveys are selected for a campaign
- whether SurveyMonkey or our existing app sends the email invitation
- collector/message IDs needed for response tracking
- webhook availability for completed responses
- API rate limits on the active plan

## Initial Data Model

### Campaign

Represents one uploaded campaign.

Fields:

```text
id
name
tool_name
survey_id
surveymonkey_collector_id
status
uploaded_source_name
response_wait_hours
created_at
survey_sent_at
non_responder_checked_at
```

Possible statuses:

```text
draft
uploaded
survey_sending
survey_sent
waiting_for_responses
non_response_checking
outbound_ready
outbound_calling
completed
failed
```

### CampaignCandidate

Represents one person in a campaign.

Fields:

```text
id
campaign_id
candidate_name
email
phone
tool_name
campaign_name
external_candidate_id
survey_status
call_status
opted_out_call
opted_out_email
created_at
updated_at
```

Survey statuses:

```text
not_sent
sent
responded
non_responder
excluded_opt_out
failed
```

Call statuses:

```text
not_queued
queued
calling
answered
responded_by_call
no_answer
busy
voicemail
failed
opted_out
handed_off_to_human
```

### CampaignSurveyResponse

Stores normalized response data from SurveyMonkey or voice collection.

Fields:

```text
id
campaign_id
candidate_id
source
surveymonkey_response_id
elevenlabs_conversation_id
answers_json
submitted_at
```

Sources:

```text
surveymonkey_email
elevenlabs_voice
manual
```

### OutboundCallAttempt

Represents each call attempt.

Fields:

```text
id
campaign_id
candidate_id
elevenlabs_conversation_id
phone
attempt_number
status
started_at
ended_at
disposition
transcript
summary
recording_url
```

### CSATInvitation

Represents post-call CSAT after inbound support calls.

Fields:

```text
id
call_record_id
candidate_email
candidate_phone
surveymonkey_survey_id
surveymonkey_collector_id
surveymonkey_response_id
status
sent_at
responded_at
score
comment
```

Statuses:

```text
pending_contact_lookup
ready_to_send
sent
responded
skipped_opt_out
failed
```

## API Surface We Should Build

Start simple. Use JSON lists before file upload.

### Campaigns

```text
POST /campaigns
GET /campaigns
GET /campaigns/{campaign_id}
```

### Candidate List Upload

First version:

```text
POST /campaigns/{campaign_id}/candidates
```

Body:

```json
[
  {
    "candidate_name": "Ada Lovelace",
    "email": "ada@example.com",
    "phone": "+2348012345678",
    "tool_name": "FOT",
    "campaign_name": "Graduate Aptitude Test"
  }
]
```

Later version:

```text
POST /campaigns/{campaign_id}/uploads
```

With file upload and preview/mapping.

### Survey Dispatch

```text
POST /campaigns/{campaign_id}/survey/send
```

This should create/send the SurveyMonkey invitation or trigger whatever SurveyMonkey collector flow we choose.

### Non-Responder Check

```text
POST /campaigns/{campaign_id}/non-responders/check
```

This can be manual first, then scheduled later.

### Outbound Calling

```text
POST /campaigns/{campaign_id}/outbound/build-queue
POST /campaigns/{campaign_id}/outbound/start
```

### Webhooks

```text
POST /webhooks/surveymonkey
POST /webhooks/voice-agent/elevenlabs
POST /webhooks/didww/call-events
```

We already have the ElevenLabs webhook for call records. We will extend it later to also update campaign/outbound records when the call belongs to a campaign.

### CSAT

```text
POST /calls/{external_call_id}/csat/send
POST /webhooks/surveymonkey/csat
```

CSAT can be automatic later. Manual trigger first is easier to test.

## Background Jobs

Initial jobs:

```text
sync_surveymonkey_responses(campaign_id)
detect_non_responders(campaign_id)
build_outbound_call_queue(campaign_id)
retry_failed_or_no_answer_calls(campaign_id)
send_pending_csat()
sync_csat_responses()
```

For now these can be manual endpoints. Later they can move to a scheduler.

## Opt-Out Rules

Opt-out is required before outbound calls.

Rules:

- If candidate opted out of calls, do not call.
- If candidate opts out during an outbound voice call, mark call opt-out immediately.
- If candidate opted out of email, do not send SurveyMonkey email/CSAT email.
- Keep call, email, and WhatsApp opt-outs separate.

For the first build, opt-out can be stored on `CampaignCandidate`.

Later, opt-out should come from the existing app/candidate profile service.

## Survey Questions Strategy

Campaign survey and CSAT should be separate surveys.

### Campaign Survey

Used for campaign feedback after assessments or candidate journey events.

Example voice-friendly questions:

```text
1. Did you complete the assessment successfully?
2. How would you rate the assessment experience from 1 to 5?
3. What issue, if any, did you experience?
4. Would you like a support officer to follow up?
```

### Inbound CSAT Survey

Used after a candidate contacted the AI support line.

Example questions:

```text
1. How satisfied were you with the support you received?
2. Was your issue resolved?
3. How would you rate the AI assistant from 1 to 5?
4. Would you like a human follow-up?
5. Optional comment.
```

Keep CSAT short. Candidates will not complete long post-call surveys.

## ElevenLabs Outbound Agent Plan

Create a separate outbound survey agent.

Prompt goals:

- identify itself as an automated Dragnet candidate experience survey call
- confirm it is speaking with the candidate
- explain the survey is short
- ask only approved survey questions
- capture answers in structured fields
- respect opt-out immediately
- avoid answering broad support questions
- if support issue appears, offer handoff/follow-up rather than improvising

Suggested behavior:

```text
If candidate answers survey:
    collect response
    thank them
    end call

If candidate asks support question:
    answer only if it is in the outbound agent KB
    otherwise mark follow_up_requested or handoff

If candidate says stop/don't call me:
    mark opted_out
    apologize briefly
    end call

If voicemail:
    leave short message if configured
    no survey response is recorded
```

Outbound agent dynamic variables:

```text
candidate_name
tool_name
campaign_name
survey_name
campaign_id
candidate_id
support_phone_or_email
```

## How This Fits Current Code

Already built:

- `CallRecord`
- call record API
- ElevenLabs post-call webhook with signature verification
- ElevenLabs payload mapper

Next code area:

```text
Campaigns
Campaign candidates
Survey dispatch placeholders
Non-responder detection placeholders
Outbound queue placeholders
CSAT invitation placeholders
```

Do not wire real SurveyMonkey first. Build the local domain model and API with fake/manual statuses first.

## Build Sequence

### Step 1: Local Campaign Model

Build campaign and campaign candidate tables.

Acceptance:

- create campaign
- add list of candidates
- list campaign candidates
- validate required candidate fields

### Step 2: Manual Survey Status

Before SurveyMonkey API, allow test code/manual endpoint to mark candidates as:

```text
sent
responded
non_responder
```

Acceptance:

- can simulate a campaign where some candidates responded and others did not

### Step 3: Non-Responder Builder

Build logic that returns candidates who:

```text
survey_status != responded
call opt-out is false
phone exists
```

Acceptance:

- opted-out candidates are excluded
- candidates without phone are excluded
- responders are excluded

### Step 4: Outbound Queue

Create outbound call attempt records for eligible non-responders.

Acceptance:

- queue records are created idempotently
- duplicate queue builds do not duplicate attempts

### Step 5: SurveyMonkey Adapter Interface

Add a service interface like:

```text
send_campaign_survey(campaign)
fetch_campaign_responses(campaign)
send_csat(call_record)
fetch_csat_responses()
```

Initial implementation can be fake/in-memory or no-op. Real SurveyMonkey implementation comes after API credentials and survey structure are known.

### Step 6: Real SurveyMonkey Integration

Integrate API only after:

- SurveyMonkey account is ready
- survey IDs are known
- collector strategy is decided
- API token/OAuth is ready
- rate limits are confirmed

### Step 7: Outbound ElevenLabs Calls

Integrate outbound calls after DIDWW outbound trunk works.

Acceptance:

- one test candidate can be called
- webhook result updates `OutboundCallAttempt`
- campaign result counts update

### Step 8: Inbound CSAT

After inbound call record is saved:

- create CSAT invitation if candidate email is known
- send SurveyMonkey CSAT
- store response

Acceptance:

- inbound call can trigger CSAT
- CSAT response links back to call record

## First Thing To Build Next

Build local campaign storage first.

Do not start with SurveyMonkey API yet.

First implementation target:

```text
POST /campaigns
POST /campaigns/{campaign_id}/candidates
GET /campaigns/{campaign_id}/candidates
```

Using JSON list input only.

That gives us the core campaign list that every later step depends on.
