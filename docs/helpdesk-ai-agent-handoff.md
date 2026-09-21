# Helpdesk AI Agent Handoff

Date: 2026-09-21  
Workspace: `/Users/kelpejol/dragnet/candidate-experience`  
Primary system area: Zoho Desk helpdesk automation for candidate email/WhatsApp support  
Related docs:

- `docs/helpdesk-conversation-architecture.md`
- `docs/helpdesk-conversation-rollout.md`
- `docs/blocked-items-and-helpdesk-plan.md`

This document is a verbose handoff for any AI agent or engineer continuing the
helpdesk work. It captures the thinking from this session: the intended
architecture, the conversational loop, the LangGraph direction, the knowledge
base assumptions, the historical Zoho Desk extraction, the CTO deliverables, and
the immediate next tasks.

## 1. What We Are Building

We are building a smarter helpdesk automation layer over Zoho Desk. Candidates
send support messages by email, WhatsApp, web, or chat. The AI must understand
the candidate's issue, determine the right context, retrieve the correct
approved knowledge, ask clarifying questions when needed, handle attachments
where possible, and either answer or escalate cleanly.

The key idea is that the system must not behave like a brittle one-pass
classifier pipeline. It must behave like a conversation-aware diagnostic loop.

A candidate may say:

> I cannot access my test.

That single sentence may refer to FOT, Test Haven, Scholastica, a recruitment
campaign, a scholarship process, an exam, an application portal, a browser
problem, an OTP problem, a secure browser problem, or some other context.

If the AI guesses wrongly, it will retrieve the wrong KB and answer wrongly.
Therefore the AI must resolve ambiguity before answering, unless the user's
message already provides enough context.

## 2. The Core Product Principle

The user explicitly wants this:

- Do not guess the tool, platform, campaign, institute, client, or issue when it
  is ambiguous.
- Ask clarifying questions when needed.
- Ask more than one or two clarification questions if those questions are still
  useful and are moving the diagnosis forward.
- Escalate when the conversation is no longer making progress, when the AI
  cannot understand the request well enough, when required knowledge is missing
  or conflicting, or when the case needs a human officer.
- Use the full conversation thread, not just the latest message.
- Preserve context across replies.
- Use screenshots or attachments when they help.
- Use Azure OCR for image/file text extraction once credentials are provided.
- Keep the knowledge base factual and approved.
- Keep style, tone, and email behaviour in prompts/configuration, not mixed into
  the factual KB.
- Use historical Zoho Desk communication to discover real recurring patterns,
  routing cues, and knowledge gaps, but do not publish raw historical answers
  directly without officer review and approval.

The system should feel intelligent: it should understand what evidence it has,
what evidence is missing, what question would reduce ambiguity, when a
screenshot would help, when a KB answer applies, and when it should stop and
handoff.

## 3. Loop, Not Pipeline

The preferred architecture is a LangGraph-style loop.

The old mental model is:

1. Receive message.
2. Classify tool/campaign/intent.
3. Retrieve KB.
4. Answer.

That is not good enough for this domain because the first message is often
ambiguous. Also, classifier labels can drift. For example, a candidate may say
"FOT test" but a classifier may return `Test Haven` if it is doing loose
semantic matching against internal labels or if the training/evaluation examples
are confused. Likewise a campaign name can be inferred wrongly if campaign names,
client names, and tool names are not clearly separated.

The intended model is:

1. Receive candidate event from Zoho Desk.
2. Load durable conversation state for that ticket.
3. Load the public thread history.
4. Extract any new evidence from the latest message and attachments.
5. Determine what is known:
   - candidate's issue
   - tool/platform
   - campaign/client/institute, if relevant
   - assessment stage
   - device/OS/browser, if relevant
   - error text/screenshot evidence
   - steps already tried
   - unanswered questions
6. Determine what remains ambiguous.
7. Decide the next useful action:
   - ask a clarifying question
   - ask for a screenshot/file
   - retrieve from KB
   - compose answer
   - escalate
8. If asking a question, persist state and wait for candidate reply.
9. If answering, validate that the answer is supported and applicable.
10. Send through the channel adapter with delivery idempotency.
11. If candidate replies again, resume the same loop.

The loop continues while useful progress is being made. There is no hard
"maximum two clarification questions" rule. The actual rule is:

- Ask another question if it has a clear purpose and is likely to reduce
  ambiguity.
- Do not repeat the same question.
- Do not keep asking when the candidate is not getting closer to resolution.
- Escalate when progress stalls or the AI lacks enough reliable context.

## 4. LangGraph Architecture Direction

The agreed architecture is a typed LangGraph StateGraph, not a collection of
loosely coordinated autonomous agents.

The graph should have focused nodes:

