"""Seed the dev database with realistic demo data across every scenario.

Wipes and repopulates the Calling Agent tables (campaigns, candidates,
outbound attempts, call records) with a curated, realistic spread so every
UI screen shows lifelike data — campaigns in each lifecycle status,
candidates with varied survey/call outcomes and opt-outs, outbound attempts
in every status, and call records covering every disposition and handoff.

Leaves the Helpdesk tables (mirror + AI audit trail) untouched — those already
hold 50 real classified tickets.

Run with: PYTHONPATH=. .venv/bin/python scripts/seed_demo_data.py
"""

import random
from datetime import datetime, timedelta

from sqlmodel import Session, delete

from app.core.database import create_db_and_tables, engine
from app.models.call_record import CallRecord
from app.models.campaign import Campaign
from app.models.campaign_candidate import CampaignCandidate
from app.models.outbound_call_attempt import OutboundCallAttempt

random.seed(42)
NOW = datetime(2026, 7, 18, 12, 0, 0)

FIRST_NAMES = [
    "Aisha", "Chidi", "Emeka", "Ngozi", "Tunde", "Fatima", "Ibrahim", "Blessing",
    "Yusuf", "Chioma", "Segun", "Amaka", "Musa", "Funke", "Obinna", "Halima",
    "Kelechi", "Bukola", "Suleiman", "Adaeze", "Femi", "Zainab", "Uche", "Damilola",
]
LAST_NAMES = [
    "Okafor", "Bello", "Adeyemi", "Okonkwo", "Ibrahim", "Balogun", "Eze", "Abubakar",
    "Nwosu", "Oladipo", "Mohammed", "Chukwu", "Adewale", "Okoro", "Yakubu", "Ojo",
]


def full_name() -> str:
    return f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"


def phone() -> str:
    return "+23480" + "".join(random.choice("0123456789") for _ in range(8))


def email(name: str) -> str:
    handle = name.lower().replace(" ", ".")
    return f"{handle}{random.randint(1, 99)}@example.com"


# (name, tool, status, has_survey, days_ago, candidate_count, stage)
CAMPAIGNS = [
    ("Dangote PRP Graduate Trainee — Feedback", "FOT", "completed", True, 40, 42, "completed"),
    ("Chevron Operator Skills — CSAT", "Scholastica", "outbound_calling", True, 12, 30, "calling"),
    ("Lafarge Africa Assessment — Feedback", "FOT", "waiting_for_responses", True, 3, 26, "waiting"),
    ("MTNN Graduate Scheme — CSAT", "Scholastica", "survey_sent", True, 1, 34, "sent"),
    ("ND Western SITP — Feedback", "FOT", "outbound_ready", True, 6, 20, "ready"),
    ("Tatum Bank Sales Trainee — Feedback", "FOT", "draft", False, 0, 0, "draft"),
    ("Matrix Energy Group — CSAT", "Scholastica", "failed", True, 8, 6, "failed"),
]

# Per-stage weighted (survey_status, call_status) outcomes.
STAGE_OUTCOMES = {
    "completed": [
        (("responded", "not_queued"), 45),
        (("non_responder", "responded_by_call"), 20),
        (("non_responder", "no_answer"), 10),
        (("non_responder", "voicemail"), 8),
        (("non_responder", "handed_off_to_human"), 5),
        (("excluded_opt_out", "opted_out"), 7),
        (("partial_response", "not_queued"), 5),
    ],
    "calling": [
        (("responded", "not_queued"), 35),
        (("non_responder", "calling"), 12),
        (("non_responder", "queued"), 18),
        (("non_responder", "responded_by_call"), 12),
        (("non_responder", "no_answer"), 10),
        (("excluded_opt_out", "opted_out"), 8),
        (("partial_response", "not_queued"), 5),
    ],
    "waiting": [
        (("sent", "not_queued"), 55),
        (("responded", "not_queued"), 30),
        (("partial_response", "not_queued"), 8),
        (("excluded_opt_out", "opted_out"), 7),
    ],
    "sent": [
        (("sent", "not_queued"), 80),
        (("responded", "not_queued"), 12),
        (("excluded_opt_out", "opted_out"), 8),
    ],
    "ready": [
        (("non_responder", "not_queued"), 70),
        (("responded", "not_queued"), 20),
        (("excluded_opt_out", "opted_out"), 10),
    ],
    "failed": [
        (("sent", "not_queued"), 60),
        (("failed", "failed"), 40),
    ],
}


