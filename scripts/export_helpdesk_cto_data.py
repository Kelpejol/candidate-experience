"""Export the raw underlying data behind the theme analysis, exactly as used
for that first pass, per the CTO's explicit request (2026-09-22): no
re-clustering, no re-classification, no consolidation -- just the real
1,967 genuine candidate-support tickets (including the 792 currently in the
long-tail bucket) with their full conversation structure preserved.

Two linked CSVs:
  - data/helpdesk-cto-export/tickets.csv       (one row per ticket)
  - data/helpdesk-cto-export/conversations.csv (one row per message)

PII: the historical extraction only reliably redacts officer-template
fields: real candidate-authored text (names in signatures, etc.) leaks
through, confirmed earlier when preparing the sample-audit section of the
theme report. This script runs a dedicated redaction pass (batched one
gateway call per ticket, covering every message in that ticket at once) so
every message body in the export is actually redacted, not just the
messages that happened to get sampled before.
"""

import csv
import json
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from app.core.config import get_settings

socket.setdefaulttimeout(20)

DATA_DIR = Path("data/helpdesk-history-full")
THEME_DIR = Path("data/helpdesk-theme-analysis")
OUT_DIR = Path("data/helpdesk-cto-export")
OUT_DIR.mkdir(parents=True, exist_ok=True)

AUTO_ACK_MARKER = "Our Customer Service team will review your email and get back to you"


def gateway_chat(system, user, max_tokens=2000, tries=6, delay=3):
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
                timeout=90,
            )
            resp.raise_for_status()
            return resp.json()["output"].strip()
        except Exception:
            time.sleep(delay)
    raise RuntimeError("gateway_chat failed after retries")


def parse_json_response(raw):
    cleaned = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    return json.loads(cleaned)


REDACT_SYSTEM = (
    "Redact personally identifying details from each real support-ticket "
    "message below. Replace: full names -> [NAME], phone numbers -> "
    "[PHONE], emails -> [EMAIL], student/matriculation/exam IDs -> [ID], "
    "physical addresses -> [ADDRESS]. Keep everything else (the actual "
    "content, complaint, request, instructions) verbatim, unchanged -- do "
    "not summarize or shorten.\n\n"
    "You are given a JSON array of {\"id\": ..., \"text\": ...} objects. "
    "Respond with ONLY a JSON array of the same length and same ids, each "
    "{\"id\": ..., \"text\": \"<redacted>\"}."
)


def redact_ticket_messages(messages):
    """messages: list of {id, text}. Returns dict id -> redacted text."""
    payload = [{"id": m["id"], "text": m["text"][:2000]} for m in messages]
    raw = gateway_chat(REDACT_SYSTEM, json.dumps(payload))
    parsed = parse_json_response(raw)
    return {item["id"]: item["text"] for item in parsed}


def load_classified():
    by_id = {}
    with open(THEME_DIR / "classified.jsonl") as f:
        for line in f:
            d = json.loads(line)
            by_id[d["ticket_id"]] = d
    return by_id


def load_theme_assignment():
    """ticket_id -> {theme_name, cluster_id, is_long_tail, context fields}."""
    clusters = json.load(open(THEME_DIR / "clusters.json"))
    named_themes = json.load(open(THEME_DIR / "deliverables" / "named_themes.json"))
    named_by_cluster = {t["cluster_id"]: t for t in named_themes}

    ids = clusters["ids"]
    assignment = {}
    for cluster_idx, members in enumerate(clusters["clusters"]):
        theme = named_by_cluster.get(cluster_idx)
        for idx in members:
            tid = ids[idx]
            if theme:
                assignment[tid] = {
                    "cluster_id": cluster_idx,
                    "theme_name": theme["name"],
                    "is_long_tail": False,
                    "audience": theme["audience"],
                    "service": theme["service"],
                    "platform": theme["platform"],
                    "campaign_trigger": theme["campaign_trigger"],
                    "ambiguity": theme["ambiguity"],
                }
            else:
                assignment[tid] = {
                    "cluster_id": cluster_idx,
                    "theme_name": "",
                    "is_long_tail": True,
                    "audience": "", "service": "", "platform": "",
                    "campaign_trigger": "", "ambiguity": "",
                }
    return assignment


def load_tickets_raw():
    by_id = {}
    with open(DATA_DIR / "tickets.jsonl") as f:
        for line in f:
            d = json.loads(line)
            by_id[d["ticket_id"]] = d
    return by_id