- Prepare/load evidence.
- Understand candidate need and current uncertainty.
- Resolve tool/campaign/context using approved evidence and cue registry.
- Choose next action.
- Extract attachment/OCR evidence.
- Retrieve approved KB articles.
- Compose answer or clarification.
- Review/validate answer.
- Deliver response or create handoff.
- Persist state and wait for the next candidate event.

The graph must be durable. It should persist:

- ticket ID
- processed event/message IDs
- public conversation summary
- recent candidate/officer messages
- extracted evidence
- candidate-provided tool/platform/campaign/client/institute
- possible entities and confidence/evidence
- unresolved questions
- questions already asked
- troubleshooting steps already suggested
- candidate responses to those steps
- attachments processed
- OCR outputs
- retrieved article IDs and versions
- answer validation result
- pending/delivered action state
- human ownership/handoff state
- schema version

The graph should release the worker while waiting for a candidate reply. A
candidate reply resumes a new graph invocation with the persisted state.

## 5. Current Runtime Implementation Status

The existing implementation is described in `docs/helpdesk-conversation-rollout.md`.
Important built items include:

- A real LangGraph StateGraph with prepare evidence, understand, choose,
  retrieve, compose, and review stages.
- Durable SQLite checkpoints.
- Separate namespaces for dry runs, drafts, email sends, and WhatsApp sends.
- Approved cue context.
- Exact tool evidence and typo/partial-name suggestions.
- Latest explicit corrections taking precedence.
- Clarification without a one/two-turn cap.
- Repetition detection and escalation when clarification is no longer useful.
- Screenshot requests and attachment OCR context.
- Tool-scoped retrieval with general content allowed alongside it.
- Technical issue answers requiring matching tool content.
- No requirement for campaign KB in this flow.
- Structured answer checks for support, applicability, unresolved requests, and
  repeated failed fixes.
- Public conversation context, with draft/private/unknown messages excluded.
- Zoho thread pagination and UTC ordering.
- Durable per-candidate-message delivery receipts.
- Delivery intent committed before send.
- Unknown send outcomes held for human reconciliation.
- Recipient, ownership, ticket status, and newest-message checks immediately
  before delivery.
- Human officer replies pausing automation.
- Review dashboard categories for clarification and attachment requests.

Important flags/configuration:

```dotenv
HELPDESK_CONVERSATION_ENABLED=false
HELPDESK_CHECKPOINT_PATH=./data/helpdesk-checkpoints.sqlite
HELPDESK_CUE_REGISTRY_PATH=./config/helpdesk-cues.json
HELPDESK_CONVERSATION_CONTEXT_MESSAGES=20
HELPDESK_AUTOMATION_ASSIGNEE_IDS=
```

The graph is implemented behind a disabled feature flag. It must be verified in
shadow/test mode before live production enablement.

## 6. Knowledge Base Direction

The current SharePoint layout reported earlier is:

| Folder | Document |
| --- | --- |
| `KB/General` | `General Inquiry Response Template.docx` |
| `KB/FOT` | `Proctored Test FAQ Response Template (FOT).docx` |
| `KB/Test Haven` | `Proctored Test FAQ Response Template (Test Haven).docx` |
| `KB/Scholastica` | `SCHOLASTICA Inquiry Response Template.docx` |
| `KB/Helpdesk` | `Proctored Test FAQ Response Template (Email Script).docx` |

The user clarified an important product intent:

- The Helpdesk folder in SharePoint appears to be more of an email-script/helpdesk
  wording source than a true tool-specific factual KB.
- The actual candidate answer knowledge should primarily live under tool-level
  KBs such as FOT, Test Haven, Scholastica, and General.
- Behavioural style, acknowledgement wording, email format, and how to speak to
  candidates should be in prompts/configuration, not in the factual KB.
- If the Helpdesk SharePoint document contains factual troubleshooting knowledge
  missing from tool folders, that knowledge should be reconciled into the
  appropriate tool/general KB after review.
- Do not blindly copy the Helpdesk document into all tool folders.
- Do not delete/retire Helpdesk as a source until live coverage is verified.

The current desired KB model:

- One canonical approved article corpus.
- Articles can apply to one or more tools.
- Email and voice should eventually retrieve from the same approved knowledge,
  even if they render answers differently.
- Campaigns mainly help identify the relevant tool/context. They do not imply
  that we need campaign-specific KBs unless the business actually maintains
  campaign-specific facts.
- Campaign/client/institute names can be routing cues, not necessarily KB scopes.

Each article should have:

- stable article ID
- title
- question variants
- answer steps
- supported tools
- applicability conditions
- assessment stage
- OS/browser/device constraints where relevant
- source document/version
- source folder
- reviewer
- approval timestamp
- lifecycle status
- effective dates
- expiry/review dates

## 7. Tool, Campaign, and Cue Registry

The user strongly emphasized that the AI should not infer mappings like:

- "exam" always means FOT
- "test" always means Test Haven
- a campaign name always means a specific tool
- a client abbreviation always maps to a campaign without confirmation

