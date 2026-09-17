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

### SurveyMonkey Campaign Survey Status

Status: functionally complete for this phase. SurveyMonkey is no longer a
backend blocker.

What is working:

- SurveyMonkey API token and scopes are working.
- The backend can list SurveyMonkey template surveys.
- The backend can clone a selected template into a campaign-specific survey.
- Template placeholders such as `{{campaign_name}}`, `{{campaign_title}}`,
  `{{assessment_name}}`, and `{{tool_name}}` are interpolated during cloning.
- The backend can create an email collector and invitation message.
- The backend can prepare campaign candidates as SurveyMonkey recipients.
- The backend can send the SurveyMonkey invitation after explicit confirmation.
- SurveyMonkey owns the email body/layout by default; our backend only sets
  the subject unless a custom body is intentionally supplied.
- The backend can bulk-fetch collector recipients and survey responses.
- The backend can sync candidates to `responded`, `partial_response`, or
  `non_responder`.
- The completed real test invite synced successfully:
  - survey `423001176`
  - collector `440321814`
  - email `paul@dragnet-solutions.com`
  - response status `completed`
- Slow SurveyMonkey sync runs through Redis/RQ.
- Duplicate sync jobs are prevented with a Redis job lock.
- SurveyMonkey test-send and completed-sync check scripts exist.

Remaining SurveyMonkey work is product/ops polish, not a blocked backend item:

1. Finalize approved SurveyMonkey templates
   - Keep template survey names prefixed with `TEMPLATE -`.
   - Confirm the final campaign feedback template questions.
   - Confirm the final CSAT template questions.
   - Keep real/live campaign surveys separate from template surveys.

2. Decide subject-line defaults
   - Example campaign subject: `Share your assessment experience`.
   - Example CSAT subject: `How was your support experience?`.
   - The backend can set subject; SurveyMonkey should keep the email body
     design/layout.

3. Build officer-facing UI later
   - Create campaign.
   - Upload/import candidates.
   - Select a SurveyMonkey template.
   - Create the campaign survey.
   - Prepare recipients.
   - Confirm and send.
   - View sync status and non-responders.

4. Optional SurveyMonkey webhook setup
   - Register webhook for survey response events if we want near-real-time
     updates.
   - Verify webhook signature/secret.
   - Keep bulk sync as the reconciliation fallback.

5. CSAT survey flow
   - Use the configured CSAT template.
   - Send CSAT after eligible inbound/support calls or resolved helpdesk
     tickets.
   - Link CSAT response back to the call record or ticket record.

Current useful SurveyMonkey env values:

```env
SURVEYMONKEY_ACCESS_TOKEN=
SURVEYMONKEY_CLIENT_ID=
SURVEYMONKEY_CLIENT_SECRET=
SURVEYMONKEY_CAMPAIGN_TEMPLATE_SURVEY_ID=
SURVEYMONKEY_CSAT_TEMPLATE_SURVEY_ID=
SURVEYMONKEY_WEBHOOK_SECRET=
```

SurveyMonkey scopes currently needed:

- View Surveys
- Create/Modify Surveys
- View Collectors
- Create/Modify Collectors
- View Contacts
- Create/Modify Contacts
- View Responses
- View Response Details
- View Webhooks and Create/Modify Webhooks only if we add webhooks.

### CSAT Contact Resolution (phone -> email) — Parked

Status: parked on a main-app integration point, not a technical blocker in
this service. Everything else in the post-call CSAT loop is built and tested
(invitation model, eligibility, idempotent creation, batched send, response
sync with score/comment extraction, gated `CSAT_AUTO_CREATE` auto-trigger on
the inbound webhook, RQ jobs, manual API). A live test CSAT was sent and
received (`paul@dragnet-solutions.com`, survey `423246877`,
collector `440571940`).

The one gap: to send a CSAT after an inbound support call we need the
candidate's **email**, but an inbound call only reliably gives us their
**phone**. Today `create_csat_invitation_for_call` resolves phone -> email by
looking up `CampaignCandidate` (our own outbound-campaign table) as a stand-in
profile source. That works only when the caller happens to also be in a
campaign; otherwise the invitation is created as `pending_contact_lookup` and
never sends.

