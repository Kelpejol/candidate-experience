"""Historical accuracy evaluation for the helpdesk AI, run offline against
real historical tickets — no live Zoho ticket or send required.

Rebuilds the ad hoc eval run earlier on the VM (1000 tickets, scored
factual_alignment/hallucination/escalation_correctness/tone), which was
never saved as a script. This version calls the real production functions
directly (classify_ticket -> retrieve_grounding -> generate_draft_reply),
the same path process_ticket() uses, so the eval reflects actual current
behavior against the current KB.

Resumable: writes incrementally to data/helpdesk-eval/results.jsonl and
skips ticket_ids already scored.
"""

import json
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from sqlmodel import Session

from app.core.config import get_settings
from app.core.database import engine
from app.core.vocabulary import is_tool_allowed
from app.services.campaign_scope_service import resolve_campaign, tool_name_to_scope
from app.services.helpdesk_classifier import classify_ticket
from app.services.helpdesk_draft_service import generate_draft_reply
from app.services.helpdesk_kb_service import retrieve_grounding

socket.setdefaulttimeout(20)

DATA_DIR = Path("data/helpdesk-history-full")
OUT_DIR = Path("data/helpdesk-eval")
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH = OUT_DIR / "results.jsonl"


def resolve_kb_scopes(session, classification):
    tool_scope = None
    if classification.tool_name and is_tool_allowed(classification.tool_name):
        tool_scope = tool_name_to_scope(classification.tool_name)
    campaign_scope = None
    if classification.campaign_name:
        resolved = resolve_campaign(session, classification.campaign_name)
        if resolved["status"] == "found":
            campaign_scope = resolved["scope"]
    return tool_scope, campaign_scope


def gateway_chat(system, user, max_tokens=400, tries=6, delay=3):
    settings = get_settings()
    for _ in range(tries):
        try:
            resp = httpx.post(
                f"{settings.inference_base_url}/chat",
                headers={"Authorization": f"Bearer {settings.inference_api_key}"},
                json={
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": 0.1,
                },
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json()["output"].strip()
        except Exception:
            time.sleep(delay)
    raise RuntimeError("gateway_chat failed after retries")


def parse_json_response(raw):
    cleaned = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    return json.loads(cleaned)


JUDGE_SYSTEM = (
    "You are grading a candidate-support helpdesk AI's draft reply against "
    "what really happened historically on this ticket, for a recruitment "
    "assessment company (Dragnet Solutions). You are given the candidate's "
    "real message, the AI's draft (or ESCALATED, meaning the AI declined to "
    "answer and would route to a human), and the real historical officer "
    "reply that was actually sent.\n\n"
    "Score on 4 axes, 1-5 each:\n"
    "- factual_alignment: does the AI's answer (when it answered) match "
    "what the real officer actually told candidates for this kind of issue? "
    "5 = fully consistent, 1 = contradicts or is unrelated.\n"
    "- hallucination: does the AI invent any link, policy, date, or promise "
    "not grounded in real KB content? 5 = nothing invented, 1 = fabricated "
    "specifics presented as fact.\n"
    "- escalation_correctness: when the AI escalated, was that the right "
    "call (genuinely needs a human/case-by-case judgment)? When it "
    "answered, was answering appropriate (not something that actually "
    "needed escalation)? 5 = correct call, 1 = clearly wrong call.\n"
    "- tone: is the reply warm, professional, and appropriately concise? "
    "5 = excellent, 1 = poor.\n\n"
    "Note: many real historical officer replies are just a generic "
    "\"ticketed for review\" acknowledgment with no real fix — that is not "
    "grounds to penalize the AI's factual_alignment if the AI's answer is "
    "itself grounded and reasonable; judge against the substance of what "
    "officers do across similar tickets, not literally this one reply, "
    "when this one reply is a non-answer.\n\n"
    'Respond with ONLY JSON: {"factual_alignment": 1-5, "hallucination": '
    '1-5, "escalation_correctness": 1-5, "tone": 1-5, "notes": "one sentence"}'
)


def eval_one(record):
    ticket_id = record["ticket_id"]
    subject = record["subject"]
    text = record["candidate_text"]
    real_answer = record.get("officer_answer") or "(no substantive reply on file)"

    classification = classify_ticket(subject, text)
    with Session(engine) as session:
        tool_scope, campaign_scope = resolve_kb_scopes(session, classification)
    grounding = retrieve_grounding(text, k=3, tool_scope=tool_scope, campaign_scope=campaign_scope)
    draft = generate_draft_reply(subject, text, grounding.chunks)

    ai_output = draft if draft else "ESCALATED (no answer generated, would route to human)"

    judge_user = (
        f"Candidate's real message:\nSubject: {subject}\n{text[:1500]}\n\n"
        f"AI's draft:\n{ai_output[:1500]}\n\n"
        f"Real historical officer reply:\n{real_answer[:1500]}"
    )
    try:
        raw = gateway_chat(JUDGE_SYSTEM, judge_user, max_tokens=300)
        scores = parse_json_response(raw)
    except Exception as e:
        scores = {"factual_alignment": None, "hallucination": None,
                  "escalation_correctness": None, "tone": None,
                  "notes": f"judge error: {e}"}

    return {
        "ticket_id": ticket_id,
        "ticket_number": record.get("ticket_number"),
        "issue_category": classification.issue_category,
        "tool_name": classification.tool_name,
        "grounded": grounding.grounded,
        "escalated": draft is None,
        "scores": scores,
    }


def load_qa_by_ticket():
    by_id = {}
    with open(DATA_DIR / "qa_pairs.jsonl") as f:
        for line in f:
            d = json.loads(line)
            tid = d["ticket_id"]
            if tid not in by_id:
                by_id[tid] = d
    return by_id


def main():
    qa_by_ticket = load_qa_by_ticket()

    done_ids = set()
    if OUT_PATH.exists():
        with open(OUT_PATH) as f:
            for line in f:
                done_ids.add(json.loads(line)["ticket_id"])
        print(f"resuming, {len(done_ids)} already evaluated")

    records = [d for tid, d in qa_by_ticket.items() if tid not in done_ids]
    print(f"evaluating {len(records)} tickets...")

    with open(OUT_PATH, "a") as out_f:
        with ThreadPoolExecutor(max_workers=6) as ex:
            futures = [ex.submit(eval_one, r) for r in records]
            for i, fut in enumerate(futures):
                try:
                    result = fut.result()
                    out_f.write(json.dumps(result) + "\n")
                    out_f.flush()
                except Exception as e:
                    out_f.write(json.dumps({"error": str(e)}) + "\n")
                    out_f.flush()
                if i % 50 == 0:
                    print(f"  {i}/{len(records)}")

    print("\nEval complete.")


if __name__ == "__main__":
    main()