Instead, there should be a cue registry supplied as context to the AI.

Cue sources:

- officer-provided cues
- approved mappings from current operations
- historical Zoho Desk examples
- campaign/tool metadata from internal systems
- SharePoint KB terminology

Cue examples may include:

- tool aliases
- misspellings
- partial names
- URLs/domains
- client names
- institute names
- exam names
- campaign labels
- abbreviations
- error phrases
- secure-browser phrases
- stage-specific language

But every cue should be treated according to its quality:

- exclusive cue: safely identifies a tool/context
- shared cue: suggests possibilities but does not decide
- conditional cue: applies only under some date/stage/client/campaign condition
- weak cue: useful only for clarification options
- negative cue: prevents wrong mapping

Example behaviour:

- Candidate says "I cannot log in."
  - Ask what platform/assessment/campaign they are trying to access, unless
    other thread context resolves it.
- Candidate says "I cannot access my FOT test."
  - Treat FOT as strong explicit tool evidence if FOT is an approved tool.
- Candidate says "I have issue with my exam."
  - Do not infer FOT automatically. Ask for the platform or provide easy options.
- Candidate says a misspelled client/campaign name.
  - Use fuzzy cue matching to ask "Do you mean X?" instead of silently assuming.

The AI should receive a compact context pack, not thousands of historical
messages:

- approved tools
- approved aliases
- possible client/campaign mappings
- cue confidence/type
- candidate alternatives
- clarification examples
- current known evidence

## 8. Attachment, File, Screenshot, and OCR Direction

The user wants the AI to be able to attend to files and images. The first
implementation path is Azure OCR.

Desired behaviour:

- Candidate sends screenshot/image/file.
- System stores attachment metadata.
- System runs OCR where possible.
- OCR text is added as evidence.
- AI can reason from the extracted text.
- If visual layout matters, OCR alone may not be enough; a vision-capable model
  may be needed later.
- If the screenshot would help, the AI can ask the candidate to send one.
- If OCR fails or the attachment is unreadable, the AI should say it cannot read
  the file clearly and either ask for a clearer screenshot/text or escalate.

Example:

Candidate says they are stuck. Screenshot shows a "Forgot password" link or an
error. The AI should use that evidence plus KB guidance to say something like:

- click "Forgot password"
- follow the reset instructions
- return if a specific error appears

But the AI must not fabricate what it saw. If OCR/vision did not extract it, it
should not claim to see it.

The user said they will provide Azure OCR environment variables. Until then,
OCR integration can be prepared but not fully verified.

## 9. Issues and Edge Cases Already Identified

The session covered many edge cases across phases. The important current ones:

### 9.1 Ambiguous User Messages

Many high-volume candidate messages are vague:

- "I cannot login."
- "My test is not opening."
- "I cannot access my exam."
- "I cannot submit."
- "My OTP is not coming."
- "I was logged out."
- "The page is blank."

These must not be answered from a random KB. The AI should resolve context first.

### 9.2 Wrong Classifier Scope

The user questioned why a classifier would return a different `tool_name` than
what the candidate said, such as returning Test Haven when the candidate says
FOT. The likely failure mode is that the classifier is doing loose semantic
matching to a registry or label set, or it is overgeneralizing from examples.

The architecture fix is:

- classifier output is evidence, not authority
- explicit user text beats model guess
- approved cue registry beats fuzzy inference
- unresolved conflict triggers clarification
- KB scope is selected only from resolved context

### 9.3 Campaign vs Tool vs Context

Campaign is not the same thing as tool. Platform is not the whole context.

The full context includes:

- who is asking
- what they are trying to do
- service/product area
- tool/platform
- campaign/client/institute, if relevant
- assessment stage
- what is failing
- where it surfaces
- any error text
- any screenshot evidence

The CTO also emphasized this: mapping themes to context is what drives
disambiguation and KB routing.

### 9.4 Helpdesk KB vs Tool KB

The Helpdesk SharePoint folder may contain email-script wording rather than the
main source of factual answers. The user wants the factual KB to be tool-level
where appropriate. Helpdesk wording should become behaviour/prompt guidance if
it is about how to reply, not what the factual answer is.

### 9.5 Historical Officer Replies Are Not Automatically KB

Officer replies may be useful, but they may also include:

- one-off manual actions
- non-solutions
- unsupported claims
- outdated instructions
- private context
- backend state assertions
- security-sensitive information
- partial answers
- answers that depend on a specific campaign or date

Therefore historical Q&A should generate proposals for officer review, not
direct KB updates.

### 9.6 Full Thread Matters

Candidate emails may be part of a long thread. There can be multiple candidate
messages and multiple officer replies. The system must know the difference
between:

- candidate messages
- officer/public support replies
- Zoho automated replies
- draft/private/internal notes
- system messages
- quoted email chains

