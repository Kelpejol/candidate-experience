"""Second-pass analysis over the completed historical extraction
(data/helpdesk-history-full/) to produce the CTO's 7 requested deliverables.

Pipeline:
  1. Build one "initial issue" record per ticket (first Q&A pair, or the
     first candidate message for tickets with no answered pair).
  2. Classify each record as a genuine candidate-support issue, or an
     excluded category (spam/sales/job-application/personal/vendor/
     insufficient-text) — via the internal inference gateway, in parallel.
  3. Embed every classified record's text, cluster by cosine similarity
     (no sklearn available; plain numpy, same approach used for the KB gap
     analysis earlier this project).
  4. Name each cluster via the gateway, using real sampled examples.
  5. Rank clusters into the top themes, merging near-duplicate names.
  6. For each top theme: theme-to-context mapping (LLM synthesis).
  7. For high-volume ambiguous themes: disambiguation/routing design.
  8. Emit all 7 deliverables as real files under data/helpdesk-theme-analysis/.

Resumable: every expensive stage writes its own checkpoint file and skips
re-work already done, matching the pattern established for prior
long-running Zoho/gateway work in this project (real, repeated network
flakiness against both Zoho and the inference gateway).
"""

import json
import re
import socket
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np

socket.setdefaulttimeout(20)

from app.core.config import get_settings

DATA_DIR = Path("data/helpdesk-history-full")
OUT_DIR = Path("data/helpdesk-theme-analysis")
OUT_DIR.mkdir(parents=True, exist_ok=True)

EXCLUDE_CATEGORIES = [
    "spam_or_marketing",
    "sales_or_business_inquiry",
    "job_application_to_dragnet",
    "personal_or_colleague_outreach",
    "vendor_or_partnership_outreach",
    "insufficient_information",
    "non_candidate_operational",
]


def gateway_chat(system, user, max_tokens=500, tries=6, delay=3):
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
                    "temperature": 0.1,
                },
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json()["output"].strip()
        except Exception:
            time.sleep(delay)
    raise RuntimeError("gateway_chat failed after retries")


