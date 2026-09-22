"""Third-pass analysis: turn the raw clusters from analyze_helpdesk_themes.py
into the CTO's 7 named deliverables.

Scope decision (confirmed with user 2026-09-21): only the 28 clusters with
>=10 tickets get individually named/mapped as "themes." Everything smaller
(434 clusters, singletons up through size-9) is reported as one aggregated
long-tail bucket with a count and an issue-tag breakdown, not named
individually.

Pipeline:
  1. Name + context-map each top theme (LLM, using real sampled tickets).
  2. Rank themes by volume -> deliverable 2 (frequency report).
  3. Emit the context mapping alongside each theme -> deliverable 3.
  4. Design disambiguation/routing for ambiguous high-volume themes -> deliverable 4.
  5. Propose a KB structure from the named themes -> deliverable 5.
  6. Emit the excluded/long-tail log -> deliverable 6.
  7. Sample 1-2 real examples per top theme for audit -> deliverable 7.
  8. Deliverable 1 (volume/classification totals) is already known from stage 2.

All outputs land under data/helpdesk-theme-analysis/deliverables/.
"""

import json
import re
import socket
import time
from collections import Counter
from pathlib import Path

import httpx

socket.setdefaulttimeout(20)

from app.core.config import get_settings

DATA_DIR = Path("data/helpdesk-history-full")
OUT_DIR = Path("data/helpdesk-theme-analysis")
DELIV_DIR = OUT_DIR / "deliverables"
DELIV_DIR.mkdir(parents=True, exist_ok=True)

TOP_CLUSTER_MIN_SIZE = 10
SAMPLES_PER_THEME = 6
AUDIT_SAMPLES_PER_THEME = 1


def gateway_chat(system, user, max_tokens=700, tries=6, delay=3):
    settings = get_settings()
    for i in range(tries):
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


# ---------------------------------------------------------------------------
# Load everything
# ---------------------------------------------------------------------------

def load_classified():
    by_id = {}
    with open(OUT_DIR / "classified.jsonl") as f:
        for line in f:
            d = json.loads(line)
            by_id[d["ticket_id"]] = d
    return by_id


def load_qa_by_ticket():
    by_id = {}
    with open(DATA_DIR / "qa_pairs.jsonl") as f:
        for line in f:
            d = json.loads(line)
            tid = d["ticket_id"]
            if tid not in by_id:
                by_id[tid] = d
    return by_id


def load_clusters():
    return json.load(open(OUT_DIR / "clusters.json"))


# ---------------------------------------------------------------------------
# Stage A: name + context-map top themes
# ---------------------------------------------------------------------------

CONTEXT_SYSTEM = (
    "You analyze recurring candidate-support themes for a recruitment "
    "assessment company (Dragnet Solutions, Nigeria — proctored online tests, "
    "scholarship verification, recruitment campaigns). You are given several "
    "real (PII-redacted) support messages that were automatically grouped "
    "together as similar. Produce ONE theme description covering all of them.\n\n"
    "Respond with ONLY JSON, no markdown fences:\n"
    "{\n"
    '  "name": "short theme name, 3-7 words",\n'
    '  "description": "1-2 sentence plain description of the problem",\n'
    '  "audience": "who is affected, e.g. exam candidates / scholarship applicants",\n'
    '  "goal": "what the person is trying to accomplish",\n'
    '  "service": "which Dragnet service/product this relates to",\n'
    '  "platform": "which system/tool is involved, e.g. proctoring app, portal, unspecified",\n'
    '  "campaign_trigger": "what typically triggers this, e.g. exam day camera check",\n'
    '  "surface": "channel this usually arrives on if inferable, else unspecified",\n'
    '  "ambiguity": "high, medium, or low - could this theme require different '
    'resolutions/routing depending on details not visible in the message alone?",\n'
    '  "ambiguity_reason": "why, in one sentence"\n'
    "}"
)