The AI should not confuse an automated Zoho receipt with an officer answer.
It should reconstruct the public support conversation and preserve thread
history.

### 9.7 Delivery Safety

Before sending, the system should check:

- the candidate is still the latest public sender if required
- no officer has taken over
- ticket status still allows automation
- recipient is correct
- message has not already been answered
- there is a durable delivery receipt

Unknown sends should not be blindly retried because that can duplicate replies.

### 9.8 Repeated Candidate Failure

If a candidate says "still not working," the system must not repeat the same
KB steps. It should remember what was already suggested and ask for the next
diagnostic evidence or escalate.

### 9.9 Screenshot/Image Issues

Screenshots can resolve ambiguity quickly, but:

- OCR may fail
- image may be blurry
- image may show personal information
- image may reveal backend-sensitive data
- AI may need to ask for text if OCR is insufficient

### 9.10 Security and Secrets

The extraction found cases flagged as `asks_for_secret` and
`security_sensitive_instruction`. The AI should never ask for passwords, OTPs,
or sensitive credentials. It can ask for non-secret identifiers only if policy
allows and if needed.

## 10. CTO Request, Verbatim

The CTO asked for the following deliverables. This should be treated as the
evidence package expected for Phase 1 of the data-driven helpdesk design.

```text
Start with the data, find the patterns, then design around them.

Here's how I'd structure that first phase.

Goal of Phase 1: Extract the top recurring questions and themes from the 3,000 historical emails, so you design around the known before the unknown.

Step 1: Normalize the emails.
Strip signatures, disclaimers, quoted chains, and footers. Keep subject, body, sender type, and date.

Step 2: Classify and cluster.
Use an LLM or embeddings plus clustering to group emails by intent. Then manually review the top clusters and name them.

Step 3: Rank by frequency.
Produce a list of the top 10, 20, 30 recurring question themes. Likely candidates based on what you described:

· Login issues (by platform: recruitment, exam, scholarship, ID verification)
· OTP not received or expired
· Locked out of an exam
· Exam scheduling or rescheduling
· Application status or submission problems
· Payment or refund issues
· Results or certificate access
· Client campaign setup or changes
· Job requests with no active campaign
· General info@ enquiries
· Personal or colleague outreach

Step 4: Map each theme to its source.
For each top theme, tag which product, service, platform, or audience it belongs to. This is what tells you how to disambiguate.

Step 5: Quantify ambiguity.
For each theme, ask: does this question usually need clarification before it can be answered? Login is the classic one. Note the disambiguation questions that would resolve it.

Step 6: Design the KB around the top themes.
Build the demarcated knowledge bases from these themes, not from an abstract org chart. The KB structure should mirror how people actually ask.

Step 7: Define the disambiguation logic per theme.
For each high-volume ambiguous theme, write the clarifying questions and routing rules. Example: Login to which platform, and which institute or client?

Step 8: Then build the pipeline.
Only after the above: retrieval, reranking, ratings, feedback loop.

Why this order matters:

· You fix the norm first. The top 20 themes probably cover the majority of volume.
· Disambiguation is designed from real patterns, not guesses.
· The remaining ambiguous tail is smaller and easier to handle with humans or escalation.
· Your KB, router, and metrics are all grounded in actual data.

One addition: also rank themes by volume times cost. A low-volume theme that causes high escalation or refunds may matter more than a high-volume trivial one.

If you want, I can give you a concrete prompt and workflow to run the 3,000 emails through for clustering and produce the top-theme list. That would be your literal first deliverable.



Following my instructions to start Phase 1 by extracting patterns from the 3,000 historical emails, please submit the following as evidence of completion. Do not summarise verbally. Send the actual artifacts.

Important context before you start:

The most frequent questions are usually the most ambiguous. High volume plus vagueness is what creates the problem, because the same words can mean very different things depending on the situation. "I can't log in" is the classic example. It could be a recruitment campaign, an exam, a scholarship - Scholastica.

This is why, once we know the top themes, we must map each one to its context, not just to a platform. Context is the deciding factor: who is asking, what they are trying to do, which service or campaign, which institute or client, and what is actually failing. Platform is part of that, but it is not the whole picture.

So the mapping exercise is not just a labelling task. It is the step that lets us find a repeatable paradigm for sorting these cases out. If we can define the context for each high-volume theme, we can design the disambiguation questions and route each email to the correct knowledge base. If we cannot, we are guessing, and the ambiguity problem stays.

Deliverable 1 - Volume and classification totals (standalone)
Just the numbers:

· Total emails processed
· Total classified
· Total unclassified
· Of the unclassified, how many flagged as critical outliers

No themes or analysis. Pure counts. This is the baseline.

Deliverable 2 - Theme frequency report
Ranked top 20 themes with counts and percentages, based only on the classified set. Flag critical outliers separately if any.

Deliverable 3 - Theme-to-context mapping
For each theme, define the full context: who is asking (audience), what they are trying to do, which service, campaign, platform, or institute or client is involved, what is triggering the issue, and where it surfaces. Platform is included, but context is the point. This mapping is what drives disambiguation and KB routing.

Deliverable 4 - Disambiguation and routing paradigm
For each high-volume ambiguous theme, the clarifying questions and routing rules, derived from the context mapping. Show the paradigm you used to sort cases, so it can be reused as new questions appear.

Deliverable 5 - Knowledge base structure
Proposed KBs with name, scope, and themes covered. We may need more than what we have, including context-specific KBs.

Deliverable 6 - Unclassified and excluded log
Every non-fitting email with reason for exclusion, e.g. client seeking a salesperson, individuals applying to work with us, spam marketing. Flag critical outliers separately.

Deliverable 7 - Sample audit
10 to 20 real emails across top themes and the unclassified set, with assigned theme and justification.

Submit all seven as a single package by tomorrow. Anything missing will be treated as incomplete.

Thanks.
```

