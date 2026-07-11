# Blocked Items and Helpdesk Implementation Plan

This document keeps track of the work we are intentionally parking while we move from the Calling Agent foundation into the Helpdesk build.

The PSA is clear about the separation:

- Calling Agent = voice call centre and outbound survey calling.
- Helpdesk = WhatsApp and email support through Zoho Desk.
- The two systems should not share storage, knowledge base, or conversation threads.
- Calling Agent records stay in our call/campaign service.
- Helpdesk records stay in Zoho Desk.

## Blocked Calling Agent Items

These are not abandoned. They are blocked because they need external platform access, provider configuration, live credentials, or real telephony setup.

### SurveyMonkey Blocked Items

Status: blocked until SurveyMonkey access/scopes are available again.

What is already working:

- SurveyMonkey API token can list surveys.
- We can fetch collectors.
- We can fetch collector recipients.
- We can bulk-fetch responses.
- We can sync campaign candidates to `responded`, `partial_response`, or `non_responder`.
- Slow SurveyMonkey sync now runs through Redis/RQ.
- Duplicate sync jobs are prevented with a Redis job lock.

Still blocked:

1. Survey template selection
   - Fetch available SurveyMonkey survey templates or approved template surveys.
   - Let an officer choose the template from our app.
   - Store the selected template against the campaign.

2. Survey copy/create for each campaign
   - Copy the selected template into a campaign-specific survey.
   - Interpolate campaign values such as `{{campaign_name}}`, `{{tool_name}}`, and `{{support_contact}}`.
   - Store the new campaign `survey_id`.

3. Collector creation
   - Create or configure an email collector for the campaign survey.
   - Store the `collector_id`.

4. Recipient/contact upload into SurveyMonkey
   - Push campaign candidates into SurveyMonkey as recipients.
   - Store SurveyMonkey `recipient_id` back on each `CampaignCandidate`.

5. Email invitation sending
   - Send the survey email through SurveyMonkey.
   - Store the message/send metadata if SurveyMonkey exposes it.
   - Move campaign status to `survey_sent`.

6. SurveyMonkey webhook setup
   - Register webhook for survey response events.
   - Verify webhook signature/secret.
   - Update candidate response status when SurveyMonkey sends events.
   - Keep the bulk sync as a fallback/reconciliation job.

7. CSAT survey flow
   - Configure a separate CSAT survey template.
   - Send CSAT after eligible inbound/support calls.
   - Link CSAT response back to the call record.

Required SurveyMonkey details later:

```env
SURVEYMONKEY_ACCESS_TOKEN=
SURVEYMONKEY_CLIENT_ID=
SURVEYMONKEY_CLIENT_SECRET=
SURVEYMONKEY_CAMPAIGN_SURVEY_ID=
SURVEYMONKEY_CAMPAIGN_COLLECTOR_ID=
SURVEYMONKEY_CSAT_SURVEY_ID=
SURVEYMONKEY_CSAT_COLLECTOR_ID=
SURVEYMONKEY_WEBHOOK_SECRET=
```

Needed scopes later:

- View Surveys
- Create/Modify Surveys
- View Collectors
- Create/Modify Collectors
- View Contacts
- Create/Modify Contacts
- View Responses
- View Response Details
- View Webhooks
- Create/Modify Webhooks

### Telephony + ElevenLabs Blocked Items

Status: blocked until the SIP path and ElevenLabs phone setup are live. Needs IT.

Direction update (July 2026): we are likely NOT using DIDWW. The current
direction is to use our existing self-hosted 3CX as the SIP endpoint:

- Open port 5060 on the 3CX host for SIP traffic from ElevenLabs.
- Firewall allowlist the ElevenLabs SIP origination/termination IPs.
- Connect ElevenLabs to 3CX as a SIP trunk (inbound calls route from 3CX to
  the ElevenLabs agent; outbound calls from ElevenLabs go out through 3CX).