def weighted_choice(pairs):
    options = [value for value, _ in pairs]
    weights = [weight for _, weight in pairs]
    return random.choices(options, weights=weights, k=1)[0]


def seed():
    create_db_and_tables()
    with Session(engine) as session:
        # Wipe Calling Agent tables (order respects nothing — no FKs enforced).
        session.exec(delete(OutboundCallAttempt))
        session.exec(delete(CampaignCandidate))
        session.exec(delete(CallRecord))
        session.exec(delete(Campaign))
        session.commit()

        all_attempts = 0
        for (name, tool, status, has_survey, days_ago, count, stage) in CAMPAIGNS:
            created = NOW - timedelta(days=days_ago)
            campaign = Campaign(
                name=name,
                tool_name=tool,
                status=status,
                response_wait_hours=random.choice([24, 48, 72]),
                survey_id=f"52{random.randint(1000000, 9999999)}" if has_survey else None,
                surveymonkey_collector_id=f"46{random.randint(1000000, 9999999)}" if has_survey else None,
                created_at=created,
                survey_sent_at=created + timedelta(hours=2) if has_survey else None,
                non_responder_checked_at=(
                    created + timedelta(days=2)
                    if stage in ("completed", "calling", "ready")
                    else None
                ),
            )
            session.add(campaign)
            session.commit()
            session.refresh(campaign)

            for _ in range(count):
                name_ = full_name()
                survey_status, call_status = weighted_choice(STAGE_OUTCOMES[stage])
                responded = survey_status in ("responded", "partial_response")
                candidate = CampaignCandidate(
                    campaign_id=campaign.id,
                    candidate_name=name_,
                    email=email(name_),
                    phone=phone(),
                    tool_name=tool,
                    campaign_name=name,
                    external_candidate_id=f"ATS-{random.randint(10000, 99999)}",
                    surveymonkey_recipient_id=f"109{random.randint(10000, 99999)}" if has_survey else None,
                    surveymonkey_response_id=f"115{random.randint(100000, 999999)}" if responded else None,
                    surveymonkey_response_status="completed" if survey_status == "responded" else ("partial" if survey_status == "partial_response" else None),
                    survey_responded_at=created + timedelta(hours=random.randint(3, 40)) if responded else None,
                    survey_status=survey_status,
                    call_status=call_status,
                    opted_out_call=call_status == "opted_out",
                    opted_out_email=survey_status == "excluded_opt_out",
                    created_at=created,
                    updated_at=created + timedelta(hours=random.randint(1, 48)),
                )
                session.add(candidate)
                session.commit()

                # Outbound attempts for candidates who entered the call pipeline.
                if call_status not in ("not_queued", "opted_out") and stage in ("completed", "calling"):
                    session.refresh(candidate)
                    attempts = 1 if call_status in ("responded_by_call", "answered") else random.randint(1, 3)
                    for attempt_no in range(1, attempts + 1):
                        final = attempt_no == attempts
                        att_status = call_status if final else random.choice(["no_answer", "busy", "voicemail"])
                        answered = att_status in ("answered", "responded_by_call", "handed_off_to_human")
                        started = created + timedelta(days=2, hours=attempt_no)
                        session.add(OutboundCallAttempt(
                            campaign_id=campaign.id,
                            candidate_id=candidate.id,
                            candidate_name=name_,
                            candidate_email=candidate.email,
                            campaign_name=name,
                            tool_name=tool,
                            phone=candidate.phone,
                            attempt_number=attempt_no,
                            status=att_status if att_status != "calling" else "calling",
                            disposition=att_status,
                            elevenlabs_conversation_id=f"conv_{random.randint(10**9, 10**10)}" if answered else None,
                            transcript="Agent: Hello, this is the Dragnet candidate experience line...\nCandidate: Yes, I took the test." if answered else None,
                            summary="Candidate completed the survey by phone." if att_status == "responded_by_call" else None,
                            recording_url=f"https://recordings.example.com/{random.randint(10**6, 10**7)}.mp3" if answered else None,
                            started_at=started,
                            ended_at=started + timedelta(minutes=random.randint(1, 6)) if att_status != "calling" else None,
                            created_at=started - timedelta(minutes=5),
                        ))
                        all_attempts += 1
            session.commit()
            print(f"  campaign: {name} [{status}] — {count} candidates")

        # Call records — inbound + outbound, every disposition, some with handoff.
        dispositions = [
            ("inbound", "answered_by_ai", False),
            ("inbound", "handed_off_to_human", True),
            ("inbound", "handoff_failed", True),
            ("inbound", "answered_by_ai", False),
            ("inbound", "missed", False),
            ("inbound", "handed_off_to_human", True),
            ("outbound", "answered_by_ai", False),
            ("outbound", "voicemail", False),
            ("outbound", "no_answer", False),
            ("outbound", "opted_out", False),
            ("outbound", "failed", False),
            ("outbound", "answered_by_ai", False),
            ("inbound", "answered_by_ai", False),
            ("inbound", "handed_off_to_human", True),
            ("outbound", "no_answer", False),
            ("inbound", "answered_by_ai", False),
        ]
        issues = [
            "Cannot access the test link", "Camera not detected in secure browser",
            "Did not receive login credentials", "Requesting reschedule due to network",
            "Practice test page not loading", "Question about test date",
        ]
        camp_names = [c[0] for c in CAMPAIGNS if c[3]]
        for i, (direction, disposition, has_handoff) in enumerate(dispositions):
            name_ = full_name()
            when = NOW - timedelta(days=random.randint(0, 14), hours=random.randint(0, 23))
            answered = disposition in ("answered_by_ai", "handed_off_to_human")
            session.add(CallRecord(
                external_call_id=f"conv_seed_{1000 + i}",
                direction=direction,
                candidate_name=name_,
                candidate_phone=phone(),
                tool_name=random.choice(["FOT", "Scholastica"]),
                campaign_name=random.choice(camp_names) if direction == "outbound" else None,
                disposition=disposition,
                issue_summary=random.choice(issues) if direction == "inbound" else "Outbound survey call",
                transcription=(
                    "Agent: Hello, thank you for calling Dragnet Solutions...\n"
                    "Candidate: Hi, I'm having trouble with my assessment link.\n"
                    "Agent: I can help with that. Let me guide you..."
                ) if answered else None,
                recording_url=f"https://recordings.example.com/{random.randint(10**6, 10**7)}.mp3" if answered else None,
                handoff_status="completed" if has_handoff and disposition != "handoff_failed" else ("failed" if disposition == "handoff_failed" else None),
                handoff_call_sid=f"CA{random.randint(10**14, 10**15)}" if has_handoff else None,
                handoff_duration=random.randint(30, 240) if has_handoff and disposition != "handoff_failed" else None,
                handoff_bridged=True if has_handoff and disposition != "handoff_failed" else (False if has_handoff else None),
                call_start_time=when,
                call_end_time=when + timedelta(minutes=random.randint(1, 8)),
                created_at=when + timedelta(minutes=random.randint(1, 9)),
            ))
        session.commit()
        print(f"\nSeeded {len(dispositions)} call records and {all_attempts} outbound attempts.")


if __name__ == "__main__":
    seed()