Important interpretation: Some of what the CTO says is directionally correct
but may not fully reflect the system architecture. For example, they say "then
build the pipeline." Our architecture should still be a loop, not a brittle
pipeline. But the CTO's core point is correct: start with historical data, find
patterns, map themes to context, quantify ambiguity, and design the KB/router
around real evidence.

## 11. Message Sent to CTO/Stakeholder

A message was drafted for the user to send to the CTO. It said, in essence:

- We are currently extracting and analyzing approximately 3,000 historical
  helpdesk/ticket emails.
- The real ticket volume is slightly more than 3,000, but the working sample is
  approximately 3,000.
- We agree that the goal is to start with data, identify recurring patterns,
  map themes to context, and design disambiguation/KB routing around actual
  candidate behaviour.
- We are treating high-volume ambiguous themes carefully because the same words
  can mean different things depending on platform, candidate goal, campaign,
  institute/client, stage, and failure point.
- We will provide the requested seven deliverables soon after extraction and
  analysis are complete.

## 12. Historical Zoho Desk Extraction: What Was Done

We ran the historical extraction from Zoho Desk and completed it.

Script:

```sh
PYTHONPATH=. .venv/bin/python scripts/analyze_helpdesk_history.py \
  --source zoho \
  --out-dir data/helpdesk-history-full \
  --max-tickets 3000 \
  --thread-detail-mode always \
  --resume
```

During the long run, Zoho frequently timed out, especially on:

- `desk.zoho.com` ticket thread calls
- `accounts.zoho.com` OAuth refresh calls

We improved and operated the extractor with:

- resume support
- cached Zoho ticket summaries
- duplicate handling
- active checkpoint cleanup
- retry files
- thread detail mode
- worker count tuning
- fast pass mode
- request timeout tuning
- fatal auth guards
- cleaner partial/error handling

The final run completed successfully.

Final extraction status:

- Unique Zoho summaries: `2995`
- Tickets extracted: `2995 / 2995`
- Remaining: `0`
- Errors: `0`
- Empty rows: `0`
- Duplicates: `0`
- Summary-source fallback messages: `0`
- Final report `ticket_extraction_error_count`: `0`

Final generated timestamp:

```text
2026-09-21T11:32:31Z
```

Output directory:

```text
data/helpdesk-history-full
```

Important final files:

| File | Purpose |
| --- | --- |
| `data/helpdesk-history-full/tickets.jsonl` | Full extracted ticket histories, one JSON object per ticket. This is the main raw artifact. |
| `data/helpdesk-history-full/zoho_ticket_summaries.jsonl` | Cached Zoho ticket summaries used for resume and coverage. |
| `data/helpdesk-history-full/qa_pairs.jsonl` | Extracted candidate/officer Q&A pairs from public conversation threads. |
| `data/helpdesk-history-full/officer_review_proposals.jsonl` | Proposed knowledge/cue items that require officer review before becoming KB or registry entries. |
| `data/helpdesk-history-full/summary.json` | Final aggregate statistics from the extraction. |
| `data/helpdesk-history-full/failed_retry_tickets.jsonl` | Retry staging file. At final success, no remaining extraction errors are reflected in the final summary. |
| `data/helpdesk-history-full/extraction.log` | Log file if used by the running script. |
| `data/helpdesk-history-full/*.before-*` | Safety backups made during cleanup/retry operations. Keep until final artifacts are backed up. |

## 13. Final Extraction Summary

From `data/helpdesk-history-full/summary.json`:

