import pytest
from sqlmodel import select

from app.core.config import get_settings
from app.models.call_record import CallRecord
from app.models.campaign_candidate import CampaignCandidate
from app.models.csat_invitation import CsatInvitation
from app.services import csat_service
from app.services.csat_service import (
    build_csat_response_updates,
    create_csat_invitation_for_call,
    extract_csat_score_and_comment,
    is_call_csat_eligible,
    parse_csat_survey_structure,
    send_pending_csat,
    sync_csat_responses,
)

# Mirrors the real CSAT survey 423246877 shape: a Yes/No/Partially resolution
# question (weightless choices), an open-ended comment, and a matrix/rating
# satisfaction question whose choices carry weights 1-5.
CSAT_SURVEY_DETAILS = {
    "pages": [
        {
            "questions": [
                {
                    "id": "299488811",
                    "family": "single_choice",
                    "answers": {"choices": [
                        {"id": "2091028591", "text": "Yes", "weight": None},
                        {"id": "2091028592", "text": "No", "weight": None},
                    ]},
                },
                {"id": "299488812", "family": "open_ended"},
                {
                    "id": "299488813",
                    "family": "matrix",
                    "answers": {"choices": [
                        {"id": "2091028595", "weight": 1},
                        {"id": "2091028598", "weight": 4},
                        {"id": "2091028599", "weight": 5},
                    ]},
                },
            ]
        }
    ]
}


def make_call(direction="inbound", disposition="answered_by_ai", phone="+2348010000000"):
    return CallRecord(
        external_call_id=f"conv_{phone}",
        direction=direction,
        disposition=disposition,
        candidate_phone=phone,
        candidate_name="Aisha Bello",
    )


# --- Eligibility ----------------------------------------------------------

def test_inbound_answered_call_is_eligible():
    assert is_call_csat_eligible(make_call("inbound", "answered_by_ai")) is True
    assert is_call_csat_eligible(make_call("inbound", "handed_off_to_human")) is True


def test_non_eligible_calls():
    assert is_call_csat_eligible(make_call("inbound", "missed")) is False
    assert is_call_csat_eligible(make_call("inbound", "no_answer")) is False
    assert is_call_csat_eligible(make_call("outbound", "answered_by_ai")) is False


# --- Invitation creation --------------------------------------------------

def persist_call(session, **kw):
    call = make_call(**kw)
    session.add(call)
    session.commit()
    session.refresh(call)
    return call


def test_no_email_gives_pending_contact_lookup(session):
    call = persist_call(session, phone="+2348099999999")  # no matching candidate
    inv = create_csat_invitation_for_call(session, call)
    assert inv.status == "pending_contact_lookup"
    assert inv.candidate_email is None


def test_email_resolved_from_phone_gives_ready_to_send(session):
    session.add(CampaignCandidate(
        campaign_id="c1", phone="+2348010000000", email="aisha@example.com",
        opted_out_email=False,
    ))
    session.commit()
    call = persist_call(session, phone="+2348010000000")

    inv = create_csat_invitation_for_call(session, call)

    assert inv.status == "ready_to_send"
    assert inv.candidate_email == "aisha@example.com"


def test_opted_out_email_gives_skipped(session):
    session.add(CampaignCandidate(
        campaign_id="c1", phone="+2348010000000", email="aisha@example.com",
        opted_out_email=True,
    ))
    session.commit()
    call = persist_call(session, phone="+2348010000000")

    inv = create_csat_invitation_for_call(session, call)

    assert inv.status == "skipped_opt_out"


def test_explicit_email_used_directly(session):
    call = persist_call(session, phone="+2348012223333")
    inv = create_csat_invitation_for_call(session, call, email="direct@example.com")
    assert inv.status == "ready_to_send"
    assert inv.candidate_email == "direct@example.com"


def test_invitation_is_idempotent_per_call(session):
    call = persist_call(session)
    first = create_csat_invitation_for_call(session, call, email="a@example.com")
    second = create_csat_invitation_for_call(session, call, email="a@example.com")
    assert first.id == second.id


# --- Response matcher (pure) ---------------------------------------------

def test_build_csat_response_updates_matches_by_recipient_and_collector():
    inv = CsatInvitation(
        call_record_id="call_1", surveymonkey_recipient_id="rcpt_1", status="sent",
    )
    responses = [
        {"id": "resp_1", "recipient_id": "rcpt_1", "collector_id": "col_1",
         "response_status": "completed", "date_modified": "2026-06-20T16:05:07+00:00"},
        {"id": "resp_x", "recipient_id": "rcpt_1", "collector_id": "other_col"},  # wrong collector
    ]
    updates = build_csat_response_updates([inv], responses, "col_1")
    assert len(updates) == 1
    assert updates[0]["response_id"] == "resp_1"


def test_build_csat_response_updates_skips_unmatched():
    inv = CsatInvitation(call_record_id="c", surveymonkey_recipient_id="rcpt_9", status="sent")
    responses = [{"id": "r", "recipient_id": "rcpt_1", "collector_id": "col_1"}]
    assert build_csat_response_updates([inv], responses, "col_1") == []


# --- Send + sync with a fake SurveyMonkey client -------------------------

