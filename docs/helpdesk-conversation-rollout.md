# Conversation implementation and remaining work

Updated 2026-09-19. The new runtime is implemented behind
`HELPDESK_CONVERSATION_ENABLED=false`. No production flag, SharePoint document,
knowledge index, or officer approval was changed by this implementation.

## Implemented

- A real LangGraph StateGraph: prepare evidence, understand, choose, retrieve,
  compose and review. Candidate replies start another graph invocation with the
  same persisted conversation. The worker exits between candidate messages.
- Durable SQLite checkpoints with separate namespaces for dry runs, drafts,
  email sends and WhatsApp sends. Structured evidence, outstanding questions,
  previous issue and tool resolution survive a worker restart.
- Approved cue context, exact tool evidence, typo/partial-name suggestions, and
  confirmation of a single suggested entity. A model's tool/campaign label cannot
  select the KB scope. Latest explicit corrections take precedence.
- Clarification based on useful next steps, with no one/two-turn maximum.
  Repeating the same question strategy with unchanged evidence escalates;
  a useful alternative strategy or new evidence allows continued clarification.
- Screenshot requests, attachment OCR context and honest handling of unreadable
  attachments. Existing Azure OCR configuration remains off until configured.
- Tool-scoped retrieval with general content allowed alongside it. Technical
  issues require matching tool content. Unknown/wrong scopes and weak chunks
  cannot supply the answer. Campaign KB is not required by this flow.
- A structured answer check for support, applicability, unresolved requests and
  repetition of failed fixes. Failed or unavailable reasoning/retrieval/review
  produces an officer handoff with diagnostic context.
- Public conversation context with draft/private/unknown messages excluded,
  detail-level privacy checks, UTC ordering, and paginated thread listing.
- A durable per-candidate-message delivery receipt. Delivery intent commits
  before the send; completed events are deduplicated. An unknown send outcome is
  held for human reconciliation, never automatically retried.
- Recipient, ownership, ticket status and newest-message checks immediately
  before delivery. Unassigned manual officer replies also pause automation.
- Existing channel flags and email allowlist continue to gate all actual sends.
  A new flag does not grant permission to send to candidates.
- Review dashboard categories for clarification and attachment requests, and
  automation counts covering the new actions.
- Local operator commands to initialise the new delivery table, inspect receipts,
  reconcile uncertain sends, and hand future messages back to automation.

## Local checks and deployment

Install `requirements.txt` into the application environment. LangGraph and its
SQLite checkpoint adapter are pinned there, including their new dependencies.
The additive delivery table is registered with normal API startup. For a worker
deployment before restarting the API, initialise just the new table:

```sh
PYTHONPATH=. .venv/bin/python scripts/manage_helpdesk_conversation.py init
```

Configuration:

```dotenv
HELPDESK_CONVERSATION_ENABLED=false
HELPDESK_CHECKPOINT_PATH=./data/helpdesk-checkpoints.sqlite
HELPDESK_CUE_REGISTRY_PATH=./config/helpdesk-cues.json
HELPDESK_CONVERSATION_CONTEXT_MESSAGES=20
HELPDESK_AUTOMATION_ASSIGNEE_IDS=
```

Use absolute paths under a persistent application data directory on deployment.
Back up the application DB, checkpoint DB and approved registry together.
The current checkpoint and file-lock adapter supports multiple workers on ONE
host with shared local paths. A multi-host deployment requires a shared database
checkpointer and distributed ownership before enabling this runtime there.
Memory passed to a model is bounded: at most 50 recent public messages (20 by
default), 100 quoted observations and 100 previous questions. Full thread history
is paginated from Zoho, but arbitrary retrieval of older facts not already captured
in those observations is still a future enhancement.

Leave `HELPDESK_AUTOMATION_ASSIGNEE_IDS` empty to treat every assigned ticket as
human-owned. Add only IDs explicitly designated for automation/shared intake by
the officers. Human assignment and manual responses always need deliberate
consideration when transferring an existing queue to automation.

Start verification with the new graph flag enabled in an isolated test/shadow
environment and ALL delivery/tag flags disabled. Dry-run checkpoint state is
separate from live state. Use the existing email allowlist for an authorised real
send test. Enable wider delivery only after checking actual graph/model behaviour
and officer readiness. The old path remains selected while the graph flag is off.

