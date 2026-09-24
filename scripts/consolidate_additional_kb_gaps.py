"""Merge near-duplicate entries in additional_gap_candidates.json.

find_additional_kb_gaps.py used too tight a clustering threshold (0.86),
so the same real topic (e.g. "confirm my presentation slide submission")
came out as several separate entries instead of one. This re-clusters
the 267 raw candidates with a looser threshold and merges each group into
one canonical Q&A via the gateway, summing cluster_size across whatever
merged into it.
"""

import json
import re
import time
from pathlib import Path

import httpx
import numpy as np

from app.core.config import get_settings
from app.services.helpdesk_kb_service import embed_text

IN_PATH = Path("data/helpdesk-additional-kb-gaps/additional_gap_candidates.json")
OUT_PATH = Path("data/helpdesk-additional-kb-gaps/additional_gap_candidates_consolidated.json")
MERGE_THRESHOLD = 0.80


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


MERGE_SYSTEM = (
    "You are merging several near-duplicate FAQ entries (same underlying "
    "candidate question, worded differently) into ONE canonical entry for "
    "a knowledge base. Pick the clearest phrasing, and write one answer "
    "grounded in what the given answers actually say -- don't invent "
    "anything new.\n\n"
    'Respond with ONLY JSON: {"question": "...", "answer": "...", '
    '"category": "short topic label"}'
)


def merge_group(items):
    if len(items) == 1:
        return items[0]
    listing = "\n\n---\n\n".join(
        f"Q: {it['question']}\nA: {it['answer']}" for it in items
    )
    raw = gateway_chat(MERGE_SYSTEM, listing, max_tokens=500)
    parsed = parse_json_response(raw)
    total_cluster_size = sum(it["cluster_size"] for it in items)
    confidences = [it["confidence"] for it in items]
    confidence = "high" if "high" in confidences else ("medium" if "medium" in confidences else "low")
    sample_ids = []
    for it in items:
        sample_ids.extend(it.get("sample_ticket_ids", []))
    return {
        "question": parsed["question"],
        "answer": parsed["answer"],
        "category": parsed.get("category", items[0]["category"]),
        "confidence": confidence,
        "cluster_size": total_cluster_size,
        "sample_ticket_ids": sample_ids[:5],
        "merged_from": len(items),
    }


def main():
    items = json.load(open(IN_PATH))
    print(f"loaded {len(items)} raw candidates")

    print("embedding questions...")
    vecs = np.array([embed_text(it["question"]) for it in items], dtype=np.float32)
    vecs = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)

    n = len(items)
    sims = vecs @ vecs.T
    assigned = np.full(n, -1, dtype=np.int64)
    groups = []
    for i in range(n):
        if assigned[i] != -1:
            continue
        idx = len(groups)
        groups.append([i])
        assigned[i] = idx
        matches = np.where((sims[i] >= MERGE_THRESHOLD) & (assigned == -1))[0]
        for j in matches:
            if j > i:
                groups[idx].append(int(j))
                assigned[j] = idx
    print(f"{len(groups)} groups after re-clustering (threshold {MERGE_THRESHOLD}) from {n} raw candidates")

    merged_count = sum(1 for g in groups if len(g) > 1)
    print(f"{merged_count} groups had duplicates merged")

    print("merging each group via gateway...")
    final = []
    for i, group in enumerate(groups):
        group_items = [items[idx] for idx in group]
        result = merge_group(group_items)
        final.append(result)
        if i % 20 == 0:
            print(f"  {i}/{len(groups)}")

    final.sort(key=lambda x: -x["cluster_size"])
    json.dump(final, open(OUT_PATH, "w"), indent=2)
    print(f"\nWrote {len(final)} consolidated entries to {OUT_PATH}")


if __name__ == "__main__":
    main()