- This also simplifies handoff: the AI leg and the officer leg live on the
  same 3CX, instead of dial-and-bridge across carriers.

This is blocked on IT (firewall change, 3CX trunk configuration). The DIDWW
items below are kept for reference in case the 3CX route falls through; the
ElevenLabs-side items (agent IDs, phone number ID, webhooks, live call tests)
apply either way.

What is already working:

- Inbound ElevenLabs post-call webhook ingestion.
- Call record storage.
- Handoff metadata tracking.
- Outbound call attempts and retry queue.
- Redis/RQ job plumbing.
- Outbound execution path wired to ElevenLabs batch calls.

Still blocked:

1. DIDWW DID purchase/configuration
   - Buy/configure the Nigerian DID.
   - Confirm inbound channels.
   - Confirm inbound SIP routing into ElevenLabs.

2. DIDWW outbound trunk
   - Enable outbound trunking.
   - Confirm Nigerian local routing.
   - Confirm outbound concurrency.
   - Confirm caller ID presentation.

3. ElevenLabs telephony setup
   - Connect DIDWW/SIP to ElevenLabs.
   - Confirm the ElevenLabs phone number ID.
   - Confirm the outbound agent ID.
   - Configure the inbound support agent and outbound survey agent separately.

4. Live inbound call test
   - Candidate calls Nigerian DID.
   - DIDWW routes to ElevenLabs.
   - ElevenLabs agent answers.
   - Webhook lands in our backend.
   - Call record is created.

5. Live outbound call test
   - Our backend creates outbound attempt.
   - Redis worker submits the attempt to ElevenLabs batch calls.
   - ElevenLabs calls through DIDWW.
   - Candidate receives the call.
   - ElevenLabs webhook updates call/campaign record.

6. Handoff to 3CX
   - Confirm whether ElevenLabs transfer handles the full desired handoff.
   - Candidate hears hold message.
   - Officer hears AI handover context.
   - Candidate is bridged to officer.
   - Confirm whether ElevenLabs recording covers handoff or only AI part.
   - Confirm whether 3CX recording covers the human part.

7. DIDWW CDR/call events
   - Ask DIDWW support to enable Call Events/CDR webhook.
   - Store carrier call ID/status/duration/SIP status.
   - Use this for billing reconciliation and SIP debugging.

Required ElevenLabs/DIDWW details later:

```env
ELEVENLABS_API_KEY=
ELEVENLABS_WEBHOOK_SECRET=
ELEVENLABS_OUTBOUND_AGENT_ID=
ELEVENLABS_OUTBOUND_PHONE_NUMBER_ID=
ELEVENLABS_OUTBOUND_CONCURRENCY_LIMIT=1
```

Potential future fields:

```text
didww_call_id
didww_cdr_status
didww_duration
didww_sip_response_code
3cx_call_id
3cx_recording_url
elevenlabs_audio_url
```

## Blocked Helpdesk Items

### Zoho Desk Workflow/Webhook Configuration

