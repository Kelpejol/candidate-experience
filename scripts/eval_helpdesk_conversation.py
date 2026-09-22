"""Multi-turn evaluation of the LangGraph conversation flow (currently
disabled in production behind HELPDESK_CONVERSATION_ENABLED) against real
historical ticket threads.

Unlike scripts/eval_helpdesk_accuracy.py (single-shot classify -> retrieve
-> draft, the pipeline actually live today), this replays each ticket's
REAL message history turn by turn through the graph itself
(app/services/helpdesk_conversation_graph.py), so it can ask clarifying
questions across multiple turns exactly as the graph is designed to.

For each ticket: for every candidate message in order, invoke the graph
with the full message history up to that point, using a fresh in-memory
checkpointer per ticket (thread_id constant) so observations/questions/
resolution/previous_issue accumulate correctly across that ticket's own
turns -- same mechanism app/services/helpdesk_conversation_service.py
relies on in production, just without a live Zoho client.

Caveats, stated plainly (not hidden in the numbers):
- attachment_notes is always [] here -- no attachment OCR text available
  from the historical extraction, so any turn that would need a
  screenshot strategy is judged without that signal.
- registry is loaded from the real (currently empty) config/helpdesk-cues.json,
  so it reflects the real current gap, not a hypothetical populated one.
- candidate_name is always None -- PII was stripped from this extraction,
  so the graph always falls back to "Dear Candidate,".

Resumable: writes incrementally to data/helpdesk-eval/conversation_results.jsonl,
skips ticket_ids already done.
"""

import json
import re
import socket
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from langgraph.checkpoint.sqlite import SqliteSaver

from app.core.config import get_settings
from app.services import helpdesk_conversation_graph as graph_module

socket.setdefaulttimeout(20)

DATA_DIR = Path("data/helpdesk-history-full")
OUT_DIR = Path("data/helpdesk-eval")
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH = OUT_DIR / "conversation_results.jsonl"

REGISTRY = json.load(open("config/helpdesk-cues.json"))


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
    "You are grading a candidate-support helpdesk AI's final draft reply "
    "(after possibly asking clarifying questions across a real multi-turn "
    "conversation) against what a real officer actually told the candidate "
    "historically, for a recruitment assessment company (Dragnet "
    "Solutions). Score on 4 axes, 1-5 each:\n"
    "- factual_alignment: does the AI's answer match what officers "
    "actually tell candidates for this kind of issue?\n"
    "- hallucination: does the AI invent any link, policy, date, or "
    "promise not grounded in real KB content? 5 = nothing invented.\n"
    "- escalation_correctness: when the AI escalated, was that the right "
    "call? When it answered, was answering appropriate?\n"
    "- tone: warm, professional, appropriately concise?\n\n"
    "Many real historical officer replies are just a generic \"ticketed "
    "for review\" acknowledgment with no real fix -- do not penalize "
    "factual_alignment against a non-answer; judge against what officers "
    "do across similar tickets.\n\n"
    'Respond with ONLY JSON: {"factual_alignment": 1-5, "hallucination": '
    '1-5, "escalation_correctness": 1-5, "tone": 1-5, "notes": "one sentence"}'
)


def load_tickets():
    tickets = []
    with open(DATA_DIR / "tickets.jsonl") as f:
        for line in f:
            d = json.loads(line)
            msgs = [m for m in d.get("messages", []) if m.get("role") in ("candidate", "officer")]
            msgs.sort(key=lambda m: m.get("created_time") or "")
            if not any(m["role"] == "candidate" for m in msgs):
                continue
            tickets.append({
                "ticket_id": d["ticket_id"],
                "ticket_number": d.get("ticket_number"),
                "subject": d.get("subject") or "",
                "channel": d.get("channel") or "Email",
                "messages": msgs,
            })
    return tickets


def run_ticket(ticket):
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    try:
        graph = graph_module.build_graph(SqliteSaver(connection))
        config = {"configurable": {"thread_id": ticket["ticket_id"]}}

        history = []
        turns = []
        asked_clarification = False
        final_result = None

        for m in ticket["messages"]:
            history.append({"id": m["id"], "role": m["role"], "text": m["text"]})
            if m["role"] != "candidate":
                continue
            state = {
                "event_id": m["id"],
                "latest_id": m["id"],
                "latest_text": m["text"],
                "subject": ticket["subject"],
                "messages": list(history),
                "attachment_notes": [],
                "sentiment": None,
                "channel": ticket["channel"],
                "candidate_name": None,
                "registry": REGISTRY,
            }
            result = graph.invoke(state, config)
            action = result.get("decision", {}).get("action")
            if action in ("ask_clarification", "request_attachment"):
                asked_clarification = True
            turns.append({"message_id": m["id"], "action": action, "rule": result.get("decision", {}).get("rule")})
            final_result = result

        real_officer_texts = [m["text"] for m in ticket["messages"] if m["role"] == "officer"]
        real_final_answer = real_officer_texts[-1] if real_officer_texts else "(no officer reply on file)"

        final_action = final_result.get("decision", {}).get("action") if final_result else None
        final_reply = final_result.get("reply") if final_result else None
        resolved = final_action == "draft_reply" and final_reply

        scores = None
        if resolved:
            judge_user = (
                f"Candidate's real message thread (last message):\n{ticket['messages'][-1]['text'][:1200]}\n\n"
                f"AI's final draft:\n{final_reply[:1500]}\n\n"
                f"Real historical officer reply:\n{real_final_answer[:1500]}"
            )
            try:
                raw = gateway_chat(JUDGE_SYSTEM, judge_user, max_tokens=300)
                scores = parse_json_response(raw)
            except Exception as e:
                scores = {"factual_alignment": None, "hallucination": None,
                          "escalation_correctness": None, "tone": None,
                          "notes": f"judge error: {e}"}

        return {
            "ticket_id": ticket["ticket_id"],
            "ticket_number": ticket["ticket_number"],
            "candidate_turns": len(turns),
            "asked_clarification": asked_clarification,
            "final_action": final_action,
            "final_rule": final_result.get("decision", {}).get("rule") if final_result else None,
            "resolved": bool(resolved),
            "turns": turns,
            "scores": scores,
        }
    finally:
        connection.close()


def main():
    done_ids = set()
    if OUT_PATH.exists():
        with open(OUT_PATH) as f:
            for line in f:
                d = json.loads(line)
                if "ticket_id" in d:
                    done_ids.add(d["ticket_id"])
        print(f"resuming, {len(done_ids)} already evaluated")

    tickets = [t for t in load_tickets() if t["ticket_id"] not in done_ids]
    print(f"evaluating {len(tickets)} tickets...")

    with open(OUT_PATH, "a") as out_f:
        with ThreadPoolExecutor(max_workers=6) as ex:
            futures = [ex.submit(run_ticket, t) for t in tickets]
            for i, fut in enumerate(futures):
                try:
                    result = fut.result()
                    out_f.write(json.dumps(result) + "\n")
                    out_f.flush()
                except Exception as e:
                    out_f.write(json.dumps({"error": str(e)}) + "\n")
                    out_f.flush()
                if i % 50 == 0:
                    print(f"  {i}/{len(tickets)}")

    print("\nConversation eval complete.")


if __name__ == "__main__":
    main()