The local automated tests exercise the actual LangGraph/checkpoint adapter, with
fake Zoho and inference adapters. They do not establish the quality of the real
model's reasoning, actual Zoho response metadata, or current SharePoint content.

The frontend production build and lint pass. The latest full pytest run disables
third-party plugin autoload to avoid the sandbox-blocked test-plugin listener;
project tests and fixtures still run normally. An earlier full run with plugins
enabled also passed before the last additional delivery/attachment cases.

Dependency inspection found a pre-existing conflict between the optional
`deepeval==4.2.0` evaluator (requires Click below 8.4) and the application dependency
`huggingface-hub==1.23.0` (requires Click at least 8.4.2). The environment now matches
the application's existing `click==8.4.2` pin. The optional evaluation dependency
set still needs a compatible update or isolation; `pip check` is not clean while
both incompatible requirements are installed. This is separate from LangGraph.

## Operator recovery

```sh
PYTHONPATH=. .venv/bin/python scripts/manage_helpdesk_conversation.py inspect --ticket TICKET_ID
```

For an uncertain send, inspect the real Zoho thread first. Then record what was
verified; these commands record an operator assertion and do not independently
verify the remote reply or send any message:

```sh
PYTHONPATH=. .venv/bin/python scripts/manage_helpdesk_conversation.py reconcile --ticket TICKET_ID --turn TURN_ID --outcome sent --reply-id ZOHO_REPLY_ID --operator OFFICER_ID --reason "Verified in Zoho"
PYTHONPATH=. .venv/bin/python scripts/manage_helpdesk_conversation.py reconcile --ticket TICKET_ID --turn TURN_ID --outcome not_sent --operator OFFICER_ID --reason "Verified no reply was accepted"
```

After the officer has finished handling the case, explicit handback enables
FUTURE incoming messages. It does not resend an earlier response or answer the
same previously processed candidate message again. Choose the intended mode:

```sh
PYTHONPATH=. .venv/bin/python scripts/manage_helpdesk_conversation.py resume --ticket TICKET_ID --mode email --operator OFFICER_ID --reason "Ready for automation on the next candidate reply"
```

The operator command refuses handback while delivery intents remain pending or
uncertain. A human assignee must still be cleared/transferred through the normal
Zoho process before automation can answer. These are local administrator commands;
an authenticated officer-facing recovery interface remains to be built.

## Officer cue input

`config/helpdesk-cues.json` deliberately starts empty. Canonical tool names work
without cues. Do not insert inferred mappings for exam, test, SB, Talview or
DRAGNETFOT.COM. Officers must confirm any additional aliases, URLs, client/exam
names, tool relationships, conditional mappings and expiry dates.

Each cue records `id`, `phrase`, `tools`, optional `campaign`, `conditions`,
`exclusive`, `source`, `status`, `approved_by`, `approved_at`, `valid_until`, and
`approved_digest`. Only currently valid approved entries whose digest matches
`Cue.content_digest()` enter the runtime. Changing the phrase, tool relationship,
conditions, source or validity period invalidates the old approval digest.
Conditional/shared cues offer suggestions; only an unconditional exclusive cue
can directly identify a tool. Fuzzy matches always require confirmation.

The registry file is a trusted deployment input, not an authenticated approval UI.
Officer approval collection, reviewer authentication, and generating/publishing
approved registry revisions still belong to the data-review work below. A checksum
prevents accidental reuse of an approval after editing; it is not a signature or
proof of officer identity.

## Remaining data and knowledge work

1. **Live SharePoint inventory.** Re-establish the configured reader connection;
   inspect the five current folders and document versions. The prior audit used
   local originals/converted copies because Python could not resolve Microsoft's
   login host. Record current files, headings, tables, skipped content and source
   hashes. Inspect BOTH helpdesk and voice indexes, not just one.
2. **Helpdesk FAQ reconciliation.** Review the 28-topic mapping in
   `helpdesk-conversation-architecture.md` against live FOT/Test Haven articles.
   Merge useful differences and question variants, preserve tool/stage/OS
   conditions, and resolve conflicts with officers. Do not blindly copy the
   email FAQ into every tool or delete it before verifying coverage.
3. **Separate behaviour from facts.** Move email/voice style into versioned
   behaviour configuration; retain operational facts as approved knowledge.
   Review General individually rather than deleting it wholesale.
4. **Knowledge safety and currency.** Obtain explicit applicability for antivirus
   advice, identity alternatives, recording duration, restart/refresh steps and
   compatibility lists. Remove unsupported claims that a specific candidate's
   credentials are valid or their submission was verified.