```json
{
  "answered_pair_count": 3800,
  "attachment_pair_count": 1042,
  "candidate_message_count": 7994,
  "channels": {
    "Chat": 42,
    "EMAIL": 1,
    "Email": 2950,
    "Web": 2
  },
  "full_detail_message_count": 13402,
  "generated_at": "2026-09-21T11:32:31Z",
  "issue_tags": {
    "login_access": 827,
    "other": 1277,
    "payment": 459,
    "reschedule": 261,
    "result": 551,
    "secure_browser": 168,
    "submission": 939,
    "test_start": 2159,
    "verification": 261
  },
  "knowledge_proposal_count": 698,
  "message_count": 13402,
  "officer_message_count": 5408,
  "proposal_count": 890,
  "qa_pair_count": 4647,
  "quality_flags": {
    "truncated_suspected": 8
  },
  "risk_flags": {
    "asks_for_secret": 29,
    "asserts_backend_state": 60,
    "not_a_solution": 2949,
    "secure_browser_needs_tool_or_stage": 73,
    "security_sensitive_instruction": 7
  },
  "routing_cue_proposal_count": 192,
  "statuses": {
    "Closed": 2900,
    "Escalated": 38,
    "On Hold": 22,
    "Open": 35
  },
  "summary_source_message_count": 0,
  "ticket_count": 2995,
  "ticket_extraction_error_count": 0,
  "tool_mentions": {
    "FOT": 29,
    "Scholastica": 511,
    "Test Haven": 2
  },
  "truncated_suspected_message_count": 14,
  "unanswered_pair_count": 847
}
```

Important read of these numbers:

- We now have complete historical thread detail for 2,995 unique tickets.
- The data has 13,402 full-detail messages.
- There are 7,994 candidate messages and 5,408 officer messages.
- There are 4,647 extracted Q&A pairs.
- There are 3,800 answered pairs and 847 unanswered pairs.
- There are 1,042 pairs involving attachments.
- There are 698 knowledge proposals and 192 routing cue proposals.
- The extraction found significant risk flags, especially `not_a_solution`.
  This confirms that officer replies cannot be blindly imported as KB.
- `summary_source_message_count` is 0, which means the final run did not rely on
  summary fallback message content.
- `ticket_extraction_error_count` is 0, which means all tickets in the selected
  set were extracted successfully.

## 14. Extraction Script Changes Made During This Work

The extraction work required improving `scripts/analyze_helpdesk_history.py` and
related integration code. The following changes were made during this broader
session:

### `scripts/analyze_helpdesk_history.py`

Added or improved:

- `source_field`
- `source_is_detail`
- `truncated_suspected`
- `--thread-detail-mode always/missing/never`
- `--resume`
- `--workers`
- raw ticket de-duplication
- history collapse logic that keeps the best version
- retry of incomplete/lower-quality rows
- fatal auth guard via `FatalExtractionError`
- fatal detection for OAuth and Desk API `401/403`
- shared client support for workers
- safer resume behaviour
- `--fast-pass`
- `--request-timeout`
- partial/error row cleanup workflow
- extraction error recording instead of whole-batch failure

### `app/integrations/zoho_auth.py`

Added:

- configurable `request_timeout`
- a lock around token refresh for concurrent workers

### `app/integrations/zoho_desk_client.py`

Added:

- configurable `request_timeout`
- builder support for request timeout

### Tests

Relevant tests were added/updated:

- `tests/test_helpdesk_history_analysis.py`
- `tests/test_zoho_desk_client.py`

Focused tests passed:

```text
37 passed, 3 warnings
```

Warnings were deprecation warnings from existing dependencies, not extraction
logic failures.

## 15. Operational Notes From the Long Extraction

The historical extraction was not a simple one-shot run. It required repeated
restart and cleanup because Zoho/network calls timed out. Important operational
lessons:

- `screen`/detached terminal approaches caused confusion with orphaned
  processes. Managed sessions were clearer.
- Non-escalated sandboxed network often failed DNS for `accounts.zoho.com`.
- Escalated network access was required for reliable Zoho API calls.
- `--workers 2` was faster but created more timeout pressure.
- `--workers 1 --request-timeout 40` was slower but better for stubborn tickets.
- Active `tickets.jsonl` was periodically cleaned to remove failed/partial rows
  before restarting.
- Backups were created before cleanup, such as:
  - `tickets.jsonl.before-restart-clean-*`
  - `tickets.jsonl.before-retry-clean-*`
  - `tickets.jsonl.before-final-retry-clean-*`
- Counts could temporarily decrease when lower-quality rows were pulled out for
  refetching. This was expected and not data loss.
- Final verification showed:
  - `raw_rows 2995`
  - `clean_unique 2995`
  - `unique_summaries 2995`
  - `remaining_unique 0`
  - `errors 0`
  - `empty 0`
  - `duplicates 0`

## 16. What The Historical Data Can Be Used For

The rich historical data should be used to produce the CTO's artifact package
and to improve the AI safely.

Primary uses:

1. Theme discovery
   - find top recurring support issues
   - compare volume across themes
   - identify common wording candidates use