def gateway_embed(text, tries=6, delay=3):
    settings = get_settings()
    for i in range(tries):
        try:
            resp = httpx.post(
                f"{settings.inference_base_url}/embed",
                headers={"Authorization": f"Bearer {settings.inference_api_key}"},
                json={"text": text[:4000]},
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json()["embedding"]
        except Exception:
            time.sleep(delay)
    raise RuntimeError("gateway_embed failed after retries")


def strip_pii_tokens(text):
    return re.sub(r"\[(NAME|STUDENT_ID|EMAIL|PHONE)\]", "", text)


# ---------------------------------------------------------------------------
# Stage 1: build one initial-issue record per ticket
# ---------------------------------------------------------------------------

def build_initial_issues():
    out_path = OUT_DIR / "initial_issues.jsonl"
    if out_path.exists():
        print(f"[1] {out_path} already exists, skipping")
        return out_path

    first_qa_by_ticket = {}
    with open(DATA_DIR / "qa_pairs.jsonl") as f:
        for line in f:
            d = json.loads(line)
            tid = d["ticket_id"]
            if tid not in first_qa_by_ticket:
                first_qa_by_ticket[tid] = d

    qa_ticket_ids = set(first_qa_by_ticket)
    zero_qa_records = []
    with open(DATA_DIR / "tickets.jsonl") as f:
        for line in f:
            t = json.loads(line)
            if t["ticket_id"] in qa_ticket_ids:
                continue
            candidate_msgs = [
                m for m in t.get("messages", []) if m.get("direction") == "in"
            ]
            text = ""
            if candidate_msgs:
                text = candidate_msgs[0].get("content") or candidate_msgs[0].get("summary") or ""
            zero_qa_records.append({
                "ticket_id": t["ticket_id"],
                "ticket_number": t.get("ticket_number"),
                "subject": t.get("subject") or "",
                "candidate_text": strip_pii_tokens(text)[:3000],
                "issue_tags": [],
                "has_qa": False,
            })

    with open(out_path, "w") as f:
        for tid, d in first_qa_by_ticket.items():
            f.write(json.dumps({
                "ticket_id": tid,
                "ticket_number": d.get("ticket_number"),
                "subject": d.get("subject") or "",
                "candidate_text": strip_pii_tokens(d.get("candidate_text") or "")[:3000],
                "issue_tags": d.get("issue_tags") or [],
                "has_qa": True,
            }) + "\n")
        for d in zero_qa_records:
            f.write(json.dumps(d) + "\n")

    print(f"[1] wrote {len(first_qa_by_ticket) + len(zero_qa_records)} initial-issue records to {out_path}")
    return out_path


# ---------------------------------------------------------------------------
# Stage 2: classify candidate-support vs excluded
# ---------------------------------------------------------------------------

CLASSIFY_SYSTEM = (
    "You triage inbound messages for a recruitment-assessment company's "
    "candidate support inbox (Dragnet Solutions, Nigeria — proctored tests, "
    "scholarship verification, recruitment campaigns).\n\n"
    "Decide: is this a genuine CANDIDATE asking for help with their own "
    "assessment/application/scholarship, or something else entirely?\n\n"
    "If it is NOT a genuine candidate support request, classify it into "
    "exactly one of: spam_or_marketing, sales_or_business_inquiry, "
    "job_application_to_dragnet, personal_or_colleague_outreach, "
    "vendor_or_partnership_outreach, insufficient_information (too short/"
    "unclear to tell), non_candidate_operational (internal/system notice, "
    "not a real inbound request).\n\n"
    'Respond with ONLY JSON: {"is_candidate_support": true/false, '
    '"exclude_reason": null or one of the categories above, '
    '"confidence": "high"/"medium"/"low"}'
)


def classify_one(record):
    text = f"Subject: {record['subject']}\n\n{record['candidate_text'][:1500]}"
    try:
        raw = gateway_chat(CLASSIFY_SYSTEM, text, max_tokens=120)
        cleaned = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        parsed = json.loads(cleaned)
    except Exception as e:
        parsed = {"is_candidate_support": True, "exclude_reason": None, "confidence": "low", "_error": str(e)[:200]}
    return {**record, "classification": parsed}


def classify_all(initial_issues_path):
    out_path = OUT_DIR / "classified.jsonl"
    done_ids = set()
    if out_path.exists():
        with open(out_path) as f:
            for line in f:
                done_ids.add(json.loads(line)["ticket_id"])
        print(f"[2] resuming, {len(done_ids)} already classified")

    records = []
    with open(initial_issues_path) as f:
        for line in f:
            d = json.loads(line)
            if d["ticket_id"] not in done_ids:
                records.append(d)

    if not records:
        print("[2] all classified already")
        return out_path

    print(f"[2] classifying {len(records)} records...")
    with open(out_path, "a") as out_f:
        with ThreadPoolExecutor(max_workers=8) as ex:
            for i, result in enumerate(ex.map(classify_one, records)):
                out_f.write(json.dumps(result) + "\n")
                out_f.flush()
                if i % 100 == 0:
                    print(f"[2]   {i}/{len(records)}")

    print(f"[2] done classifying")
    return out_path


# ---------------------------------------------------------------------------
# Stage 3: embed classified candidate-support records
# ---------------------------------------------------------------------------

def embed_one(record):
    text = f"{record['subject']}\n{record['candidate_text'][:1000]}"
    return record["ticket_id"], gateway_embed(text)


def embed_all(classified_path):
    npz_path = OUT_DIR / "embeddings.npz"
    if npz_path.exists():
        print(f"[3] {npz_path} already exists, skipping")
        return npz_path

    support_records = []
    with open(classified_path) as f:
        for line in f:
            d = json.loads(line)
            c = d.get("classification", {})
            if c.get("is_candidate_support", True):
                support_records.append(d)

    print(f"[3] embedding {len(support_records)} candidate-support records...")
    ids, vecs = [], []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for i, (tid, emb) in enumerate(ex.map(embed_one, support_records)):
            ids.append(tid)
            vecs.append(emb)
            if i % 200 == 0:
                print(f"[3]   {i}/{len(support_records)}")

    arr = np.array(vecs)
    np.savez(npz_path, ids=np.array(ids), vecs=arr)
    print(f"[3] wrote {len(ids)} embeddings to {npz_path}")
    return npz_path


# ---------------------------------------------------------------------------
# Stage 4: cluster by cosine similarity
# ---------------------------------------------------------------------------

def cluster_all(npz_path, threshold=0.80):
    out_path = OUT_DIR / "clusters.json"
    if out_path.exists():
        print(f"[4] {out_path} already exists, skipping")
        return out_path

    data = np.load(npz_path, allow_pickle=True)
    ids = data["ids"]
    vecs = data["vecs"].astype(np.float32)
    vecs = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)

    n = len(ids)
    print(f"[4] clustering {n} items (threshold={threshold})...")

    assigned = np.full(n, -1, dtype=np.int64)
    clusters = []
    block = 500
    for start in range(0, n, block):
        end = min(start + block, n)
        sims = vecs[start:end] @ vecs.T  # (block, n)
        for local_i, i in enumerate(range(start, end)):
            if assigned[i] != -1:
                continue
            cluster_idx = len(clusters)
            clusters.append([i])
            assigned[i] = cluster_idx
            row = sims[local_i]
            matches = np.where((row >= threshold) & (assigned == -1))[0]
            for j in matches:
                if j > i:
                    clusters[cluster_idx].append(int(j))
                    assigned[j] = cluster_idx
        print(f"[4]   processed {end}/{n}, {len(clusters)} clusters so far")

    result = {"ids": ids.tolist(), "clusters": [[int(x) for x in c] for c in clusters]}
    json.dump(result, open(out_path, "w"))
    print(f"[4] {len(clusters)} raw clusters from {n} items")
    return out_path


if __name__ == "__main__":
    p1 = build_initial_issues()
    p2 = classify_all(p1)
    p3 = embed_all(p2)
    p4 = cluster_all(p3)
    print("\nStages 1-4 complete. Run analyze_helpdesk_themes_name.py next for cluster naming + deliverables.")