5. **Canonical article schema and publishing.** Stable article IDs, variants,
   tool applicability, effective dates, source versions and approval history are
   still required. Generate both consumer indexes from one approved corpus.
   Build/version/validate a replacement before activation; the existing reindex
   implementation still deletes a scope before rebuilding it and needs upgrading.
6. **Importer improvements.** The existing SharePoint KB loader still needs listing
   pagination, explicit subfolder policy and richer document validation. Word
   tables and other file types remain unsupported KB authoring formats. Runtime
   attachment OCR is a separate mechanism and does not fix KB ingestion.
7. **Historical extraction script.** `scripts/analyze_helpdesk_history.py` now
   creates offline review data from either the local mirror or live Zoho:

   ```sh
   PYTHONPATH=. .venv/bin/python scripts/analyze_helpdesk_history.py --source local --max-tickets 3000
   PYTHONPATH=. .venv/bin/python scripts/analyze_helpdesk_history.py --source zoho --max-tickets 3000 --retries 3 --retry-delay 3
   ```

   It reconstructs public candidate/officer exchanges, redacts obvious emails,
   phones, secrets and student IDs, collapses duplicate message text, strips
   common ticket-receipt/footer boilerplate from proposed answers, compares
   proposals lexically with the local KB, records source ticket/message IDs,
   flags weak/non-solution replies, and writes:
   `data/helpdesk-history/summary.json`,
   `data/helpdesk-history/tickets.jsonl`,
   `data/helpdesk-history/qa_pairs.jsonl`, and
   `data/helpdesk-history/officer_review_proposals.jsonl`.
   The local mirror currently contains ticket metadata but no thread bodies, so
   only a Zoho-backed run produces real Q&A pairs. A small live sample completed
   on 2026-09-19, but Zoho connectivity was slow and intermittent; ticket-level
   extraction errors are now recorded instead of failing the whole batch. The
   full 3,000-ticket sweep still needs to run as an operational batch, ideally
   from a stable network path. This script creates proposals only; it does not
   publish knowledge or approve cues.
8. **Officer review workflow.** Build durable, authenticated per-item approval,
   edit, reject and request-information states for both articles and cues. Compare
   each proposed answer with current KB coverage. Editing invalidates approval.
   The existing 38-item browser-local review page is preliminary material only.
9. **Publish approved history.** After officers approve exact revisions, publish
   those entries to the canonical source, update both consumer indexes, and retain
   rollback manifests. Never import raw officer replies directly as approved KB.
10. **Azure OCR and vision.** Supply the Azure endpoint/key and test real candidate
    screenshots/files. OCR extracts text; richer visual/layout reasoning still
    requires a supported vision model and evaluation. OCR failure escalates when
    it leaves no readable message; the AI does not claim to see the attachment.
11. **Live conversation evaluation.** Test ambiguous starts, short confirmations,
    corrections, repeated failures, five-plus useful clarifications, screenshots,
    multi-issue requests and handoffs against the actual model. Keep held-out
    conversations separate from those used to enrich the KB. Confirm live Zoho
    thread pagination/metadata, draft sending and WhatsApp delivery in a test queue.
12. **Operations.** Agree automation assignees, officer routing coverage, retention
    for checkpoint/message data, recovery responsibilities, and live monitoring of
    stalled clarification, rejected answers, unknown delivery outcomes and KB gaps.

## Known boundaries

The graph makes a structured model judgment about whether another useful question
exists; the backend additionally blocks exact repeated strategies without new
quoted evidence. This is not a calibrated statistical progress measure and needs
real conversation evaluation. It does not impose a maximum conversational count.

One graph owns the conversation and can describe multiple unresolved issues in
its state. Dedicated per-issue subgraphs and mixed-tool answers are not implemented;
conflicting tool evidence currently asks for clarification or escalates.

An answer-review model can fail in the same ways as the writer. Its checks and
the scope/evidence gates reduce risk, but are not proof that every answer is
correct. Approved source quality and real evaluations are required before rollout.

There remains a small external race between the final Zoho read and sending a
reply: the remote API offers no compare-and-send transaction here. Unknown send
outcomes are deliberately handed to a human; exactly-once external delivery is
not claimed. Replay-safe delivery applies to the new graph path, not the legacy
path selected while its feature flag is off.
