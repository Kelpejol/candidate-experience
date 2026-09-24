"""Extend the 38-item KB-gap list by direct-checking the remaining real
historical questions against the CURRENT live KB.

Same methodology as the original 38 (2026-09-16): take a real candidate
question, run it through the actual production retrieve_grounding()
function, and keep only the ones the KB genuinely doesn't cover today.
No clustering/theming involved -- this is independent of the CTO's
disputed theme-taxonomy work and does not need his review.

Only ~1,155 of the 2,995 historical tickets were checked this way before;
this covers all genuine candidate-support tickets (per this week's
classification pass) and re-checks against the CURRENT KB (which has had
4 items added since the original 38 were found), then dedupes against
the existing 38 so nothing already surfaced shows up twice.
"""

import json
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np

from app.core.config import get_settings
from app.services.helpdesk_kb_service import embed_text, retrieve_grounding

socket.setdefaulttimeout(20)

DATA_DIR = Path("data/helpdesk-history-full")
THEME_DIR = Path("data/helpdesk-theme-analysis")
OUT_DIR = Path("data/helpdesk-additional-kb-gaps")
OUT_DIR.mkdir(parents=True, exist_ok=True)

AUTO_ACK_MARKER = "Our Customer Service team will review your email and get back to you"
CLUSTER_THRESHOLD = 0.86
DEDUPE_VS_38_THRESHOLD = 0.84

EXISTING_38_QUESTIONS = [
    "What should I do if my course of study or university is not listed in the scholastica portal while filling out my profile?",
    "What should I do if I encounter issues installing the Talview Secure Browser?",
    "What happens if I upload a selfie photo instead of a valid identification document during the ID verification step?",
    "Can I use plain sheets of paper for calculations during the test?",
    "What should I do if my CGPA has not been released yet but is required for a scholarship application?",
    "What should I do if the 'Next' button on the test platform is unresponsive or stuck?",
    "I submitted my application for the Julius Berger Nigeria Female Scholarship Scheme, but my actual course of study was not available in the course-selection field. What should I do to clarify this?",
    "Can the CIPM examination be taken on a Chromebook?",
    "What should I do if no interviewer joins my scheduled interview for a recruitment campaign?",
    "I am concerned because I have not received my payment for the scholarship cycle, even though others have. What should I do?",
    "What should I do if I want to upload multiple result documents, but the system only allows one document at a time?",
    "Can I use my next of kin's account number to register since I am underaged?",
    "What documents do I need to bring for the second-level screening?",
    "How and where can I apply for a scholarship?",
    "Should I delete my previous transcript or signed statement of result before uploading a new academic document for verification?",
    "What CGPA should I provide in my scholarship application if my current CGPA is incomplete due to pending semester results?",
    "Who should be listed as the academic referee for the JBN Scholarship application verification profile?",
    "Do I need to bring the originals of my credentials to the screening?",
    "Can I request an online interview if I reside outside Nigeria and am unable to attend the physical venue?",
    "Can I know the specific reasons why I was not shortlisted for a program or scholarship?",
    "What should I do if the Talview Secure Browser is not detecting my microphone during the hardware test?",
    "What should I do if I don't have a school ID for my application?",
    "How do I renew my Julius Berger Female Scholarship award?",
    "How can I delete my account if I no longer wish to use it?",
    "Will receiving additional educational financial assistance, such as a student loan, bursary, or sponsorship, affect my eligibility for the OML17 HCDT Scholarship?",
    "What measures are in place to detect cheating during assessments?",
    "Can I use an Android device or the Talview Secure Browser App to take my test?",
    "What should I do if my secure browser is loading but not opening the test page?",
    "What should I do if I encounter the \"Restricted Software Detected\" error during my test due to an application like Anydesk?",
    "I am having difficulty with the hardware test (audio) during the test setup. What should I do?",
    "What should I do if the secure browser download link is not working?",
    "What should I do if I do not have access to the test platform?",
    "What can I do if I am experiencing network issues in my area?",
    "What should I do if my exam software gets blocked by Microsoft Windows Smart App Control?",
    "What system requirements are needed for running the Talvie browser?",
    "Can the Exxon aptitude test be taken on a mobile phone, and what are the test schedule details?",
    "Can I use an external keyboard or external headphones for the assessment if my laptop's keyboard or microphone is not functional?",
    "Can I use an Android phone to take the test?",
]


def gateway_chat(system, user, max_tokens=500, tries=6, delay=3):
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


def load_genuine_ticket_ids():
    ids = set()
    with open(THEME_DIR / "classified.jsonl") as f:
        for line in f:
            d = json.loads(line)
            if d.get("classification", {}).get("is_candidate_support", True):
                ids.add(d["ticket_id"])
    return ids


def load_qa_pairs(genuine_ids):
    pairs = []
    with open(DATA_DIR / "qa_pairs.jsonl") as f:
        for line in f:
            d = json.loads(line)
            if d["ticket_id"] not in genuine_ids:
                continue
            if d.get("outcome") != "answered":
                continue
            answer = d.get("officer_answer") or ""
            if AUTO_ACK_MARKER in answer and len(answer) < 600:
                # Only the auto-ack, no real second reply -- nothing to
                # ground a canonical answer in.
                continue
            pairs.append(d)
    return pairs


def check_one(pair):
    query = f"{pair.get('subject', '')}\n{pair.get('candidate_text', '')}"[:2000]
    try:
        result = retrieve_grounding(query, k=3)
        return {
            "ticket_id": pair["ticket_id"],
            "ticket_number": pair.get("ticket_number"),
            "subject": pair.get("subject", ""),
            "candidate_text": pair.get("candidate_text", ""),
            "officer_answer": pair.get("officer_answer", ""),
            "grounded": result.grounded,
            "best_distance": result.best_distance,
        }
    except Exception as e:
        return {"ticket_id": pair["ticket_id"], "error": str(e)}


