"""Single source of truth for the Helpdesk ticket taxonomy.

Drives the classifier prompt, sensitivity rules, and (later) Zoho tags.
Derived from taxonomy discovery over real tickets (July 2026) — see
docs/ticket-taxonomy-discovery.json for the raw label counts.

Adding a category = adding one entry here; the classifier prompt, the
validation set, and the sensitivity gate all pick it up automatically.
"""

HELPDESK_TAXONOMY = {
    "availability_confirmation": {
        "description": "Candidate confirming receipt of a test invitation or their availability for a test. Normal, expected replies.",
        "sensitive": False,
        "examples": [
            "Re: Confirmation of Receipt/Availability of Matrix Energy Group Online Test Invitation",
            "Re: Action Required: Confirmation of Receipt/Availability for the Dangote PRP Second Stage Test",
        ],
    },
    "test_invitation_issue": {
        "description": "Test invitation missing, undelivered, expired, or sent to a wrong address.",
        "sensitive": False,
        "examples": [
            "Undeliverable: Chevron Nigeria Limited Operator Skills Test Invitation",
            "I have not received my test invitation",
        ],
    },
    "technical_issue": {
        "description": "Technical problems with a test or the platform: camera, browser, login, test link, page not loading, uploads, submission errors.",
        "sensitive": False,
        "examples": [
            "URGENT: Technical Glitch & Lockout During Lafarge Africa Dragnet Assessment",
            "INABILITY TO LOG INTO TALVIEW SECURE BROWSER FOR ELM EXAMINATION",
            "Page for test not displaying for practice test",
        ],
    },
    "reschedule_request": {
        "description": "Candidate asking to reschedule or postpone a test (illness, clash, technical cause).",
        "sensitive": False,
        "examples": [
            "Unable to Attend Scheduled Assessment Due to Illness",
            "Request for Technical Assistance and Rescheduling of Assessment",
        ],
    },
    "result_question": {
        "description": "Questions about test results, scores, or result verification.",
        "sensitive": True,
        "examples": [
            "June 2026 result verification",
        ],
    },
    "payment_issue": {
        "description": "Scholarship or award payments: pending, missing, duplicate, transfer confirmations, certificates.",
        "sensitive": True,
        "examples": [
            "Inquiry on Chevron payment to 2025/2026 beneficiaries",
        ],
    },
    "identity_verification": {
        "description": "Identity or record problems AND requests to change candidate details: wrong name, name correction, email or bank detail change, profile updates, verification exclusion.",
        "sensitive": True,
        "examples": [
            "URGENT: Exclusion from Verification Correspondence & Pending Status - CHV23406131",
            "Request for Correction of My Name on the Candidate Profile",
        ],
    },
    "complaint": {
        "description": "Complaints or negative feedback about the process, service, or experience.",
        "sensitive": True,
        "examples": [
            "Complaint",
            "Error Complaints",
        ],
    },
    "legal_or_privacy": {
        "description": "Legal matters, data privacy requests, or requests to delete personal information.",
        "sensitive": True,
        "examples": [],
    },
    "application_enquiry": {
        "description": "Application status follow-ups, CV submissions, internship/SIWES placement requests.",
        "sensitive": False,
        "examples": [
            "FOLLOW-UP ON GRADUATE TRAINEE ENGINEERING APPLICATION",
            "Follow-up on Undergraduate Internship Application",
        ],
    },
    "scholarship_training_enquiry": {
        "description": "Questions about scholarships, training programmes, or certification courses.",
        "sensitive": False,
        "examples": [
            "Inquiries for the ND Western Graduate Trainee Test",
        ],
    },
    "business_or_partnership": {
        "description": "Non-candidate contact: vendors, partnerships, promotions, business services.",
        "sensitive": False,
        "examples": [
            "Destination Wedding & Luxury Multi-Day Celebration Enquiry",
            "Advance Your Career with a Globally Recognized Lean Six Sigma Certification",
        ],
    },
    "general_enquiry": {
        "description": "Catch-all for genuine candidate questions that fit no other category.",
        "sensitive": False,
        "examples": [
            "Enquiry",
        ],
    },
    "spam_or_irrelevant": {
        "description": "True spam or chain mail only. A candidate writing about their test, application, or payment is NEVER spam.",
        "sensitive": False,
        "examples": [
            "Barack Obama, President of the United States of America, is popular in your network",
        ],
    },
}

ISSUE_CATEGORIES = list(HELPDESK_TAXONOMY)
SENSITIVE_CATEGORIES = {name for name, cfg in HELPDESK_TAXONOMY.items() if cfg["sensitive"]}
