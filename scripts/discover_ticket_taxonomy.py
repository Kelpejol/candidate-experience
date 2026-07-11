"""Discover a ticket taxonomy from real Zoho tickets (map/reduce via the gateway).

Stage 1 labels tickets concurrently: the inference gateway can take 10s+
per call, so sequential labeling of 1000 tickets would run for hours. A
semaphore caps parallelism to avoid flooding the gateway. Progress prints
flush immediately so `tail -f` on a redirected log shows live output.
"""

import asyncio
import json
from collections import Counter

import httpx

from app.core.config import get_settings
from app.integrations.zoho_desk_client import build_zoho_desk_client

settings = get_settings()
zoho = build_zoho_desk_client(settings)

PAGES = 20  # pages of 50 tickets
CONCURRENCY = 8

LABEL_SYSTEM_PROMPT = (
    "You label candidate support tickets for a recruitment assessment "
    "company. Respond with ONLY a 2-4 word snake_case label for the "
    "candidate's issue or request. No explanation."
)


async def label_ticket(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    index: int,
    total: int,
    subject: str,
) -> str:
    """Label one ticket via the gateway; return '__failed__' on any error."""
    async with semaphore:
        try:
            resp = await client.post(
                f"{settings.inference_base_url}/chat",
                headers={"Authorization": f"Bearer {settings.inference_api_key}"},
                json={
                    "messages": [
                        {"role": "system", "content": LABEL_SYSTEM_PROMPT},
                        {"role": "user", "content": subject},
                    ],
                    "max_tokens": 20,
                },
            )
            resp.raise_for_status()
            label = resp.json()["output"].strip().lower()
        except Exception as exc:
            print(f"{index}/{total}  FAILED: {exc}", flush=True)
            return "__failed__"

    print(f"{index}/{total}  {label}  <-  {subject[:60]}", flush=True)
    return label


async def main() -> None:
    # Stage 0: page through tickets (stop early if Zoho runs out)
    tickets = []
    for page in range(PAGES):
        batch = zoho.list_tickets(
            department_id=settings.zoho_department_id,
            limit=50,
            from_index=page * 50,
        )
        data = batch.get("data", [])
        tickets.extend(data)
        if len(data) < 50:
            break
    print(f"Collected {len(tickets)} tickets", flush=True)

    # Resolve subjects; fall back to the latest thread body for empty subjects
    subjects = []
    for ticket in tickets:
        subject = (ticket.get("subject") or "").strip()
        if not subject or subject == "(No Subject)":
            thread = zoho.get_latest_thread(str(ticket["id"]))
            subject = (thread.get("content") or thread.get("summary") or "")[:300]
        subjects.append(subject)

    # Stage 1 (map): label concurrently
    semaphore = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(timeout=120) as client:
        labels = await asyncio.gather(
            *[
                label_ticket(client, semaphore, i + 1, len(subjects), subject)
                for i, subject in enumerate(subjects)
            ]
        )

        counts = Counter(label for label in labels if label != "__failed__")
        failed = sum(1 for label in labels if label == "__failed__")
        with open("docs/ticket-taxonomy-discovery.json", "w") as f:
            json.dump(counts.most_common(), f, indent=1)
        print(f"\nLabeled {sum(counts.values())} tickets ({failed} failed)", flush=True)

        # Stage 2 (reduce): merge free labels into a final taxonomy
        resp = await client.post(
            f"{settings.inference_base_url}/chat",
            headers={"Authorization": f"Bearer {settings.inference_api_key}"},
            json={
                "messages": [
                    {"role": "system", "content": (
                        "You design support-ticket taxonomies. Be decisive and "
                        "grounded in the data."
                    )},
                    {"role": "user", "content": (
                        "Free-form labels with counts from real candidate support "
                        "tickets at a recruitment assessment company:\n"
                        f"{json.dumps(counts.most_common(), indent=1)}\n\n"
                        "Merge these into 8-14 final categories. For each give: "
                        "snake_case name, one-line definition, the raw labels it "
                        "absorbs, and rough share of volume. Then list which "
                        "categories must be treated as SENSITIVE (results disputes, "
                        "complaints, payments, identity, legal/privacy)."
                    )},
                ],
                "max_tokens": 1500,
            },
        )
        resp.raise_for_status()
        print("\n===== PROPOSED TAXONOMY =====\n", flush=True)
        print(resp.json()["output"], flush=True)


asyncio.run(main())