What unblocks it: a real candidate-profile lookup owned by the main Dragnet
application (the system that actually knows every candidate's phone + email),
exposed to this service as either:

- a small internal "resolve contact by phone" API endpoint we call, or
- a shared/replicated profile table this service can read.

When that exists, swap the body of `_candidate_by_phone` (in
`app/services/csat_service.py`) to hit it — a one-function change; the rest of
the CSAT loop (status transitions, send, sync) already handles a resolved
email vs. `pending_contact_lookup` correctly. No other code changes needed.

Interim: `pending_contact_lookup` invitations can be resolved manually (create
with an explicit `email=`), which is exactly how the live test was sent.

### Telephony + ElevenLabs Blocked Items

Status: blocked until the live SIP/telephony path and ElevenLabs phone setup
are configured.

**Current blocker (2026-09-03): ElevenLabs requires an Enterprise plan for a
static IP.** 3CX needs a static IP to allowlist for the SIP trunk, and
ElevenLabs only offers a static IP on its Enterprise tier. This is the one
thing stopping outbound calls from going live — everything else on our side
is built and tested (see below). Needs an org decision: upgrade to ElevenLabs
Enterprise, or find an alternative that gets 3CX a stable IP to allowlist
without it (e.g. routing through DIDWW instead for the SIP leg, or a
relay/proxy with a fixed egress IP in front of ElevenLabs). Whoever owns the
ElevenLabs account/billing relationship should weigh in.

Decision still needed: use 3CX as the SIP endpoint, use DIDWW as the Nigerian
DID/SIP provider, or use DIDWW for the public Nigerian number while routing
handoff/officer calls through 3CX.

Current options:

1. 3CX-first path
   - Use the existing self-hosted 3CX as the SIP endpoint.
   - Open/allow the required SIP/RTP ports on the 3CX host.
   - Firewall allowlist ElevenLabs SIP origination/termination IPs.
   - Connect ElevenLabs to 3CX as a SIP trunk.
   - Route inbound calls from 3CX to the ElevenLabs agent.
   - Route outbound calls from ElevenLabs through 3CX.
   - Handoff is simpler because the AI leg and officer leg can stay inside
     the same phone system.

2. DIDWW-first path
   - Buy/configure the Nigerian DID.
   - Enable inbound SIP forwarding.
   - Enable outbound trunking for Nigerian local calls.
   - Connect DIDWW SIP services to ElevenLabs.
   - Use 3CX mainly for officer/handoff destinations and recordings.

3. Hybrid path
   - DIDWW owns the public Nigerian number and carrier routing.
   - ElevenLabs owns the AI agent/call automation.
   - 3CX owns internal officer extensions, handoff handling, and possibly
     human-leg recording.

The ElevenLabs-side items (agent IDs, phone number ID, webhooks, live call
tests) apply whichever telephony path we choose.

What is already working:

- Inbound ElevenLabs post-call webhook ingestion.
- Call record storage.
- Handoff metadata tracking.
- Outbound call attempts and retry queue.
- Redis/RQ job plumbing.
- Outbound execution path wired to ElevenLabs batch calls.
- Scheduled campaign orchestration (built 2026-08-16): a scheduler
  (`scripts/run_campaign_scheduler.py` + `run_campaign_orchestration_job`)
  advances every active campaign through its lifecycle on a cron —
  survey_sent/waiting_for_responses → (wait window) → sync responses →
  outbound_ready → build call queue → outbound_calling → drain calls (capped by
  `campaign_outbound_concurrency`, default 1) → completed. The decision logic
  (`campaign_orchestration_service.decide_campaign_next_step`) reads each
  campaign's own `response_wait_hours`. Everything up to "queue built" runs
  today; the `drain_calls` step only places real calls once telephony is live.
  The campaign summary UI shows an "Automation" line describing the next
  scheduled step. To activate: run the scheduler alongside the RQ worker.
- Outbound agent's voice tools, built and tested (2026-08-28 to 2026-09-01):
  `/voice-agent/kb/query` (Q&A, campaign-scoped), `/voice-agent/outbound/answer`
  (records each confirmation the candidate gives, idempotent per question),
  `/voice-agent/outbound/opt-out` (terminal — suppresses the candidate across
  every campaign, not just the current one). Campaigns now carry the outbound
  call-context fields the outreach script needs (`call_reason`,
  `organization_name`, `assessment_at`, `assessment_location`,
  `practice_test_url`, `contact_info`), settable at creation and editable on
  the campaign's Outbound tab; they reach the agent as dynamic variables,
  including a server-computed `time_of_day_greeting` so the opening line is
  never left to the model's guess.
- Durability hardening on the outbound path: atomic claim (no double-dial
  under concurrent workers), a unique DB constraint against duplicate
  attempts, bounded auto-retry, at-most-once survey/CSAT sends, and the
  stale-call sweep now runs every orchestration tick.
- Full ElevenLabs agent configuration handed over (system prompt, first
  message, all three tools' exact request/response shapes, dynamic variable
  list, phone-number/webhook notes) — nothing left to design, only to paste
  into the dashboard once telephony unblocks.

Still blocked:

1. Telephony route decision
   - Decide whether production uses 3CX-first, DIDWW-first, or hybrid.
   - Confirm who owns firewall/SIP changes.
   - Confirm who owns call recording for AI leg and human handoff leg.

2. 3CX SIP configuration, if using 3CX
   - Open/allow required SIP/RTP traffic.
   - Configure ElevenLabs SIP trunk/extension routing.
   - Confirm inbound routing from candidate-facing number to ElevenLabs.
   - Confirm outbound routing from ElevenLabs to Nigerian candidates.
   - Confirm handoff routing to officer extensions/queues.

3. DIDWW configuration, if using DIDWW
   - Buy/configure the Nigerian DID.
   - Confirm inbound channels.
   - Enable inbound SIP forwarding.
   - Enable outbound trunking.
   - Confirm Nigerian local routing.
   - Confirm outbound concurrency.
   - Confirm caller ID presentation.
   - Ask DIDWW support to enable Call Events/CDR webhook if we use DIDWW.

4. ElevenLabs telephony setup
   - Connect the chosen SIP path to ElevenLabs.
   - Confirm the ElevenLabs phone number ID.
   - Confirm the outbound agent ID.
   - Configure the inbound support agent and outbound survey agent separately.

5. Live inbound call test
   - Candidate calls the Nigerian/candidate-facing number.
   - Telephony provider routes to ElevenLabs.
   - ElevenLabs agent answers.
   - Webhook lands in our backend.
   - Call record is created.

6. Live outbound call test
   - Our backend creates outbound attempt.
   - Redis worker submits the attempt to ElevenLabs batch calls.
   - ElevenLabs calls through the chosen SIP/telephony path.
   - Candidate receives the call.
   - ElevenLabs webhook updates call/campaign record.

7. Handoff to 3CX/officer
   - Confirm whether ElevenLabs transfer handles the full desired handoff.
   - Candidate hears hold message.
   - Officer hears AI handover context.
   - Candidate is bridged to officer.
   - Confirm whether ElevenLabs recording covers handoff or only AI part.
   - Confirm whether 3CX recording covers the human part.

8. Carrier/PBX call events
   - Store carrier/PBX call ID/status/duration/SIP status where available.
   - Use this for billing reconciliation and SIP debugging.

Required ElevenLabs/telephony details later:

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

### Zoho Integration Account — Identity/Permissions Decision Needed

Status: blocked on a team decision, not a technical blocker. For discussion
with Olumide/IT before choosing a path.

The problem: our API integration (`ZOHO_CLIENT_ID`/`ZOHO_CLIENT_SECRET`/
`ZOHO_REFRESH_TOKEN` in `.env`) was created under `tech@dragnet-solutions.com`.
Checking Zoho's own role/profile data (`scripts/check_zoho_agents.py`) shows:

```text
tech@dragnet-solutions.com        -> profile: Light Agent   (restricted tier)
chizoba / elizabeth / fisayo      -> profile: Agent
janet / olumide                   -> profile: Support Administrator (full admin)
```

`tech@` being a Light Agent explains the "Insufficient Privileges" error on
Setup → Automation → Workflows, and is a real risk for the parts of our
pipeline that write to Zoho (draft replies, tags, priority, assignment) —
those have never been tested for real yet (still gated off by
`HELPDESK_DRAFT_EXECUTE` / `HELPDESK_TAG_EXECUTE`), and Light Agent accounts
commonly cannot perform them at all.

Asked Olumide (2026-07-13) about upgrading `tech@` to a full Agent/Admin
profile: not straightforward — Zoho Desk licenses are per-seat, so upgrading
`tech@` would mean removing the license/role from an existing person (e.g.
Janet or Olumide himself). Olumide first offered his own personal login as a
stopgap; decided against that (would attribute every AI action to him
personally in Zoho's audit trail, and breaks if his password changes or he
leaves).

**Decision (2026-07-13): requested a new, dedicated Zoho seat/license
(`stella@dragnet-solutions.com`) for the AI integration instead** — a real
service identity, not a personal account. IT to confirm profile assigned.

Two separate permission needs to confirm with IT for `stella`, since one
license tier may not cover both:

1. **Agent profile** (same tier as Chizoba/Elizabeth/Fisayo) is enough for
   everything our API integration does: ticket reads, comments, draft
   replies, tags, priority, assignment. This is the minimum needed to flip
   `HELPDESK_DRAFT_EXECUTE`/`HELPDESK_TAG_EXECUTE` on safely.
2. **Support Administrator profile** is separately required for the
   Setup → Automation → Workflows page (the webhook rule) — and that page
   can only ever be operated by a human in a browser, regardless of
   `stella`'s profile. If `stella` is only Agent-tier, Olumide or Janet still
   need to personally do the one-time workflow rule setup; if IT gives
   `stella` Support Administrator too, `stella`'s login could eventually
   cover that as well.

Once `stella`'s credentials exist: re-run the Self Client → grant code →
refresh token exchange we did originally under `tech@`, and swap `.env`.
No code changes needed. Reads (ticket sync, classification) are unaffected
either way; this only matters for the write path.

**Update (2026-08-16): write path UNBLOCKED and verified — using janet as an
interim service identity.** `stella@` had not arrived, so we minted a refresh
token authorized by **janet.abodunrin@dragnet-solutions.com** (Support
Administrator) via her own Self Client, and swapped all three `ZOHO_*` values
in `.env` (client id/secret/refresh token). Verified live against the real
Zoho Desk:

- `check_zoho_auth.py` confirms the token authenticates as the Dragnet org.
- A private comment and an unsent draft reply were both created successfully
  on test ticket #96170 (`add_comment` + `create_draft_reply`), attributed to
  **Janet Abodunrin**. A Light Agent (`tech@`) would have been 403'd — so the
  write capability is now proven, not assumed.

**Identity decision — CONFIRMED (2026-08-16): janet's seat is the license we
will use, and `stella@` is no longer being pursued.** Crucially, janet's Zoho
account is a *social-media-only* seat — she does not use it to send candidate
email — so AI writes attributed to "Janet Abodunrin" won't collide with her
own manual replies (there are none). That removes the main objection to using
a named account. The only residual caveat is ordinary credential hygiene: if
janet's password is rotated or the seat is reassigned, re-run the grant-code →
refresh-token swap and update `.env` (no code changes). This supersedes the
earlier `stella@` request above.

Remaining before candidate-facing go-live:

1. **`HELPDESK_DRAFT_EXECUTE` is still `false` on purpose.** The end-to-end
   live draft path is proven, but rollout is gated on the **officer heads-up**
   — officers have not yet been told AI drafts will start appearing in their
   reply boxes. When they have, flip `HELPDESK_DRAFT_EXECUTE=true` and restart
   the worker; that is the only remaining step for drafts. A pre-launch review
   of the dry-run drafts also caught and fixed a bounce-filter gap (a Gmail
   mail-daemon NDR was being drafted to — `is_system_bounce_notification` now
   catches `mailer-daemon`/`postmaster` senders on any domain and more NDR
   subject phrasings).
2. **Tag writing fixed (2026-08-16).** Verified against live Zoho that a ticket
   PATCH with a `tags` field is rejected 422 — the executor now writes tags via
   the dedicated `POST /tickets/{id}/associateTag` endpoint
   (`ZohoDeskClient.associate_tags`), with priority/assignee still via PATCH.
   `HELPDESK_TAG_EXECUTE` was dead before this fix; it is safe to enable now
   (independently of the draft flag). **It is now `true` in `.env` — tag
   writes to real Zoho tickets are live in production today.**
3. **Update (2026-09-15): the classify → draft path re-verified live against
   a real ticket**, on top of the 2026-08-16 proof, specifically to exercise
   the new tool-scoped grounding above. New script,
   `scripts/dry_run_one_ticket.py <zoho_ticket_id>` — runs one real ticket
   through `process_ticket` and prints the classification/decision/draft;
   refuses to run at all if `HELPDESK_DRAFT_EXECUTE`/
   `HELPDESK_WHATSAPP_AUTO_REPLY_EXECUTE` are on, so it can never
   accidentally send. Ran against ticket `1010551000054684573`
   (`tool_name=Scholastica`, campaign "JV Renaissance Scholarship"):
   classification and `tool_name` → `scholastica` scope resolution both
   correct, grounding came back `grounded`, and the generated draft matched
   house style (the "Dear [name], We warmly acknowledge receipt of your
   email." opener, no invented sign-off) without overstating anything the
   candidate isn't owed (declined to share shortlisting outcomes/timelines,
   correctly deferring to the client). Point 1 above (officer heads-up) is
   still the only gate before flipping `HELPDESK_DRAFT_EXECUTE` — this just
   adds a second, independent real-ticket proof to the first.
   Note for whoever runs this next: Zoho connectivity from a sandboxed
   session was intermittent this run (~1-in-3 to 1-in-10 attempts timed out
   before succeeding) — retry rather than assume it's broken.

### SharePoint Knowledge Base Connector

Status: Candidate Experience team created the site (2026-07-13):
`https://dragnetnigeria.sharepoint.com/sites/candidateexperience/...`,
existing FAQ content lives there in a document (not yet a clean table).
Content work can start now; the connector is blocked on an Azure AD app
registration.

What's already built: the KB pipeline (`app/services/helpdesk_kb_service.py`)
is source-agnostic — `load_kb_chunks()` currently reads local `kb/*.md`
files, but chunking/embedding/indexing downstream doesn't care where chunks
come from. Swapping to SharePoint is a new loader function only.

**Update (2026-09-04): the conversion pipeline this needed is now built and
tested, for the voice/campaign KB.** Real content in SharePoint arrives as
`.docx` files (not markdown text), so `app/services/docx_kb_loader.py`
converts one document's bytes into the same heading-per-question markdown
text the local loader produces — Word's "Heading 1"/"Heading 2" styles map to
`#`/`##`, exactly the standard already decided above. A heading used as a
bare section title (no answer under it) is dropped rather than indexed as a
near-empty junk entry, and reported as a warning naming the file and heading,
so whoever authored the SharePoint content can go fix it.
`app/services/sharepoint_kb_loader.py` lists a SharePoint folder's `.docx`
files via `sharepoint_client`, downloads and converts each one, and skips
(with a warning) anything that isn't `.docx` or fails to convert — one bad
file never blocks the rest of the folder. Wired into
`POST /campaigns/{id}/kb/reindex-from-sharepoint` (separate from the existing
local-directory `/kb/reindex`, so the caller explicitly picks which source
`kb_source` means — a local path and a SharePoint folder name can look
identical as a string).

**Update (2026-09-04): now wired into the Helpdesk KB too, under one unified
authoring rule for both systems.** The chunking algorithm itself
(`chunk_markdown`/`heading_of`/`slug`) was pulled into a shared
`app/services/markdown_kb_chunker.py` so both KBs use the exact same logic,
not two copies that could drift apart. `helpdesk_kb_service.
chunks_from_markdown` uses it to turn a converted `.docx` into this KB's
chunk shape, and `rebuild_kb_index_from_sharepoint(folder_path)` wires
`sharepoint_kb_loader` → `chunks_from_markdown` → the existing
`rebuild_kb_index` (now accepts an explicit `chunks` list, defaulting to the
local folder as before). Run via
`scripts/sync_helpdesk_kb_from_sharepoint.py "<folder name>"` — Helpdesk's KB
reindex has always been script-only (no HTTP route), so this matches that
existing pattern rather than the voice KB's per-campaign endpoint.

**Important: the SharePoint-authoring rule is now identical for both KBs —
Heading 1 or Heading 2 in Word, either is a real question, no title needed —
so an officer only ever learns one convention.** This is deliberately
*different* from each system's own pre-existing LOCAL `.md` file convention,
which stays untouched: `kb_voice/*.md` already has no title line (matches the
unified rule already), while `kb/*.md` uses "Heading 1 = document title
(skipped), Heading 2 = question" — that's internal dev/demo tooling, not
something officers touch, so changing it wasn't needed or done.

Still blocked on step 3 below (the per-site grant) before any of this can be
pointed at the real site, for either KB.

**Update (2026-09-09): real KB content reviewed against this standard, and a
third KB tier added — for the Calling Agent's voice KB specifically, not
Helpdesk's (the two stay separate by design, per this doc's own principle).**
The real documents (General Inquiry, Scholastica, FOT FAQ, Test Haven FAQ)
don't use Heading 1/2 today — they use plain bold-in-"Normal" paragraphs or
Word's "List Paragraph" style. Rather than build a tolerant multi-format
parser, the decision was to convert the existing content by hand into the
Heading-1/2 standard (the person maintaining the content owns the
conversion), so there's exactly one authoring rule going forward, not several.