def name_theme(cluster_id, ticket_ids, classified_by_id):
    samples = []
    for tid in ticket_ids[:SAMPLES_PER_THEME]:
        rec = classified_by_id.get(tid)
        if not rec:
            continue
        samples.append(f"- Subject: {rec['subject']}\n  Message: {rec['candidate_text'][:500]}")
    user = f"{len(ticket_ids)} similar tickets in this group. Sample of {len(samples)}:\n\n" + "\n\n".join(samples)
    raw = gateway_chat(CONTEXT_SYSTEM, user)
    parsed = parse_json_response(raw)
    parsed["cluster_id"] = cluster_id
    parsed["ticket_count"] = len(ticket_ids)
    parsed["sample_ticket_ids"] = ticket_ids[:SAMPLES_PER_THEME]
    return parsed


def build_named_themes(clusters, classified_by_id):
    out_path = DELIV_DIR / "named_themes.json"
    if out_path.exists():
        print(f"[A] {out_path} already exists, skipping")
        return json.load(open(out_path))

    ids = clusters["ids"]
    cluster_lists = clusters["clusters"]
    indexed = [(i, [ids[idx] for idx in members]) for i, members in enumerate(cluster_lists)]
    top = sorted(indexed, key=lambda x: -len(x[1]))
    top = [c for c in top if len(c[1]) >= TOP_CLUSTER_MIN_SIZE]

    print(f"[A] naming {len(top)} top themes (>= {TOP_CLUSTER_MIN_SIZE} tickets each)...")
    themes = []
    for i, (cluster_id, ticket_ids) in enumerate(top):
        theme = name_theme(cluster_id, ticket_ids, classified_by_id)
        themes.append(theme)
        print(f"[A]   {i+1}/{len(top)}: {theme['name']} ({theme['ticket_count']} tickets)")

    json.dump(themes, open(out_path, "w"), indent=2)
    print(f"[A] wrote {len(themes)} named themes to {out_path}")
    return themes


# ---------------------------------------------------------------------------
# Stage B: deliverable 2 - ranked frequency report
# ---------------------------------------------------------------------------

def build_frequency_report(themes, total_support, total_all):
    out_path = DELIV_DIR / "01_02_volume_and_frequency_report.json"
    themed_total = sum(t["ticket_count"] for t in themes)
    rest = total_support - themed_total

    ranked = sorted(themes, key=lambda t: -t["ticket_count"])[:20]
    rows = [
        {
            "rank": i + 1,
            "name": t["name"],
            "ticket_count": t["ticket_count"],
            "pct_of_candidate_support": round(100 * t["ticket_count"] / total_support, 1),
            "pct_of_all_tickets": round(100 * t["ticket_count"] / total_all, 1),
        }
        for i, t in enumerate(ranked)
    ]

    report = {
        "deliverable_1_volume_and_classification": {
            "total_tickets": total_all,
            "candidate_support": total_support,
            "excluded": total_all - total_support,
        },
        "deliverable_2_top_20_themes": rows,
        "long_tail_summary": {
            "note": "Themes below account for tickets in clusters smaller than "
                     f"{TOP_CLUSTER_MIN_SIZE}, not individually named (see deliverable 6).",
            "ticket_count": rest,
            "pct_of_candidate_support": round(100 * rest / total_support, 1),
        },
    }
    json.dump(report, open(out_path, "w"), indent=2)
    print(f"[B] wrote {out_path}")
    return report


# ---------------------------------------------------------------------------
# Stage C: deliverable 3 already embedded in named_themes.json context fields;
# just re-emit as its own file for clarity.
# ---------------------------------------------------------------------------

def build_context_mapping(themes):
    out_path = DELIV_DIR / "03_theme_context_mapping.json"
    rows = [
        {
            "name": t["name"],
            "audience": t["audience"],
            "goal": t["goal"],
            "service": t["service"],
            "platform": t["platform"],
            "campaign_trigger": t["campaign_trigger"],
            "surface": t["surface"],
            "ambiguity": t["ambiguity"],
            "ambiguity_reason": t["ambiguity_reason"],
        }
        for t in sorted(themes, key=lambda t: -t["ticket_count"])
    ]
    json.dump(rows, open(out_path, "w"), indent=2)
    print(f"[C] wrote {out_path}")
    return rows