2. Ambiguity analysis
   - find high-volume vague phrases
   - identify what context was needed to resolve them
   - identify which clarifying questions officers asked

3. Theme-to-context mapping
   - audience
   - candidate goal
   - platform/tool
   - campaign/client/institute
   - stage
   - failure point
   - surface/channel

4. KB gap analysis
   - identify repeated officer answers not represented in approved KB
   - identify existing KB articles that need better variants
   - identify outdated or unsafe answers

5. Cue discovery
   - candidate language that indicates a tool or campaign
   - aliases and misspellings
   - client/institute names
   - domain or URL evidence
   - shared/non-exclusive cues

6. Clarification design
   - reusable question templates
   - option lists
   - screenshot request conditions
   - escalation triggers

7. Routing design
   - map themes to KB scope
   - map context to routing rules
   - determine when general KB is enough
   - determine when tool-specific KB is required

8. Evaluation set construction
   - build held-out examples for AI testing
   - include ambiguous, multi-turn, attachment, and escalation examples
   - avoid training/evaluating on the same exact examples

9. Officer review package
   - proposed articles
   - proposed cues
   - evidence examples
   - counterexamples
   - risk flags

Do not use historical data to directly auto-update production KB without review.

## 17. Immediate CTO Deliverable Work Still Needed

Extraction is complete. The next task is analysis and packaging. The deliverables
are not complete until actual artifacts are produced.

### Deliverable 1 - Volume and Classification Totals

Need produce standalone numbers:

- total emails/tickets processed
- total classified
- total unclassified
- among unclassified, number flagged as critical outliers

Important: CTO asked for "No themes or analysis" in this deliverable. It should
be a clean count-only artifact.

### Deliverable 2 - Theme Frequency Report

Need produce:

- ranked top 20 themes
- counts
- percentages based only on classified set
- critical outliers separately

Current script has heuristic `issue_tags`, but CTO specifically wants top themes.
The existing tags are useful but not enough. We likely need LLM/embedding/manual
review clustering to get robust top themes.

### Deliverable 3 - Theme-to-Context Mapping

For each theme:

- who is asking
- what they are trying to do
- service/product area
- platform/tool
- campaign/client/institute if applicable
- what triggers the issue
- where it surfaces
- ambiguity level
- evidence examples

This is the most important deliverable for the architecture.

### Deliverable 4 - Disambiguation and Routing Paradigm

For high-volume ambiguous themes:

- clarifying questions
- routing rules
- when to ask for screenshot
- when tool is required before answering
- when campaign/client/institute is required
- when general KB can answer
- when to escalate

The paradigm should be reusable for new questions, not just a static list.

### Deliverable 5 - Knowledge Base Structure

Need propose KBs/articles around actual themes.

Possible structure:

- General candidate support
- FOT technical support
- Test Haven technical support
- Scholastica support
- Secure browser and assessment environment issues
- Account/login/access
- OTP/verification
- Application/submission
- Results/certificates
- Payment/refunds
- Scheduling/reschedule
- Handoff/escalation policy

But this must be grounded in data, not assumed.

### Deliverable 6 - Unclassified and Excluded Log

Need every non-fitting email/ticket with reason, such as:

- sales inquiry
- job application to Dragnet
- spam/marketing
- colleague/personal outreach
- vendor outreach
- insufficient text
- non-candidate operational email
- critical outlier

Need flag critical outliers separately.

### Deliverable 7 - Sample Audit

Need 10 to 20 real examples across:

- top themes
- ambiguous themes
- attachment cases
- unclassified/excluded cases
- critical outliers

For each:

- ticket/message reference
- sanitized candidate text
- assigned theme
- context
- justification
- whether classification/routing is obvious or ambiguous

## 18. Need A Second Analysis Script/Workflow

The extraction script got the data. Now we need a separate analysis workflow for
the CTO deliverables.

Suggested workflow:

1. Normalize extracted candidate messages.
   - strip signatures
   - strip quoted chains
   - remove disclaimers
   - preserve subject, date, sender type, channel
   - keep ticket/message IDs

2. Build candidate "initial issue" records.
   - first candidate message per ticket
   - follow-up candidate messages if they introduce a new issue
   - subject + body + OCR/attachment marker where applicable

3. Classify/exclude.
   - candidate support issue
   - non-candidate support issue
   - spam/vendor/sales
   - internal/personal
   - too little information
   - critical outlier

4. Cluster/classify themes.
   - use embeddings plus clustering, or LLM classification with a stable label set
   - manually review top clusters
   - merge/split themes based on meaning

5. Map context.
   - audience
   - goal
   - service/tool/platform
   - campaign/client/institute
   - stage
   - failure surface
   - evidence strength

6. Quantify ambiguity.
   - can answer immediately
   - needs tool/platform
   - needs campaign/client/institute
   - needs stage
   - needs screenshot/error text
   - needs officer/backend lookup

