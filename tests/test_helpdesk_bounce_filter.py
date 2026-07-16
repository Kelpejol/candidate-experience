from app.models.helpdesk_ticket_mirror import HelpdeskTicketMirror
from app.services.helpdesk_ai_service import is_system_bounce_notification


def make_mirror(email=None, subject=None):
    return HelpdeskTicketMirror(
        zoho_ticket_id="t1", channel="Email", candidate_email=email, subject=subject,
    )


def test_our_own_domain_sender_is_a_bounce():
    mirror = make_mirror(email="microsoftexchange329e71ec@dragnet-solutions.com")
    assert is_system_bounce_notification(mirror) is True


def test_undeliverable_subject_is_a_bounce():
    mirror = make_mirror(email="jane@gmail.com", subject="Undeliverable: Test Invitation")
    assert is_system_bounce_notification(mirror) is True


def test_real_candidate_is_not_a_bounce():
    mirror = make_mirror(email="jane@gmail.com", subject="I cannot access my test")
    assert is_system_bounce_notification(mirror) is False


def test_case_insensitive_domain_match():
    mirror = make_mirror(email="Postmaster@Dragnet-Solutions.COM")
    assert is_system_bounce_notification(mirror) is True
