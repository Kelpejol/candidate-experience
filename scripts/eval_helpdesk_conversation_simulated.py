"""Multi-turn conversation-graph eval with a SIMULATED candidate reply.

scripts/eval_helpdesk_conversation.py replays real historical message
threads verbatim -- but the "next candidate message" in that data is almost
always a duplicate of the original email, never an actual answer to a
clarifying question the AI asks. That makes it structurally impossible for
a ticket to ever resolve there, regardless of how good the question is: the
eval can't measure the one thing multi-turn clarification is for.

This script closes that gap: when the graph asks a clarifying question, a
second LLM call plays the candidate -- honestly, using only what a real
candidate would know about their own situation (their original message and
subject) -- and answers it. That simulated reply becomes the next turn, and
the conversation continues (bounded) until it resolves, escalates for a
real (non-clarification) reason, or hits a turn cap.

Caveat stated plainly: this measures "if a cooperative, honest candidate
answers, does the flow work" -- not "how often do real candidates actually
answer clearly," which real historical data can't tell us either way. Also
reports a simulator sanity check: for the subset of tickets with a known
ground-truth platform (mined the same way as the officer-review cue
candidates), how often the simulated candidate's stated platform actually
matches it.
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
OUT_PATH = OUT_DIR / "conversation_simulated_results.jsonl"

REGISTRY = json.load(open("config/helpdesk-cues.json"))
MAX_SIMULATED_TURNS = 5
TOOLS = ["FOT", "Test Haven", "Scholastica"]


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
                    "temperature": 0.2,
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


SIMULATE_SYSTEM = (
    "You are playing the CANDIDATE in a real support conversation, for a "
    "test-eval of a support AI -- not the AI itself. You already sent the "
    "original message shown below. The support AI just asked you a "
    "question. Answer as an honest, cooperative real candidate would, "
    "using ONLY what you would actually know about your own situation "
    "(your exam/application, what platform or tool you were using, your "
    "own account). Do not invent institutional facts (policies, deadlines, "
    "ticket outcomes). If the question asks which platform/tool, answer "
    "with the actual name if it's something you would plausibly know or "
    "can tell from your own context (e.g. the exam program you mentioned); "
    "if you genuinely wouldn't know, say so honestly and ask how to check. "
    "Keep it short and natural, like a real short email reply, not a "
    "list.\n\n"
    'Respond with ONLY JSON: {"reply": "..."}'
)


def simulate_candidate_reply(subject, original_message, ai_question, prior_turns):
    history = "\n".join(f"- You said: {t['candidate']}\n  AI asked: {t['ai']}" for t in prior_turns)
    user = (
        f"Your original message:\nSubject: {subject}\n{original_message[:1200]}\n\n"
        + (f"Conversation so far:\n{history}\n\n" if history else "")
        + f"The AI's latest question:\n{ai_question}\n\n"
        "Your honest reply as the candidate:"
    )
    raw = gateway_chat(SIMULATE_SYSTEM, user, max_tokens=200)
    try:
        return parse_json_response(raw)["reply"].strip()
    except Exception:
        return raw.strip()


AUTO_ACK_MARKER = "Our Customer Service team will review your email and get back to you"

JUDGE_SYSTEM = (
    "You are grading a candidate-support helpdesk AI's final draft reply, "
    "for a recruitment assessment company (Dragnet Solutions). You are "
    "given: the KB excerpts the AI actually retrieved and was supposed to "
    "answer from, the AI's draft, and the real historical officer reply "
    "(which may just be a generic auto-acknowledgment with no real fix --"
    " this is flagged for you explicitly below; when it's flagged, it is "
    "NOT usable as a comparison and you must judge factual_alignment and "
    "hallucination ONLY against the KB excerpts, not against it).\n\n"
    "Score on 4 axes, 1-5 each:\n"
    "- factual_alignment: PRIMARILY, does the AI's draft accurately reflect "
    "what the KB excerpts actually say (not vaguer, not stronger, not "
    "different)? If the historical reply is substantive (not flagged as "
    "auto-ack-only), secondarily check it's consistent with that too, but "
    "the KB excerpts are the ground truth, not the historical reply.\n"
    "- hallucination: does the AI's draft state any specific fact, link, "
    "step, date, or promise that is NOT present in the KB excerpts shown? "
    "Quote-check this directly against the excerpt text. 5 = every "
    "concrete claim traces to an excerpt, 1 = concrete claims invented.\n"
    "- escalation_correctness: when the AI escalated, was that the right "
    "call given the excerpts/conversation? When it answered, was "
    "answering (vs. escalating) appropriate?\n"
    "- tone: warm, professional, appropriately concise?\n\n"
    'Respond with ONLY JSON: {"factual_alignment": 1-5, "hallucination": '
    '1-5, "escalation_correctness": 1-5, "tone": 1-5, "notes": "one '
    'sentence, and if hallucination < 4 name the specific unsupported claim"}'
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


def load_ground_truth_tools():
    """Same mining approach as the officer-review cue candidates: a ticket
    counts as ground-truth-known only if exactly one of the 3 tools is
    unambiguously mentioned somewhere in its real thread."""
    truth = {}
    with open(DATA_DIR / "tickets.jsonl") as f:
        for line in f:
            d = json.loads(line)
            tid = d["ticket_id"]
            full_text = (d.get("subject") or "") + " " + " ".join(m.get("text", "") for m in d.get("messages", []))
            low = full_text.lower()
            mentioned = {t for t in TOOLS if t.lower() in low}
            if "fot.com.ng" in low:
                mentioned.add("FOT")
            if len(mentioned) == 1:
                truth[tid] = next(iter(mentioned))
    return truth


def run_ticket(ticket, ground_truth):
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    try:
        graph = graph_module.build_graph(SqliteSaver(connection))
        config = {"configurable": {"thread_id": ticket["ticket_id"]}}

        first_candidate = next(m for m in ticket["messages"] if m["role"] == "candidate")
        original_text = first_candidate["text"]
        history = [{"id": first_candidate["id"], "role": "candidate", "text": original_text}]
        prior_turns = []
        simulated_platform_mentions = []
        turns_log = []
        final_result = None

        latest_id, latest_text = first_candidate["id"], original_text
        for turn_index in range(MAX_SIMULATED_TURNS):
            state = {
                "event_id": latest_id, "latest_id": latest_id, "latest_text": latest_text,
                "subject": ticket["subject"], "messages": list(history),
                "attachment_notes": [], "sentiment": None, "channel": ticket["channel"],
                "candidate_name": None, "registry": REGISTRY,
            }
            result = graph.invoke(state, config)
            action = result.get("decision", {}).get("action")
            turns_log.append({"turn": turn_index, "action": action, "rule": result.get("decision", {}).get("rule")})
            final_result = result

            if action not in ("ask_clarification", "request_attachment"):
                break

            ai_question = result.get("reply") or ""
            simulated_reply = simulate_candidate_reply(ticket["subject"], original_text, ai_question, prior_turns)
            turns_log[-1]["ai_question"] = ai_question
            turns_log[-1]["simulated_reply"] = simulated_reply
            prior_turns.append({"candidate": original_text if turn_index == 0 else "(see above)", "ai": ai_question})
            for tool in TOOLS:
                if tool.lower() in simulated_reply.lower():
                    simulated_platform_mentions.append(tool)

            synthetic_id = f"{ticket['ticket_id']}-sim-{turn_index}"
            history.append({"id": synthetic_id, "role": "officer", "text": ai_question})
            history.append({"id": synthetic_id + "-r", "role": "candidate", "text": simulated_reply})
            latest_id, latest_text = synthetic_id + "-r", simulated_reply

        real_officer_texts = [m["text"] for m in ticket["messages"] if m["role"] == "officer"]
        real_final_answer = real_officer_texts[-1] if real_officer_texts else "(no officer reply on file)"
        real_answer_is_auto_ack_only = all(AUTO_ACK_MARKER in t for t in real_officer_texts) if real_officer_texts else True

        final_action = final_result.get("decision", {}).get("action") if final_result else None
        final_reply = final_result.get("reply") if final_result else None
        final_chunks = final_result.get("chunks") if final_result else None
        resolved = final_action == "draft_reply" and bool(final_reply)

        scores = None
        if resolved:
            chunks_text = "\n\n---\n\n".join(
                f"[{c.get('heading', c.get('source', 'excerpt'))}]\n{c.get('text', '')}"
                for c in (final_chunks or [])
            ) or "(no chunks recorded)"
            ack_flag = (
                "FLAGGED: this is ONLY the generic auto-acknowledgment template -- "
                "not usable as a comparison, judge against the KB excerpts only."
                if real_answer_is_auto_ack_only else
                "This is a substantive historical reply, usable as secondary comparison."
            )
            judge_user = (
                f"Candidate's original message:\n{original_text[:1200]}\n\n"
                f"KB excerpts the AI retrieved and was supposed to answer from:\n{chunks_text[:2500]}\n\n"
                f"AI's final draft:\n{final_reply[:1500]}\n\n"
                f"Real historical officer reply ({ack_flag}):\n{real_final_answer[:1500]}"
            )
            try:
                raw = gateway_chat(JUDGE_SYSTEM, judge_user, max_tokens=350)
                scores = parse_json_response(raw)
            except Exception as e:
                scores = {"factual_alignment": None, "hallucination": None,
                          "escalation_correctness": None, "tone": None,
                          "notes": f"judge error: {e}"}

        truth_tool = ground_truth.get(ticket["ticket_id"])
        simulator_matched_truth = None
        if truth_tool and simulated_platform_mentions:
            simulator_matched_truth = truth_tool in simulated_platform_mentions

        return {
            "ticket_id": ticket["ticket_id"],
            "ticket_number": ticket["ticket_number"],
            "turns_used": len(turns_log),
            "final_action": final_action,
            "final_rule": final_result.get("decision", {}).get("rule") if final_result else None,
            "resolved": bool(resolved),
            "turns": turns_log,
            "scores": scores,
            "final_reply_text": final_reply if resolved else None,
            "final_chunks": final_chunks if resolved else None,
            "real_answer_is_auto_ack_only": real_answer_is_auto_ack_only,
            "ground_truth_tool": truth_tool,
            "simulated_platform_mentions": simulated_platform_mentions,
            "simulator_matched_truth": simulator_matched_truth,
        }
    finally:
        connection.close()


def main():
    ground_truth = load_ground_truth_tools()
    print(f"tickets with a known ground-truth platform: {len(ground_truth)}")

    done_ids = set()
    if OUT_PATH.exists():
        with open(OUT_PATH) as f:
            for line in f:
                d = json.loads(line)
                if "ticket_id" in d:
                    done_ids.add(d["ticket_id"])
        print(f"resuming, {len(done_ids)} already evaluated")

    tickets = [t for t in load_tickets() if t["ticket_id"] not in done_ids]
    print(f"evaluating {len(tickets)} tickets (simulated candidate, max {MAX_SIMULATED_TURNS} turns each)...")

    with open(OUT_PATH, "a") as out_f:
        # Lowered from 6 (2026-09-23): the inference gateway is a single-
        # process, single-worker uvicorn instance behind nginx/Cloudflare,
        # shared with real production traffic -- heavy concurrent eval load
        # was very likely contributing to the connection-reset/522/525
        # instability seen tonight, not just receiving it.
        with ThreadPoolExecutor(max_workers=2) as ex:
            futures = [ex.submit(run_ticket, t, ground_truth) for t in tickets]
            for i, fut in enumerate(futures):
                try:
                    result = fut.result()
                    out_f.write(json.dumps(result) + "\n")
                    out_f.flush()
                except Exception as e:
                    out_f.write(json.dumps({"error": str(e)}) + "\n")
                    out_f.flush()
                if i % 25 == 0:
                    print(f"  {i}/{len(tickets)}")

    print("\nSimulated conversation eval complete.")


if __name__ == "__main__":
    main()