def main():
    print("Step 1: loading genuine candidate-support tickets...")
    genuine_ids = load_genuine_ticket_ids()
    print(f"  {len(genuine_ids)} genuine tickets")

    pairs = load_qa_pairs(genuine_ids)
    print(f"Step 2: {len(pairs)} qa_pairs with a real (non-auto-ack-only) officer answer")

    checked_path = OUT_DIR / "checked.jsonl"
    done_ids = set()
    if checked_path.exists():
        with open(checked_path) as f:
            for line in f:
                d = json.loads(line)
                done_ids.add((d.get("ticket_id"), d.get("subject", "")[:50]))

    todo = [p for p in pairs if (p["ticket_id"], p.get("subject", "")[:50]) not in done_ids]
    print(f"Step 3: checking {len(todo)} against the current live KB (resuming, {len(done_ids)} already done)...")

    with open(checked_path, "a") as out_f:
        with ThreadPoolExecutor(max_workers=2) as ex:
            for i, result in enumerate(ex.map(check_one, todo)):
                out_f.write(json.dumps(result) + "\n")
                out_f.flush()
                if i % 100 == 0:
                    print(f"  {i}/{len(todo)}")

    print("Step 3 complete.")

    print("Step 4: filtering to genuinely ungrounded (current gaps)...")
    checked = [json.loads(l) for l in open(checked_path)]
    checked = [c for c in checked if "error" not in c]
    gaps = [c for c in checked if not c.get("grounded")]
    print(f"  {len(gaps)} / {len(checked)} not currently grounded in the KB")

    if not gaps:
        print("No gaps found. Done.")
        return

    print("Step 5: embedding gap questions + existing 38 for clustering/dedup...")
    gap_vecs = []
    for g in gaps:
        text = f"{g['subject']}\n{g['candidate_text'][:1000]}"
        gap_vecs.append(embed_text(text))
    gap_vecs = np.array(gap_vecs, dtype=np.float32)
    gap_vecs = gap_vecs / np.linalg.norm(gap_vecs, axis=1, keepdims=True)

    existing_vecs = np.array([embed_text(q) for q in EXISTING_38_QUESTIONS], dtype=np.float32)
    existing_vecs = existing_vecs / np.linalg.norm(existing_vecs, axis=1, keepdims=True)

    print("Step 6: clustering gap questions by similarity...")
    n = len(gaps)
    assigned = np.full(n, -1, dtype=np.int64)
    clusters = []
    sims_all = gap_vecs @ gap_vecs.T
    for i in range(n):
        if assigned[i] != -1:
            continue
        cluster_idx = len(clusters)
        clusters.append([i])
        assigned[i] = cluster_idx
        row = sims_all[i]
        matches = np.where((row >= CLUSTER_THRESHOLD) & (assigned == -1))[0]
        for j in matches:
            if j > i:
                clusters[cluster_idx].append(int(j))
                assigned[j] = cluster_idx
    print(f"  {len(clusters)} raw clusters from {n} gap questions")

    print("Step 7: filtering out clusters already covered by the existing 38 (via similarity)...")
    dedupe_sims = gap_vecs @ existing_vecs.T
    max_sim_to_38 = dedupe_sims.max(axis=1)
    kept_clusters = []
    for members in clusters:
        rep_idx = members[0]
        if max_sim_to_38[rep_idx] >= DEDUPE_VS_38_THRESHOLD:
            continue
        kept_clusters.append(members)
    print(f"  {len(kept_clusters)} clusters remain after dedup (vs. {len(clusters)} raw)")

    print("Step 8: drafting canonical Q&A per cluster (grounded in real officer answers)...")
    DRAFT_SYSTEM = (
        "You draft a single generic FAQ entry for a candidate-support "
        "knowledge base, grounded ONLY in the real historical officer "
        "answers given below. Multiple real candidate messages and real "
        "officer replies for the same underlying question are shown. "
        "Produce ONE canonical question (phrased the way a candidate "
        "would ask it) and ONE answer, generalizing across the examples, "
        "using only what the officers actually said -- do not invent "
        "policy beyond that.\n\n"
        'Respond with ONLY JSON: {"question": "...", "answer": "...", '
        '"category": "short topic label", "confidence": "high/medium/low"}'
    )

    candidates = []
    for members in kept_clusters:
        examples = []
        for idx in members[:5]:
            g = gaps[idx]
            examples.append(f"Candidate: {g['subject']}\n{g['candidate_text'][:400]}\nOfficer: {g['officer_answer'][:400]}")
        user = "\n\n---\n\n".join(examples)
        try:
            raw = gateway_chat(DRAFT_SYSTEM, user, max_tokens=500)
            parsed = parse_json_response(raw)
        except Exception as e:
            parsed = {"question": None, "answer": None, "category": "Other", "confidence": "low", "_error": str(e)}
        if not parsed.get("question") or not parsed.get("answer"):
            continue
        candidates.append({
            "question": parsed["question"],
            "answer": parsed["answer"],
            "category": parsed.get("category", "Other"),
            "confidence": parsed.get("confidence", "low"),
            "cluster_size": len(members),
            "sample_ticket_ids": [gaps[idx]["ticket_id"] for idx in members[:5]],
        })
        print(f"  [{len(candidates)}] ({len(members)}x, {parsed.get('confidence')}) {parsed['question'][:70]}")

    out_path = OUT_DIR / "additional_gap_candidates.json"
    json.dump(candidates, open(out_path, "w"), indent=2)
    print(f"\nWrote {len(candidates)} additional gap candidates to {out_path}")


if __name__ == "__main__":
    main()
