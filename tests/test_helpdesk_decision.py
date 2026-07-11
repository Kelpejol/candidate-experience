from app.services.helpdesk_classifier import TicketClassification
from app.services.helpdesk_decision import decide_ticket_action


def make(category="technical_issue", sensitive=False, confidence="high"):
    return TicketClassification(
        issue_category=category, tool_name=None, campaign_name=None,
        sensitivity_detected=sensitive, confidence_label=confidence, reason="test",
    )


def test_sensitive_category_never_drafts_even_at_high_confidence():
    decision = decide_ticket_action(make(category="complaint", confidence="high"))
    assert decision.action == "route_to_human"


def test_sensitivity_flag_alone_routes_even_for_safe_category():
    decision = decide_ticket_action(make(category="technical_issue", sensitive=True))
    assert decision.action == "route_to_human"


def test_low_confidence_always_routes():
    decision = decide_ticket_action(make(category="general_enquiry", confidence="low"))
    assert decision.action == "route_to_human"


def test_confirmation_is_tag_only():
    decision = decide_ticket_action(make(category="availability_confirmation"))
    assert decision.action == "tag_only"


def test_medium_confidence_confirmation_still_gets_drafted_not_ignored():
    decision = decide_ticket_action(make(category="availability_confirmation", confidence="medium"))
    assert decision.action == "draft_reply"


def test_answerable_ticket_drafts():
    decision = decide_ticket_action(make(category="technical_issue", confidence="high"))
    assert decision.action == "draft_reply"


def test_complaint_subject_routes_no_matter_what_the_model_said():
    # Model says harmless technical_issue at high confidence — subject wins.
    decision = decide_ticket_action(
        make(category="technical_issue", confidence="high"),
        subject="Complaint on application error",
    )
    assert decision.action == "route_to_human"
    assert decision.rule == "complaint_keyword_backstop"


def test_complaint_stem_variants_are_caught():
    for subject in (
        "I am COMPLAINING about my test",
        "Disputing my assessment result",
        "Appeal for reconsideration",
        "Request for compensation",
    ):
        decision = decide_ticket_action(make(), subject=subject)
        assert decision.action == "route_to_human", subject


def test_benign_subject_does_not_false_positive():
    decision = decide_ticket_action(
        make(category="technical_issue"),
        subject="Unable to Access Practice Test",
    )
    assert decision.action == "draft_reply"


def test_negative_zoho_sentiment_routes():
    decision = decide_ticket_action(
        make(category="technical_issue", confidence="high"),
        subject="Practice test issue",
        zoho_sentiment="NEGATIVE",
    )
    assert decision.action == "route_to_human"
    assert decision.rule == "negative_sentiment_backstop"


def test_spam_verdict_on_a_reply_to_our_thread_routes_instead_of_ignoring():
    decision = decide_ticket_action(
        make(category="spam_or_irrelevant", confidence="high"),
        subject="Re: Demo Test Reminder for the Chevron Aptitude Test",
    )
    assert decision.action == "route_to_human"
    assert decision.rule == "spam_claim_on_reply_backstop"


def test_actual_cold_spam_still_gets_tagged_away():
    decision = decide_ticket_action(
        make(category="spam_or_irrelevant", confidence="high"),
        subject="Is your company still doing payroll manually?",
    )
    assert decision.action == "tag_only"


def test_grounded_draft_stays_a_draft():
    from app.services.helpdesk_ai_service import apply_grounding_gate
    from app.services.helpdesk_kb_service import GroundingResult

    draft = decide_ticket_action(make(category="technical_issue"))
    grounding = GroundingResult(grounded=True, best_distance=0.25, chunks=[{"text": "x"}])

    decision, status = apply_grounding_gate(draft, grounding)
    assert decision.action == "draft_reply"
    assert status == "grounded"


def test_ungrounded_draft_flips_to_human():
    from app.services.helpdesk_ai_service import apply_grounding_gate
    from app.services.helpdesk_kb_service import GroundingResult

    draft = decide_ticket_action(make(category="technical_issue"))
    grounding = GroundingResult(grounded=False, best_distance=0.55, chunks=[])

    decision, status = apply_grounding_gate(draft, grounding)
    assert decision.action == "route_to_human"
    assert decision.rule == "kb_grounding_missing"
    assert status == "missing"


def test_unavailable_kb_counts_as_ungrounded():
    from app.services.helpdesk_ai_service import apply_grounding_gate
    from app.services.helpdesk_kb_service import GroundingResult

    draft = decide_ticket_action(make(category="technical_issue"))
    grounding = GroundingResult(grounded=False, best_distance=None, chunks=[])

    decision, status = apply_grounding_gate(draft, grounding)
    assert decision.action == "route_to_human"
    assert status == "missing"