class FakeClient:
    def __init__(self):
        self.sent = []

    def create_collector_message(self, collector_id, subject):
        return {"id": "msg_1"}

    def add_message_recipients_bulk(self, collector_id, message_id, contacts):
        return {"succeeded": [
            {"id": f"rcpt_{i}", "email": c["email"]} for i, c in enumerate(contacts)
        ]}

    def send_collector_message(self, collector_id, message_id):
        self.sent.append((collector_id, message_id))
        return {}

    def list_all_survey_responses_bulk(self, survey_id):
        return [{
            "id": "resp_1", "recipient_id": "rcpt_0", "collector_id": "col_1",
            "response_status": "completed", "date_modified": "2026-06-20T16:05:07+00:00",
            "pages": [{"questions": [
                {"id": "299488813", "answers": [{"choice_id": "2091028598"}]},  # weight 4
                {"id": "299488812", "answers": [{"text": "The agent was great."}]},
            ]}],
        }]

    def get_survey_details(self, survey_id):
        return CSAT_SURVEY_DETAILS


def _configure_csat(monkeypatch, fake):
    monkeypatch.setenv("SURVEYMONKEY_CSAT_SURVEY_ID", "survey_1")
    monkeypatch.setenv("SURVEYMONKEY_CSAT_COLLECTOR_ID", "col_1")
    get_settings.cache_clear()
    monkeypatch.setattr(csat_service, "_client", lambda: fake)


def test_send_pending_marks_invitations_sent(session, monkeypatch):
    fake = FakeClient()
    _configure_csat(monkeypatch, fake)
    session.add(CsatInvitation(
        call_record_id="call_1", candidate_email="a@example.com", status="ready_to_send",
    ))
    session.commit()

    result = send_pending_csat(session)

    assert result["sent"] == 1
    assert fake.sent == [("col_1", "msg_1")]
    inv = session.exec(select(CsatInvitation)).first()
    assert inv.status == "sent"
    assert inv.surveymonkey_message_id == "msg_1"
    assert inv.surveymonkey_recipient_id == "rcpt_0"
    assert inv.sent_at is not None
    get_settings.cache_clear()


def test_send_pending_no_ready_invitations(session, monkeypatch):
    fake = FakeClient()
    _configure_csat(monkeypatch, fake)
    assert send_pending_csat(session) == {"sent": 0}
    get_settings.cache_clear()


def test_a_crash_after_the_real_send_does_not_re_email_next_run(session, monkeypatch):
    """The realistic crash: SurveyMonkey has already sent the email, but the
    process dies before we commit "sent". The invitation must NOT be sitting at
    "ready_to_send" for the next scheduled run to pick up and email again."""

    class CrashesAfterSend(FakeClient):
        def send_collector_message(self, collector_id, message_id):
            super().send_collector_message(collector_id, message_id)
            raise RuntimeError("worker killed right after the send")

    fake = CrashesAfterSend()
    _configure_csat(monkeypatch, fake)
    session.add(CsatInvitation(
        call_record_id="call_1", candidate_email="a@example.com", status="ready_to_send",
    ))
    session.commit()

    with pytest.raises(RuntimeError):
        send_pending_csat(session)

    inv = session.exec(select(CsatInvitation)).first()
    # Claimed before the send, so the crash leaves it here — not back at
    # ready_to_send, where the next run would email this person again.
    assert inv.status == "sending"

    # The scheduled batch job explicitly only looks for ready_to_send, so a
    # normal next run leaves this alone rather than silently re-sending.
    assert send_pending_csat(session) == {"sent": 0}
    get_settings.cache_clear()


def test_sync_marks_responded(session, monkeypatch):
    fake = FakeClient()
    _configure_csat(monkeypatch, fake)
    session.add(CsatInvitation(
        call_record_id="call_1", candidate_email="a@example.com",
        surveymonkey_recipient_id="rcpt_0", status="sent",
    ))
    session.commit()

    result = sync_csat_responses(session)

    assert result["updated"] == 1
    inv = session.exec(select(CsatInvitation)).first()
    assert inv.status == "responded"
    assert inv.surveymonkey_response_id == "resp_1"
    assert inv.responded_at is not None
    assert inv.score == 4
    assert inv.comment == "The agent was great."
    get_settings.cache_clear()


# --- Score/comment extraction (pure) -------------------------------------

def test_parse_csat_survey_structure_indexes_rating_and_comment():
    structure = parse_csat_survey_structure(CSAT_SURVEY_DETAILS)
    # Only the weighted rating choices become score choices; Yes/No are excluded.
    assert structure["score_choice_weights"] == {
        "2091028595": 1, "2091028598": 4, "2091028599": 5,
    }
    assert structure["comment_question_ids"] == {"299488812"}


def test_extract_score_and_comment_from_response():
    structure = parse_csat_survey_structure(CSAT_SURVEY_DETAILS)
    response = {"pages": [{"questions": [
        {"id": "299488813", "answers": [{"choice_id": "2091028599"}]},  # weight 5
        {"id": "299488812", "answers": [{"text": "  Loved it  "}]},
    ]}]}
    assert extract_csat_score_and_comment(response, structure) == (5, "Loved it")


def test_extract_returns_none_when_answers_missing():
    structure = parse_csat_survey_structure(CSAT_SURVEY_DETAILS)
    # Only the resolution question answered — no rating, no comment.
    response = {"pages": [{"questions": [
        {"id": "299488811", "answers": [{"choice_id": "2091028591"}]},
    ]}]}
    assert extract_csat_score_and_comment(response, structure) == (None, None)