7. Generate deliverable artifacts.
   - CSV/JSON/Markdown tables
   - sample audit
   - excluded log
   - KB proposal
   - routing paradigm

8. Prepare officer review.
   - proposed KB entries
   - proposed cue registry additions
   - example evidence
   - risk flags
   - approval/rejection states

## 19. SharePoint and KB Work Still Needed

The historical extraction is done, but SharePoint/KB cleanup remains.

Needed:

1. Reconnect to live SharePoint.
   - Inspect current `KB/General`, `KB/FOT`, `KB/Test Haven`,
     `KB/Scholastica`, and `KB/Helpdesk`.
   - Record document names, versions, source hashes, modified dates, and owners.

2. Confirm live index contents.
   - Helpdesk index
   - Voice/calling index
   - Any separate Chroma collections
   - Which documents are actually indexed now

3. Reconcile Helpdesk email script.
   - Identify factual troubleshooting items.
   - Move/merge facts into appropriate tool/general articles.
   - Keep tone/style in prompt/config.
   - Do not delete until coverage is verified.

4. Review General.
   - Do not blindly move all General content to prompts.
   - Some General entries may be actual business facts.

5. Improve ingestion.
   - Current Word converter has limitations.
   - Tables may not be extracted.
   - Body text before recognized headings may be skipped.
   - Subfolders and pagination need review.

6. Build canonical article publishing.
   - Generate both email/helpdesk and voice indexes from the same approved corpus.
   - Keep article IDs stable across consumers.
   - Keep manifests and rollback.

7. Build officer approval workflow.
   - approve/edit/reject knowledge proposals
   - approve/edit/reject cue proposals
   - editing invalidates prior approval
   - officer identity and timestamp required

## 20. Current Local Artifacts To Inspect Next

Main extraction artifacts:

```text
data/helpdesk-history-full/tickets.jsonl
data/helpdesk-history-full/qa_pairs.jsonl
data/helpdesk-history-full/officer_review_proposals.jsonl
data/helpdesk-history-full/summary.json
data/helpdesk-history-full/zoho_ticket_summaries.jsonl
```

Existing architecture docs:

```text
docs/helpdesk-conversation-architecture.md
docs/helpdesk-conversation-rollout.md
docs/blocked-items-and-helpdesk-plan.md
```

KB folders:

```text
kb/
kb_voice/
documents/
documents/converted/
```

Relevant script:

```text
scripts/analyze_helpdesk_history.py
```

Relevant tests:

```text
tests/test_helpdesk_history_analysis.py
tests/test_zoho_desk_client.py
```

## 21. What Not To Do

Do not:

- Treat the extraction as the final CTO deliverable package.
- Import `officer_review_proposals.jsonl` directly into KB.
- Assume campaign-specific KBs exist just because the CTO mentions context.
- Assume Helpdesk SharePoint folder should remain an answer source forever.
- Delete Helpdesk KB before reconciling and verifying coverage.
- Infer tool from generic terms like "exam", "test", or "assessment".
- Let model classifier output override explicit user text.
- Ask for only one or two clarifications and then give up if useful progress is
  still possible.
- Keep asking the same clarification question repeatedly.
- Claim to see a screenshot if OCR/vision did not actually read it.
- Use officer replies as truth when they include backend assertions or one-off
  manual handling.
- Re-answer a ticket after a human officer has taken ownership.
- Retry uncertain sends automatically.
- Enable live sending before shadow evaluation.

## 22. Suggested Next Step

The next agent should start by producing the CTO artifact package from the
completed extraction.

Recommended first concrete actions:

1. Inspect `tickets.jsonl`, `qa_pairs.jsonl`, and
   `officer_review_proposals.jsonl` schemas.
2. Build or update an analysis script that creates:
   - normalized email/message records
   - classified vs unclassified records
   - top theme candidates
   - theme frequency report
   - theme-to-context mapping draft
   - disambiguation/routing draft
   - proposed KB structure
   - excluded/unclassified log
   - sample audit
3. Use the current heuristic tags as a starting point, but do not stop there.
4. Manually inspect top clusters before naming them.
5. Keep all outputs linked back to ticket/message IDs.
6. Produce actual files, not just a verbal summary.
7. After the artifact package is ready, move into officer review and SharePoint
   KB reconciliation.

## 23. Short Mental Model For The Next Agent

This work is about turning messy, ambiguous candidate support into a reliable
diagnostic conversation.

The AI should not be a one-shot classifier. It should be a careful support
operator with memory:

- understand what the candidate is trying to do
- gather missing context
- use approved cues
- retrieve the right KB only after scope is clear enough
- answer only when supported
- ask for screenshots/files when helpful
- remember the whole thread
- stop when it is guessing
- escalate with useful context

The historical Zoho extraction is now complete. The next stage is to use that
data to discover the actual recurring patterns and build the CTO-requested
deliverables around real evidence.