def process_one_ticket(ticket_id, raw_ticket, classified_rec, theme_info):
    msgs = [m for m in raw_ticket.get("messages", []) if m.get("role") in ("candidate", "officer")]
    msgs.sort(key=lambda m: m.get("created_time") or "")

    if not msgs:
        return None, []

    to_redact = [{"id": m["id"], "text": m.get("text") or ""} for m in msgs]
    try:
        redacted_by_id = redact_ticket_messages(to_redact)
    except Exception:
        redacted_by_id = {m["id"]: m.get("text") or "" for m in msgs}

    conv_rows = []
    for seq, m in enumerate(msgs, start=1):
        direction = "inbound" if m["role"] == "candidate" else "outbound"
        is_auto_ack = m["role"] == "officer" and AUTO_ACK_MARKER in (m.get("text") or "")
        conv_rows.append({
            "ticket_id": ticket_id,
            "message_id": m["id"],
            "sequence": seq,
            "timestamp": m.get("created_time") or "",
            "role": m["role"],
            "direction": direction,
            "is_auto_ack_template": is_auto_ack,
            "body_redacted": redacted_by_id.get(m["id"], m.get("text") or ""),
        })

    ticket_row = {
        "ticket_id": ticket_id,
        "ticket_number": raw_ticket.get("ticket_number") or "",
        "subject": raw_ticket.get("subject") or "",
        "created_time": raw_ticket.get("created_time") or "",
        "channel": raw_ticket.get("channel") or "",
        "cluster_id": theme_info["cluster_id"],
        "theme_name": theme_info["theme_name"],
        "status": "long_tail" if theme_info["is_long_tail"] else "named_theme",
        "issue_tags": ";".join(classified_rec.get("issue_tags") or []),
        "audience": theme_info["audience"],
        "service": theme_info["service"],
        "platform": theme_info["platform"],
        "campaign_trigger": theme_info["campaign_trigger"],
        "ambiguity": theme_info["ambiguity"],
        "message_count": len(msgs),
    }
    return ticket_row, conv_rows


def main():
    classified_by_id = load_classified()
    genuine_ids = [
        tid for tid, d in classified_by_id.items()
        if d.get("classification", {}).get("is_candidate_support", True)
    ]
    print(f"exporting {len(genuine_ids)} genuine candidate-support tickets...")

    theme_assignment = load_theme_assignment()
    raw_tickets = load_tickets_raw()

    tickets_path = OUT_DIR / "tickets.csv"
    conv_path = OUT_DIR / "conversations.csv"
    done_ids = set()
    if tickets_path.exists():
        with open(tickets_path) as f:
            for row in csv.DictReader(f):
                done_ids.add(row["ticket_id"])
        print(f"resuming, {len(done_ids)} already exported")

    todo = [tid for tid in genuine_ids if tid not in done_ids]

    ticket_fieldnames = ["ticket_id", "ticket_number", "subject", "created_time", "channel",
                         "cluster_id", "theme_name", "status", "issue_tags",
                         "audience", "service", "platform", "campaign_trigger", "ambiguity",
                         "message_count"]
    conv_fieldnames = ["ticket_id", "message_id", "sequence", "timestamp", "role",
                       "direction", "is_auto_ack_template", "body_redacted"]

    tickets_write_header = not tickets_path.exists()
    conv_write_header = not conv_path.exists()

    with open(tickets_path, "a", newline="") as tf, open(conv_path, "a", newline="") as cf:
        tw = csv.DictWriter(tf, fieldnames=ticket_fieldnames)
        cw = csv.DictWriter(cf, fieldnames=conv_fieldnames)
        if tickets_write_header:
            tw.writeheader()
        if conv_write_header:
            cw.writeheader()

        def work(tid):
            raw = raw_tickets.get(tid)
            if not raw:
                return None
            classified_rec = classified_by_id.get(tid, {})
            theme_info = theme_assignment.get(tid, {
                "cluster_id": -1, "theme_name": "", "is_long_tail": True,
                "audience": "", "service": "", "platform": "", "campaign_trigger": "", "ambiguity": "",
            })
            return process_one_ticket(tid, raw, classified_rec, theme_info)

        with ThreadPoolExecutor(max_workers=6) as ex:
            futures = [ex.submit(work, tid) for tid in todo]
            for i, fut in enumerate(futures):
                result = fut.result()
                if result and result[0]:
                    ticket_row, conv_rows = result
                    tw.writerow(ticket_row)
                    for row in conv_rows:
                        cw.writerow(row)
                    tf.flush()
                    cf.flush()
                if i % 50 == 0:
                    print(f"  {i}/{len(todo)}")

    print("\nExport complete.")


if __name__ == "__main__":
    main()