This also surfaced that FOT/Test Haven/Scholastica content isn't
campaign-specific — it applies to every campaign using that tool, and a
candidate never states which tool they're on (they only mention their
campaign, e.g. "ExxonMobil"). So the voice KB now has three tiers instead of
two: general + tool (resolved silently from the campaign's own `tool_name`,
via `campaign_scope_service.campaign_tool_scope`) + campaign. `"Test Haven"`
is now a recognized tool name alongside FOT and Scholastica
(`app/core/vocabulary.py`). Tool-scoped content indexes through the same
`reindex_campaign` everything else uses, just keyed by the tool's scope tag
instead of a campaign id — no Campaign row exists for a tool, so this is
script-triggered (`scripts/reindex_voice_kb_tool.py` for a local directory,
`scripts/reindex_voice_kb_tool_from_sharepoint.py` for a SharePoint folder),
not a new HTTP route. Verified end-to-end with a real embedding (indexed a
sample Test Haven doc, queried it back correctly scoped alongside general
content, cleaned up after).

**Update (2026-09-15): Helpdesk KB now has the same 3-tier scoping, closing
the gap noted below.** `helpdesk_kb_service.retrieve_grounding` and
`rebuild_kb_index` now take `tool_scope`/`campaign_scope` the same way the
voice KB's `query`/`reindex_campaign` do. The classifier already extracted
`tool_name`/`campaign_name` from ticket text (`helpdesk_classifier.
TicketClassification`) — that was sitting unused; `helpdesk_ai_service.
_resolve_kb_scopes()` now turns it into scope tags (reusing
`campaign_scope_service.tool_name_to_scope`/`resolve_campaign` as-is,
no new resolver needed) before every grounding call. New script,
mirroring the voice one: `scripts/reindex_helpdesk_kb_tool_from_sharepoint.py`.

