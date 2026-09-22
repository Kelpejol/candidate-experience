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