# ---------------------------------------------------------------------------
# Stage D: deliverable 4 - disambiguation/routing design for ambiguous,
# high-volume themes
# ---------------------------------------------------------------------------

ROUTING_SYSTEM = (
    "You design a disambiguation/routing rule for a candidate-support "
    "helpdesk AI at a recruitment-assessment company. You are given a theme "
    "that is both high-volume and ambiguous (the same surface complaint can "
    "need different resolutions depending on details not visible up front). "
    "Propose a concrete routing design: what clarifying question(s) the AI "
    "should ask (if any), what signals distinguish the sub-cases, and where "
    "each sub-case should route (auto-answer from KB, draft-for-officer, "
    "escalate-to-technical, escalate-to-scholarship-team, etc).\n\n"
    "Respond with ONLY JSON: {\"clarifying_questions\": [...], "
    '"sub_cases": [{"signal": "...", "routing": "..."}], '
    '"design_notes": "1-3 sentences"}'
)


def build_disambiguation_design(themes):
    out_path = DELIV_DIR / "04_disambiguation_routing_design.json"
    if out_path.exists():
        print(f"[D] {out_path} already exists, skipping")
        return json.load(open(out_path))

    candidates = [t for t in themes if t["ambiguity"] == "high"]
    candidates = sorted(candidates, key=lambda t: -t["ticket_count"])[:10]

    print(f"[D] designing routing for {len(candidates)} ambiguous high-volume themes...")
    designs = []
    for t in candidates:
        user = (
            f"Theme: {t['name']}\nDescription: {t['description']}\n"
            f"Ticket volume: {t['ticket_count']}\nWhy ambiguous: {t['ambiguity_reason']}"
        )
        raw = gateway_chat(ROUTING_SYSTEM, user)
        design = parse_json_response(raw)
        design["theme_name"] = t["name"]
        design["ticket_count"] = t["ticket_count"]
        designs.append(design)

    json.dump(designs, open(out_path, "w"), indent=2)
    print(f"[D] wrote {out_path}")
    return designs


# ---------------------------------------------------------------------------
# Stage E: deliverable 5 - KB structure proposal
# ---------------------------------------------------------------------------

KB_STRUCTURE_SYSTEM = (
    "You propose a knowledge-base document structure for a candidate-support "
    "helpdesk AI, given the ranked list of real recurring support themes "
    "below. Propose section headings that would organize KB content so each "
    "major theme has clear, findable coverage, plus note any themes that "
    "look like real gaps (no existing dedicated section likely exists).\n\n"
    "Respond with ONLY JSON: {\"proposed_sections\": [{\"heading\": \"...\", "
    '"covers_themes": ["..."], "priority": "high/medium/low"}], '
    '"notes": "1-3 sentences on structure rationale"}'
)


def build_kb_structure_proposal(themes):
    out_path = DELIV_DIR / "05_kb_structure_proposal.json"
    if out_path.exists():
        print(f"[E] {out_path} already exists, skipping")
        return json.load(open(out_path))

    ranked = sorted(themes, key=lambda t: -t["ticket_count"])
    listing = "\n".join(
        f"- {t['name']} ({t['ticket_count']} tickets): {t['description']}" for t in ranked
    )
    raw = gateway_chat(KB_STRUCTURE_SYSTEM, listing, max_tokens=1200)
    proposal = parse_json_response(raw)
    json.dump(proposal, open(out_path, "w"), indent=2)
    print(f"[E] wrote {out_path}")
    return proposal


# ---------------------------------------------------------------------------
# Stage F: deliverable 6 - excluded/unclassified log (excluded categories +
# long-tail clusters)
# ---------------------------------------------------------------------------