Caught one real bug while wiring this up: `rebuild_kb_index` used
`collection.add()`, which silently no-ops on an id that already exists
(no error, no overwrite) rather than replacing it — harmless once every
chunk carries a scope tag, but it meant the first re-sync after adding
scoping left the existing local dev chunks stuck without one, and they
silently stopped matching any scoped query. Fixed by switching to
`collection.upsert()`. 474 tests passing (up from 452).

~~Helpdesk's KB has no such tiering yet~~ — resolved above. Original note,
kept for history: `retrieve_grounding` queried its whole collection with no
scope filter at all; its own historical taxonomy note further down this doc
("The KB should be tagged by: tool name...") had already anticipated
needing this.

Content decision (revised 2026-07-13): do **not** require converting the
existing FAQ document into an Excel table. As long as each FAQ is its own
heading (Heading 1/2 in Word) with the answer below it, the loader can split
on headings exactly like the local `kb/*.md` files — much less rework than
re-authoring into rows. Only reformat further if the existing doc mixes
several topics into unstructured paragraphs (bad for retrieval regardless of
file format). No "Sensitive" flag is required in the KB content itself —
sensitive categories (complaints, payments, results, identity) are already
blocked from drafting upstream, by the classifier/decision layer, before the
KB is ever consulted (see `app/services/helpdesk_decision.py`).

