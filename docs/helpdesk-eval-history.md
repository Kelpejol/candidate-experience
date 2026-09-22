# Helpdesk accuracy eval — history

Run via `scripts/eval_helpdesk_accuracy.py`, scored against real historical
tickets by an LLM judge (rubric fixed in `JUDGE_SYSTEM` in that script — keep
it unchanged between runs so scores stay comparable; if the rubric changes,
start a new section below rather than comparing directly against older rows).

Raw per-ticket results live in `data/helpdesk-eval/results.jsonl`
(gitignored — contains real candidate text) and are not preserved here;
only the aggregate numbers are, since those carry no PII.

## Rubric v1 (this script's current `JUDGE_SYSTEM`)

| Date | Tickets scored | Factual alignment | Hallucination | Escalation correctness | Tone | Escalation rate | Grounded rate | Notes |
|---|---|---|---|---|---|---|---|---|
| 2026-09-16 (ad hoc, script not saved) | 1155 (150 + 1000) | 4.04/5 | 4.32/5 | 3.94/5 | 4.6/5 | — | — | Original eval, different rubric — penalized factual_alignment against non-answer historical replies. Script never committed; numbers kept for reference only, not directly comparable to rows below. |
| 2026-09-21 | 2957 (of 2963 attempted, 6 errors) | 4.64/5 | 4.78/5 | 4.69/5 | 4.87/5 | 76.7% | 74.1% | First run of this saved script/rubric — the actual comparable baseline going forward. Rubric explicitly does not penalize factual_alignment when the real historical reply was itself a non-answer (generic "ticketed for review"). Escalation rate (77%) is high — AI defers to a human on most tickets, including some where KB grounding succeeded. |

## Conversation-graph eval (`scripts/eval_helpdesk_conversation.py`)

Separate script — replays each ticket's real message thread turn by turn
through `app/services/helpdesk_conversation_graph.py` (the LangGraph flow,
disabled in production behind `HELPDESK_CONVERSATION_ENABLED=False`), so it
can ask clarifying questions across multiple turns, unlike the single-shot
eval above. Not directly comparable to the table above (different pipeline
entirely) — comparable only against future runs of this same script.

| Date | Tickets replayed | Asked ≥ 1 clarifying question | Reached a final answer (`draft_reply`) | Of single-shot escalations, resolved by this flow instead | Notes |
|---|---|---|---|---|---|
| 2026-09-22 | 2963 | 48.2% | **0.1%** (2 tickets) | 0.04% (1 of 2268) | Root cause identified and verified against real examples: `config/helpdesk-cues.json` is empty, and the graph will only recognize a platform (FOT/Test Haven/Scholastica) if the candidate names it literally or a registry cue maps some other phrase to it. Almost no real candidate names the platform, so nearly every technical-issue ticket gets stuck asking "which platform are you using?" and stalls into escalation when the replayed historical follow-up doesn't cleanly answer that question. This contradicts an earlier code-only audit (2026-09-21) that concluded the empty registry was "non-blocking" — that conclusion was wrong; verify empirically before trusting a static-trace conclusion again. Also caveat: this historical data has PII replaced with placeholders (`[NAME]`, `[STUDENT_ID]`), which may itself inflate one failure mode (`conversation_understanding_failed`, ~11.6% of tickets) where the LLM's claimed observation can't be verified as an exact quote against placeholder-laden text. Re-run after the cue registry is populated (see officer review tool) to see the real delta. |