Status: blocked — our Zoho Desk account lacks admin privileges ("Insufficient
Privileges" on Setup → Automation → Workflows). Needs the Zoho Desk admin.

What is already working:

- Zoho OAuth (client, refresh token) and Desk API access.
- Org ID, department ID discovered and configured.
- Local `HelpdeskTicketMirror` with idempotent upsert.
- Backfill script mirrors real tickets.
- Webhook endpoint `POST /helpdesk/webhooks/zoho/ticket` built and tested
  end to end via curl + ngrok (token auth, full-ticket fetch, upsert).

Still blocked (needs admin):

1. Workflow rule on Ticket Create/Update that POSTs
   `{"ticketId": "<ticket id>"}` to our endpoint with the
   `X-Webhook-Token` header (or `?token=` query param).
2. Alternatively: grant our profile Automation/Workflow permissions so we
   can create and iterate on the rule ourselves.

Interim: a scheduled polling sync (RQ) keeps the mirror fresh without
webhooks, and stays afterwards as the reconciliation fallback.

## Helpdesk Understanding

The Helpdesk is the written support system for candidates. It is not the same thing as the Calling Agent.

The Helpdesk handles:

- WhatsApp enquiries.
- Email enquiries.
- Zoho Desk tickets.
- Human assignment and escalation.
- AI answers or AI drafts using a Helpdesk-specific knowledge base.

The Calling Agent handles:

- Inbound exam-time voice calls.
- Outbound survey campaign calls.
- Call transcripts and call records.
- Survey follow-up through voice.

Important separation rules:

- A voice call should not automatically become a Zoho ticket.
- A WhatsApp/email support conversation should not become a voice-call thread.
- The Helpdesk has its own knowledge base.
- The Calling Agent has its own knowledge base.
- The Helpdesk system of record is Zoho Desk.
- The Calling Agent system of record is our call/campaign database.

## Helpdesk Target Architecture

```text
Candidate sends WhatsApp message
        |
        v
WhatsApp Business Platform / BSP
        |
        v
Zoho Desk channel integration
        |
        v
Zoho Desk ticket
        |
        v
Helpdesk AI reads ticket + retrieves from Helpdesk KB
        |
        +--> confident + in-KB + safe -> reply to candidate
        |
        +--> in-KB but uncertain/sensitive -> draft for officer
        |
        +--> out-of-KB / complaint / result dispute -> assign to officer
```

Email is similar:

```text
Candidate sends email
        |
        v
Zoho Desk email channel
        |
        v
Zoho Desk ticket
        |
        v
Helpdesk AI reads ticket + retrieves from Helpdesk KB
        |
        +--> auto-reply later, only after confidence is proven
        |
        +--> draft-for-human at launch
        |
        +--> route-to-human for sensitive/out-of-scope issues
```

## WhatsApp vs Email Behaviour

### WhatsApp

WhatsApp is real-time and candidate-facing. It should have two outcomes:

1. Answer
   - Only when the issue is clearly in the Helpdesk KB.
   - Only when the answer is low-risk.
   - Only when the conversation is inside the allowed WhatsApp service window or an approved template is being used.

2. Route to human
   - When the question is out of scope.
   - When the candidate is angry.
   - When the topic is sensitive.
   - When the AI is uncertain.
   - When the candidate asks for a person.

No draft step for WhatsApp in the same way as email, because WhatsApp is expected to be conversational. A human can still take over inside Zoho Desk.

### Email

Email is less real-time, so it can safely have three outcomes:

1. Auto-reply
   - Later phase only.
   - Only for proven, repeatable question types.
   - Only when grounded and high confidence.

2. Draft for human approval
   - Recommended launch mode.
   - AI writes the suggested reply.
   - Officer reviews, edits, and sends.

3. Route to human
   - Out-of-KB.
   - Sensitive.
   - Complaint/dispute.
   - Negative sentiment.
   - Candidate asks for escalation.

## Helpdesk Knowledge Base

This must be separate from the Calling Agent KB.

Helpdesk KB should include:

- General FOT support.
- General Scholastica support.
- Login and account access guidance.
- Test invitation/email guidance.
- Assessment link issues.
- Browser/device guidance.
- Test timing rules.
- Common candidate FAQs.
- Approved escalation language.
- What not to answer.
- Human handoff rules.

The KB should be tagged by:

- tool name, for example `FOT`, `Scholastica`
- campaign/test name where known
- issue category
- sensitivity level
- allowed channel, if some answers differ by email vs WhatsApp

Examples of issue categories:

```text
login_issue
test_link_issue
deadline_question
browser_issue
result_question
reschedule_request
complaint
payment_issue
identity_verification
general_enquiry
```

Sensitive categories should route to human by default:

```text
result_dispute
eligibility_dispute
complaint
legal_or_privacy_request
abuse_or_threat
client_specific_exception
payment_or_refund
identity_mismatch
```

## Helpdesk Data We Should Store Locally

Zoho Desk remains the system of record for tickets. Our backend should only store what helps with orchestration, audit, reporting, or AI decisions.

Suggested local models later:

### HelpdeskTicketMirror

Purpose: a lightweight local mirror of important Zoho ticket metadata.

Fields:

```text
id
zoho_ticket_id
channel: whatsapp | email
candidate_name
candidate_email
candidate_phone
tool_name
campaign_name
issue_category
sentiment
ai_disposition: auto_replied | drafted | routed_to_human | no_action
zoho_status
zoho_assignee_id
last_message_at
created_at
updated_at
```

### HelpdeskAIAction

Purpose: record what the AI decided and why.

Fields:

```text
id
zoho_ticket_id
message_id
action_type: auto_reply | draft_reply | route_to_human | tag_only
confidence_label: high | medium | low
grounding_status: grounded | partial | missing
sensitivity_detected: true | false
draft_text
sent_text
reason
created_at
```

### WhatsAppConsent

Purpose: track WhatsApp-specific consent separately from email/call consent.

Fields:

```text
id
candidate_phone
candidate_email
whatsapp_opted_in
whatsapp_opted_out
source
confirmed_at
opted_out_at
created_at
updated_at
```

## Helpdesk Build Plan

### Step 1: Zoho Desk Discovery

Goal: understand the Zoho Desk account and what is enabled.

Check:

- department IDs
- ticket fields
- ticket statuses
- assignment rules
- tags
- email channel configuration
- WhatsApp channel availability
- API access/OAuth setup
- whether Zia is available and usable
- whether we will use Zia or our own AI layer

Output:

- `ZOHO_ORG_ID`
- `ZOHO_DEPARTMENT_ID`
- OAuth client credentials
- refresh token or token flow
- chosen statuses/tags
- list of custom fields we need

### Step 2: Zoho API Connector

Goal: our backend can talk to Zoho Desk.

Build:

- Zoho OAuth/token refresh helper.
- Zoho Desk client.
- List departments script.
- List tickets script.
- Get ticket details script.
- Add comment/draft/reply helper.
- Update ticket tags/custom fields.

Initial scripts:

```text
scripts/check_zoho_auth.py
scripts/check_zoho_departments.py
scripts/check_zoho_tickets.py
```

### Step 3: Ticket Mirror and Webhooks

Goal: when Zoho ticket events happen, our backend can receive and understand them.

Build:

- webhook endpoint for Zoho ticket events
- signature/auth validation if available
- local `HelpdeskTicketMirror`
- idempotent ticket upsert
- logging of raw webhook payloads during testing

Flow:

```text
Zoho ticket created/updated
        |
        v
Our webhook receives event
        |
        v
Fetch full ticket from Zoho if needed
        |
        v
Upsert HelpdeskTicketMirror
```

### Step 4: Helpdesk Tagging

Goal: tag tickets by tool, campaign, and issue category.

Build:

- controlled vocabulary for tools/campaigns
- classifier for issue category
- confidence/sensitivity detector
- Zoho tag update
- Zoho custom field update

At first this can be rule-based plus AI assistance. Later we can improve it.

### Step 5: Helpdesk AI Decision Engine

Goal: decide what to do with each ticket.

Decision output:

```json
{
  "action": "draft_reply",
  "tool_name": "FOT",
  "campaign_name": "Graduate Aptitude Test",
  "issue_category": "test_link_issue",
  "confidence_label": "medium",
  "grounding_status": "grounded",
  "sensitivity_detected": false,
  "reply_text": "..."
}
```

Decision rules:

- high confidence + grounded + low risk -> can answer
- medium confidence -> draft for email, route for WhatsApp
- missing grounding -> route to human
- sensitive topic -> route/draft, never auto-reply
- candidate asks for human -> route to human

Launch mode should be conservative:

- Email: draft-only first.
- WhatsApp: answer only for very safe FAQs; otherwise route.

### Step 6: Email Reply/Draft Flow

Goal: support the PSA's three-outcome email logic.

Build:

- create draft reply on Zoho ticket
- optionally auto-send for whitelisted categories later
- route to human by assignment/team/status
- store `HelpdeskAIAction`

Launch behaviour:

```text
Email ticket arrives
        |
        v
AI classifies + retrieves from Helpdesk KB
        |
        v
AI writes draft
        |
        v
Officer reviews and sends from Zoho Desk
```

### Step 7: WhatsApp Flow

Goal: support WhatsApp tickets once Meta/WhatsApp setup is approved.

Build after WhatsApp number is live:

- confirm WhatsApp messages create Zoho tickets
- detect 24-hour service window where applicable
- support approved templates for business-initiated messages if needed
- track WhatsApp opt-in/opt-out
- route unsafe/uncertain WhatsApp tickets to humans

Flow:

```text
Candidate sends WhatsApp
        |
        v
Zoho ticket created
        |
        v
Our backend evaluates ticket
        |
        +--> safe FAQ -> reply through Zoho
        |
        +--> unsafe/uncertain -> assign to officer
```

### Step 8: Human Escalation and SLA

Goal: make escalation operational, not just technical.

Define:

- which department/team receives candidate support tickets
- assignment rules by issue category
- priority rules
- SLA expectations
- tags for escalated tickets
- what status means "AI handled", "human needed", "waiting candidate", "resolved"

Suggested tags:

```text
ai_auto_replied
ai_draft_created
ai_routed_to_human
ai_low_confidence
sensitive_issue
candidate_angry
kb_gap
whatsapp_opt_out
```

### Step 9: Reporting

Goal: know if the Helpdesk automation is actually helping.

Metrics:

- total WhatsApp tickets
- total email tickets
- auto-replied count
- draft-created count
- routed-to-human count
- KB gap count
- average first response time
- average resolution time
- top issue categories
- tickets by tool/campaign
- negative sentiment count
- human override/edit rate

### Step 10: CSAT After Ticket Resolution

Goal: send CSAT after support resolution, not while the issue is still open.

Flow:

```text
Zoho ticket marked resolved/closed
        |
        v
Backend checks candidate email + opt-out
        |
        v
Send SurveyMonkey CSAT
        |
        v
Store CSAT invitation locally
        |
        v
Sync CSAT response later
```

This depends on SurveyMonkey access, so it is also blocked for now.

## Helpdesk External Setup Needed

### Zoho Desk

Needed from Zoho Desk:

- org ID
- department ID
- support email channel confirmation
- WhatsApp channel confirmation when ready
- OAuth client ID/secret
- refresh token/token flow
- ticket custom fields list
- ticket status list
- tag permissions
- webhook/event configuration
- whether Zia is enabled

### WhatsApp / Meta

Already in progress.

Needed later:

- Meta Business verification approval
- WhatsApp Business Account ID
- phone number ID
- display phone number
- permanent access token or system user token
- webhook verify token
- app secret
- approved message templates, if business-initiated messages are needed
- confirmation that messages flow into Zoho Desk

## First Helpdesk Implementation Slice

Start with Zoho Desk before WhatsApp is fully ready.

Recommended first build:

1. Add Zoho settings to `.env`.
2. Build Zoho auth/token helper.
3. Build Zoho Desk client.
4. Add scripts to list departments and fetch tickets.
5. Add local ticket mirror model.
6. Add webhook endpoint for Zoho ticket events.
7. Add simple tag/classification service.
8. Add draft-reply flow for email tickets.

Do not start with full WhatsApp automation until:

- WhatsApp number is approved.
- Zoho receives WhatsApp tickets correctly.
- Consent rules are clear.
- The 24-hour window/template behaviour is confirmed.

## Current Next Move

Since SurveyMonkey and telephony are blocked, the next useful work is:

```text
Zoho Desk connector
-> ticket mirror
-> email draft-assist flow
-> Zoho webhook ingestion
-> WhatsApp flow after Meta approval
```