Progress (2026-07-16), verified with `scripts/check_sharepoint_access.py`
(tests each permission layer in order and reports exactly which one fails):

1. ✅ Azure AD app registered by the user themselves: "Dragnet Candidate
   Experience KB Reader", client id `1092cc2d-6fcc-4fc4-9b19-99d1c2f819dc`.
   Client secret created; `KB_READER_TENANT_ID`/`KB_READER_CLIENT_ID`/
   `KB_READER_SECRET_VALUE` in `.env`. Token acquisition confirmed working.
2. ✅ Microsoft Graph `Sites.Selected` application permission requested and
   **admin-consented** by IT (confirmed: error moved from 401 → 403).
3. ⬜ **Still blocked**: the per-site grant — this app has not yet been
   explicitly given access to the candidateexperience site itself. This is
   a separate step from admin consent and needs someone with SharePoint
   Administrator (or Global Admin) rights to run two Graph API calls in
   Graph Explorer (GET the site id, then POST to
   `/sites/{id}/permissions` granting our app's client id `read` access).
   Full request bodies are in the chat history from 2026-07-16.

Once step 3 is done, rerun `scripts/check_sharepoint_access.py` — if it
passes, the connector can be pointed at the real FAQ document.

Interim/permanent plan either way: poll on a schedule (same RQ pattern as
the Zoho sync) — check the file's last-modified timestamp, and if changed,
rebuild the whole KB index (same `rebuild_kb_index()` we already have, just
fed by a different loader). No need for Graph webhooks/change
notifications; full-rebuild-on-change is simple and already proven with the
local KB folder.

**Update (2026-09-14): the site itself turned out to be wrong, and the
library was in a non-default document library — both fixed.** The real site
is `https://dragnetnigeria.sharepoint.com/sites/everybody`, library
"Candidate experience KB" — not the `/sites/candidateexperience` placeholder
this doc's earlier config defaulted to, and `.env` had never actually set
`SHAREPOINT_SITE_PATH` at all, so every call was silently resolving the
wrong site. Also, "Candidate experience KB" isn't the site's default
library — Graph's `/sites/{id}/drive` only ever reaches the default one, so
a library with its own name needs resolving to a drive id first.
`SharePointClient.get_drive_id()` added (looks it up via `/sites/{id}/drives`
by display name); `sharepoint_kb_loader.load_sharepoint_kb_folder()` and both
`rebuild_kb_index_from_sharepoint` functions take an optional `library_name`
now. `.env` updated: `SHAREPOINT_SITE_PATH=/sites/everybody`,
`SHAREPOINT_LIBRARY_NAME=Candidate experience KB`. New test file
`tests/test_sharepoint_client.py`.

**Update (2026-09-15): step 3 (the per-site Graph grant) is done — access is
live, verified end-to-end.** `get_site`/`get_drive_id`/`list_drive_items` all
succeed now. Real folder structure, as actually uploaded (one level deeper
than first suggested — everything lives under a `KB/` folder):

```text
KB/General       -> General Inquiry Response Template.docx
KB/FOT           -> Proctored Test FAQ Response Template (FOT).docx
KB/Test Haven    -> Proctored Test FAQ Response Template (Test Haven).docx
KB/Scholastica   -> SCHOLASTICA Inquiry Response Template.docx
KB/Helpdesk      -> Proctored Test FAQ Response Template (Email Script).docx
```

All 5 files present, matching what was converted (see the 2026-09-09 update
above for the conversion details and the two content judgment calls made
converting "General Inquiry"). Nothing has been indexed from these yet —
verification so far is read-only (listing the folders); running the actual
reindex scripts against them is the next step, not yet done. `KB/Helpdesk`'s
scope is still an open question — its content overlaps heavily with
`KB/FOT`/`KB/Test Haven` (same technical issues, reworded for an email
reply) rather than being its own distinct tool or general content.

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

**Update (2026-09-09): this logic is now built and tested**, ready for the
moment real WhatsApp tickets exist. `helpdesk_ai_service.process_ticket`
branches on `mirror.channel == "WhatsApp"`:
- **Answer** — reuses the exact same classify/ground/decide pipeline as
  email (so "out of scope," "sensitive," "uncertain" all route to a human
  identically to email — no separate rule set to maintain), but a grounded,
  answerable question is generated in a short, conversational tone
  (`helpdesk_draft_service.generate_whatsapp_reply`, distinct prompt from
  the email draft one) and recorded as `action_type: auto_reply` — sent
  immediately only if `HELPDESK_WHATSAPP_AUTO_REPLY_EXECUTE` is on (off by
  default, separate flag from the email draft one, more cautious since
  there's no human-review step to catch a bad answer before it goes out).
- **Route to human** — same shared backstops as email, PLUS a new
  WhatsApp-only one: `helpdesk_decision.detect_human_request` scans for the
  candidate explicitly asking for a person ("speak to a human," "connect me
  with an agent," etc.) and escalates immediately, before any KB
  retrieval/generation is attempted.
- **The "inside the service window / approved template" requirement is NOT
  implemented** — there's no way to check Meta's 24-hour messaging window
  from our side without a real WhatsApp connection to test against. Today
  the assumption is Zoho's own WhatsApp integration enforces or surfaces
  that constraint; this needs verifying once real WhatsApp tickets exist.
- **The actual send mechanism is unverified.**
  `ZohoDeskClient.send_whatsapp_reply` is a best-guess implementation (posts
  a public comment via `/tickets/{id}/comments`, since Zoho's dedicated
  email-reply endpoints are hardcoded to `channel: EMAIL` and can't be
  reused) — confirm this against Zoho's API docs or a live test WhatsApp
  conversation before ever turning the execute flag on.

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

**Update (2026-09-09): draft tone corrected against a real officer template.**
Reviewed the officer-authored `Proctored Test FAQ Response Template (Email
Script).docx` (140 real reply entries) and found our
`helpdesk_draft_service.DRAFT_SYSTEM_PROMPT` didn't match house style:
- Real replies open every answer with "Dear [Candidate/Name]," then, on its
  own line, "We warmly acknowledge receipt of your email." — our prompt had
  no such acknowledgment line at all. Added it.
- Real replies never sign off — checked the whole document for "Best/Kind/
  Warm regards", "Sincerely", "Candidate Experience Team", "Dragnet
  Solutions": zero occurrences. Our prompt forced a
  "Candidate Experience Team / Dragnet Solutions" sign-off. Removed it —
  drafts now end right after the answer, like the real templates do.
- Kept personalizing by first name when known (falling back to "Dear
  Candidate,") rather than matching the real template's inconsistent ~8-of-140
  "Dear Candidate" usage exactly — that inconsistency reads as the officers'
  own variance, not a deliberate rule, and personalizing when we can is
  strictly better.

The WhatsApp draft prompt (`WHATSAPP_SYSTEM_PROMPT`) was already
short/no-salutation/no-sign-off and needed no change.

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

SurveyMonkey access is now available, so this is no longer blocked by
SurveyMonkey. It is pending product timing: decide whether ticket CSAT belongs
in the helpdesk phase now or after the calling-agent telephony work is live.

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

Superseded by everything above — this list predates the Zoho connector,
mirror, draft flow, and webhook endpoint, all of which are now built and
live-verified. Kept for history; see the dated updates throughout this doc
for what's actually true as of when each was written.

**Update (2026-09-15) — actual current state:**

Blocked on someone external:
- SharePoint per-site Graph grant — **done**; content uploaded, not yet
  indexed (see the SharePoint section above for the real folder layout).
- Zoho workflow rule (instant webhook vs. the 5-min poll) — no longer
  blocked on permissions (Janet/Olumide already hold Support Administrator),
  just needs one of them to click it through in the Zoho UI.
- Outbound calling — ElevenLabs Enterprise/static IP, owned by the user.
- WhatsApp/Meta — business verification etc., already in progress.
- CSAT phone→email lookup — needs a real candidate-lookup API from the main
  Dragnet app; not buildable from inside this service.

Pending a decision only the user can make:
- Inbound identity verification (the real call script's "verify 2 data
  points" step) — the built AI has no stored data to look up in the first
  place, so whether this still matters is unresolved.
- Voice cloning — paused on an internal conversation with Dragnet.
- `HELPDESK_DRAFT_EXECUTE` — one real ticket's draft has now been read and
  judged good; flip when ready, or dry-run a few more first.

Untested still:
- WhatsApp send — the mechanism itself is unverified against a real
  WhatsApp ticket (email's equivalent path now has two real-ticket proofs).

Next concrete step once picked up again: run the actual SharePoint reindex
scripts against the real, now-accessible content (`scripts/
reindex_voice_kb_tool_from_sharepoint.py` / `reindex_helpdesk_kb_tool_from_
sharepoint.py` for FOT/Test Haven/Scholastica, the campaign-admin route /
`rebuild_kb_index_from_sharepoint` for General) — nothing from the 5
uploaded docs is indexed into either KB yet.

**Update (2026-09-15): call capacity reviewed for the line manager's 1,000-
caller scenario; three real changes made off the back of it, not just a
report.** Full write-up (scenario math, cost, infra findings) in the
delivered capacity report; summary of what actually changed in the system:

1. **Outbound pacing raised.** `campaign_outbound_concurrency` was `1`
   (comment literally said "keep at 1 until the telephony path proves it can
   handle more" — never revisited). Raised to `6`, leaving 4 of our
   ElevenLabs Creator plan's 10 concurrent-call slots free for real inbound
   candidates while a campaign runs (that pool is shared, not per-direction
   — confirmed from ElevenLabs' own docs). `ELEVENLABS_OUTBOUND_CONCURRENCY_
   LIMIT` in `.env` raised to match.
2. **Orchestration cadence shortened from every 10 minutes to every 1**
   (`campaign_orchestration_cron`). With a 2-3 minute average call, the old
   10-minute tick — not concurrency — was the real throughput ceiling: a
   slot freed by a finished call sat idle for up to ~9.5 more minutes
   waiting for the next tick to refill it. This was the single biggest lever
   found, bigger than the concurrency number itself.
3. **Inbound call queueing turned on for real**, via the ElevenLabs agent
   API (`platform_settings.queueing_config`, not `queueing` as ElevenLabs'
   own changelog copy implies — confirmed by reading the live agent config
   before patching it). `enabled: true`, 180s wait (default), verified via a
   follow-up GET after the PATCH. Also discovered while in there: burst
   pricing (`call_limits.bursting_enabled`) was already `true` on this
   agent — nobody had explicitly turned that on either; worth knowing it's
   not a fresh decision.
4. **`run_simple_worker.py` (RQ `SimpleWorker`, no forking, its own
   docstring already said "not for production") replaced as the recommended
   production worker** by new `scripts/run_worker.py` (RQ `Worker`, forks a
   process per job — a crashing job can't take the whole worker down).
   `run_simple_worker.py` is kept (unchanged) for local debugging or
   environments where forking isn't available; README's getting-started and
   production process list now both point to `run_worker.py`.

475 tests passing after all four changes — none needed a test update, since
these were config/script changes, not behavior changes to anything under
test.

**Update (2026-09-15): Friday go-live is full auto-reply, not draft-review —
new capability built.** The line manager's requirement ("it replies, unknowns
route to human") is a step beyond anything shipped so far: `create_draft_reply`
(the flag above) still waits for an officer to click send. Added a new,
separate, off-by-default setting `helpdesk_email_auto_reply_execute` —
mirrors `helpdesk_whatsapp_auto_reply_execute`'s shape exactly, and takes
priority over `helpdesk_draft_execute` if both are ever on at once. When set,
`process_ticket` calls `ZohoDeskClient.send_reply` (existing method, written
back in August, never wired in or called against real Zoho until now) instead
of `create_draft_reply`, and labels the action `auto_reply` same as WhatsApp.
Ungrounded questions are unaffected — they still escalate to `route_to_human`
before this branch is ever reached. New test file
`tests/test_helpdesk_email_auto_reply.py` (6 tests, including a same-shape
regression guard to WhatsApp's existing cross-channel test). 481 passing.

**Still needed before this can be trusted for real candidate traffic**: a
real send against a real Zoho test ticket — `send_reply` has never touched
Zoho's live API, unlike `create_draft_reply`. Also same-day audit found real
KB gaps against Dragnet's own public Zoho KB portal (`https://dragnetsolutions
.zohodesk.com/portal/en/kb/dragnet-solutions`) — most flagged topics turned
out to already be covered (a first pass compared against memory, not the
actual indexed file, and overclaimed); the three genuine gaps (CELPIP
registration, JAMB registration number errors, and the real "no rescheduling
provision" policy — the local demo file promising a reschedule link is
already dead, superseded by the real SharePoint reindex, so no live
misinformation risk, just a coverage gap) were added directly to `documents/
converted/General Inquiry Response Template.docx` and `...SCHOLASTICA
Inquiry Response Template.docx`, verified through the production loader
(12→14 and 20→21 questions respectively, zero warnings) — pending re-upload
to `KB/General` and `KB/Scholastica` on SharePoint and a reindex.

Production host: a separate VM, not this dev machine — deployment there is
the user's own action item, separate from everything built/tested locally
this session.

## 2026-09-16: production deployment + large-scale historical evaluation

Deployed to the production VM (`InterviewerAdmin`, Ubuntu 22.04, already
hosting `dragnet-gpu`/`gpu.idhub.ng`, `erecruiter-server`/`erecruiter-web`,
and unrelated Postgres/RabbitMQ/Apache/PHP-FPM services — extreme care taken
not to disturb any of them). Checked out to `/var/www/candidate-experience`,
Python 3.12 installed alongside the box's existing 3.10/3.11 (this repo's
pinned `numpy==2.5.1` needs 3.12+; installed additively via deadsnakes, never
touching the system default). Real bugs fixed along the way: pip silently
fell back to `--user` installs when venv creation failed (root cause: missing
`python3.10-venv`) — verified this never actually altered `dragnet-gpu`'s own
already-satisfied dependency pins before moving on; PM2 misdetected the bare
`uvicorn` script as a Node.js file (fixed by invoking `python -m uvicorn`
instead); `PYTHONPATH` needed setting explicitly per `pm2 start` invocation,
a shell `export` alone did not propagate to PM2's process registration; the
uvicorn target was `app.main:app` (copied from `dragnet-gpu`'s own pattern by
mistake) when this repo's entrypoint is actually root-level `main.py` — fixed
to `main:app`. All four processes (`ce-api`, `ce-worker`,
`ce-helpdesk-scheduler`, `ce-campaign-scheduler`) now run under PM2 with 0
restarts, `pm2 save`d so they survive a reboot via the box's existing
`pm2-azureuser.service`. KB reindexed on the VM across all four scopes
(General 18, FOT 46, Scholastica 21, Test Haven 51 chunks), verified live via
`retrieve_grounding`. `HELPDESK_EMAIL_AUTO_REPLY_EXECUTE` deliberately left
`False` — the actual go-live switch, to be flipped only when the user says so
(after the officer heads-up). Domain/Apache vhost/SSL and the Postgres+
pgvector migration both explicitly deferred post-Friday, by the user's own
choice — confirmed the Zoho polling pipeline (5-minute cron, outbound only)
needs no public URL, and the officer-facing React frontend isn't required
for the auto-reply path either (Zoho Desk remains officers' own interface for
anything routed to a human).

Ran the historical accuracy eval at real scale on the VM: 1000 new tickets
(1155 cumulative with the earlier 150-ticket dev-machine batch), stratified
across the full ~3,844-ticket untested backlog. Scores: factual alignment
4.04/5, hallucination 4.32/5, escalation correctness 3.94/5, tone 4.6/5.
Corrected an over-read of the raw "312 low scorers" number: 51 are the
already-known `availability_confirmation` harness artifact (production never
generates a reply for these), at least 37 more are cases where the real
historical officer only sent a generic "ticketed for review" ack with no
actual technical fix — directly verified one of these live and confirmed the
AI's answer was correctly grounded in real KB content, not hallucinated, it
simply didn't match a non-answer. Of 482 raw `kb_gap` candidates, the large
majority cluster around themes already fixed this morning (ticket-ID/
follow-up process, reschedule policy, outcome/next-stage contact) —
re-verified all of those live via `retrieve_grounding` post-reindex and
confirmed they're now correctly retrieved, so not new gaps.

**Genuinely new KB gaps found** (confirmed distinct from anything already
covered), backlogged for a post-Friday pass rather than rushed in now:
- Test-day logistics: calculators, plain paper for calculations, external
  keyboard/headphones, taking the test on Android/Chromebook/mobile
- Time/accommodation requests: test time extension for technical issues,
  schedule-conflict timing adjustments, remote/online interview for
  candidates outside Nigeria
- Account/profile: correcting Scholastica profile errors post-submission,
  changing Academic Referee info, deleting an account, using a next-of-kin's
  account for underage registration
- Process questions: confirming whether a test submission actually
  succeeded, whether cheating-prevention/fair-grading measures exist,
  whether original credentials are needed at screening
- Off-topic but recurring: candidates/others asking how to apply for a job
  *at* Dragnet Solutions itself (not a candidate assessment question), and
  business-partnership inquiries — both need a short redirect-style answer
  rather than silence

Full raw results saved locally at `/tmp/phase2_results_vm.json` (1155
entries) and `/tmp/phase2_results.json`/`/tmp/phase2_tested_ids.json` (the
150-ticket dev-machine batch + checkpoint) for the follow-up pass.

Also built an officer-facing review page for the 38 confirmed-genuine gaps
(clustered from the raw 482 `kb_gap` candidates by embedding similarity,
then each one live-verified against `retrieve_grounding` — 164 of 202
clusters turned out to already be covered and were dropped, not guessed
away) — https://claude.ai/artifact/Czjg67stzakVLckp9YPz2p — pending the
officer's approve/reject pass before anything gets added.

## 2026-09-17: real end-to-end email send test, two real bugs found and fixed

Added a scoped test allowlist, `helpdesk_email_auto_reply_test_emails` (comma-
separated), so a single real address can get the live auto-send experience
without opening auto-send to every candidate — the existing global flag
alone couldn't do this safely. Empty (default) = no restriction, same as
before this setting existed.

Sent real test emails end-to-end (external Gmail address, since our own
`@dragnet-solutions.com` addresses are correctly treated as suspicious/bounce
by `is_system_bounce_notification` — confirmed this is by design, not a bug,
after our first test using an internal address got silently suppressed).
Found two real, now-fixed issues this way:

1. **Zoho's own sentiment analysis can override a perfectly answerable
   question.** A neutrally-answerable "trouble logging in, how do I reset my
   password" message got flagged NEGATIVE by Zoho's sentiment engine (likely
   the words "not working"/"trouble"), which fired `negative_sentiment_backstop`
   and escalated to a human before the KB-answer path was ever considered.
   Confirmed via the real ticket's AI-action row. Not a bug — the rule is a
   deliberate, conservative safety net — but it means real auto-answer
   coverage will be measurably lower than KB-only testing suggested, purely
   from how candidates phrase things. No code change; documented so it isn't
   mistaken for a KB gap later.

2. **Outbound replies (both `send_reply` and `create_draft_reply`) sent with
   no subject line at all.** A real officer's reply to the same test carried
   `Re:[## 102799 ##] Unable to log in` — Zoho's own convention for
   threading a candidate's follow-up reply back into the same ticket. Ours
   had `"subject": null`, confirmed directly from the real sent thread's
   detail. Fixed: both client methods take an optional `subject`, and
   `helpdesk_ai_service._reply_subject_for(mirror)` builds
   `"Re:[## <ticket_number> ##] <subject>"` from the mirror's own synced
   fields. Re-verified live after the fix.

Also found and fixed a test-isolation bug this surfaced: with a real test
address left configured in the deployed VM's `.env`
(`HELPDESK_EMAIL_AUTO_REPLY_TEST_EMAILS`), 4 of the email-auto-reply tests
silently picked up that live value and failed, because they'd never been
isolated from the real environment. Added an autouse fixture that clears
the var by default; tests exercising the allowlist itself still set it
explicitly, which correctly overrides the fixture.

**Confirmed working, not a bug**: a real human officer picked up the
negative-sentiment-escalated test ticket and asked a clarifying question
("which assessment are you taking?") — something our AI cannot currently do.
Checked the actual code: `get_latest_candidate_message` only ever fetches
the single newest incoming message: no prior thread history (including an
officer's own earlier clarifying question) is passed to classification or
generation. `decide_ticket_action` has exactly two outcomes — answer from
the KB, or escalate — with no "ask for clarification" path. Explicitly
deferred past Friday, by choice: multi-turn conversations are rare relative
to first-contact questions, and the current failure mode is safe (escalates
rather than guesses wrong) rather than harmful. If revisited, the smallest
safe version is widening context to the last 2-3 thread messages (better
read comprehension of an existing reply) without adding the ability to ask
its own follow-up questions (a materially bigger feature: new action type,
new safety review, likely still human-in-the-loop given the trust step).