def build_excluded_log(classified_by_id, clusters, themes):
    out_path = DELIV_DIR / "06_excluded_and_long_tail_log.json"

    excluded_by_reason = {}
    for tid, rec in classified_by_id.items():
        c = rec.get("classification", {})
        if not c.get("is_candidate_support", True):
            reason = c.get("exclude_reason") or "unspecified"
            excluded_by_reason.setdefault(reason, []).append(
                {"ticket_id": tid, "subject": rec["subject"]}
            )

    excluded_summary = [
        {
            "reason": reason,
            "count": len(items),
            "example_tickets": items[:5],
        }
        for reason, items in sorted(excluded_by_reason.items(), key=lambda x: -len(x[1]))
    ]

    named_cluster_ids = {t["cluster_id"] for t in themes}
    ids = clusters["ids"]
    long_tail_tag_counts = Counter()
    long_tail_ticket_count = 0
    long_tail_examples = []
    for i, members in enumerate(clusters["clusters"]):
        if i in named_cluster_ids:
            continue
        long_tail_ticket_count += len(members)
        for idx in members:
            tid = ids[idx]
            rec = classified_by_id.get(tid, {})
            for tag in rec.get("issue_tags", []):
                long_tail_tag_counts[tag] += 1
            if len(long_tail_examples) < 15:
                long_tail_examples.append({"ticket_id": tid, "subject": rec.get("subject", "")})

    result = {
        "excluded_categories": excluded_summary,
        "long_tail": {
            "note": f"Clusters smaller than {TOP_CLUSTER_MIN_SIZE} tickets, not "
                     "individually named as themes.",
            "ticket_count": long_tail_ticket_count,
            "issue_tag_breakdown": long_tail_tag_counts.most_common(20),
            "example_tickets": long_tail_examples,
        },
    }
    json.dump(result, open(out_path, "w"), indent=2)
    print(f"[F] wrote {out_path}")
    return result


# ---------------------------------------------------------------------------
# Stage G: deliverable 7 - sample audit
# ---------------------------------------------------------------------------

def build_sample_audit(themes, classified_by_id, qa_by_ticket):
    out_path = DELIV_DIR / "07_sample_audit.json"
    ranked = sorted(themes, key=lambda t: -t["ticket_count"])

    samples = []
    for t in ranked:
        for tid in t["sample_ticket_ids"][:AUDIT_SAMPLES_PER_THEME]:
            rec = classified_by_id.get(tid)
            qa = qa_by_ticket.get(tid)
            if not rec:
                continue
            samples.append({
                "theme": t["name"],
                "ticket_id": tid,
                "ticket_number": rec.get("ticket_number"),
                "subject": rec.get("subject"),
                "candidate_message": rec.get("candidate_text"),
                "real_officer_answer": qa.get("officer_answer") if qa else None,
                "officer_answer_outcome": qa.get("outcome") if qa else None,
            })
        if len(samples) >= 20:
            break

    json.dump(samples[:20], open(out_path, "w"), indent=2)
    print(f"[G] wrote {len(samples[:20])} audit samples to {out_path}")
    return samples[:20]


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    classified_by_id = load_classified()
    qa_by_ticket = load_qa_by_ticket()
    clusters = load_clusters()

    total_all = len(classified_by_id)
    total_support = sum(
        1 for r in classified_by_id.values()
        if r.get("classification", {}).get("is_candidate_support", True)
    )

    themes = build_named_themes(clusters, classified_by_id)
    build_frequency_report(themes, total_support, total_all)
    build_context_mapping(themes)
    build_disambiguation_design(themes)
    build_kb_structure_proposal(themes)
    build_excluded_log(classified_by_id, clusters, themes)
    build_sample_audit(themes, classified_by_id, qa_by_ticket)

    print("\nAll 7 deliverables written to data/helpdesk-theme-analysis/deliverables/")
