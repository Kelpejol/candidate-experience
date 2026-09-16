"""Golden test data for the voice-agent KB eval, built from the REAL content
in kb_voice/ — not synthetic examples. If the KB changes, regenerate these by
hand against the new headings.

Three tiers, each testing something different:

- REAL_QUESTIONS: the KB's own headings, verbatim. Tests the floor — if these
  don't retrieve correctly, nothing will.
- PARAPHRASED_QUESTIONS: the same intents, in a caller's own words rather than
  the heading text. Tests whether the embedding generalizes — this is what a
  real caller actually sounds like.
- OUT_OF_SCOPE_QUESTIONS: questions the KB has no answer for. Tests the other
  failure mode — answering confidently when it should escalate instead.

GENERATION_SAMPLE is a smaller, curated subset used for the (slower, LLM-judged)
generation-quality pass — enough to be meaningful without scoring all 39.
"""

REAL_QUESTIONS = [
    {"question": "How long does the test take?", "expected_source": "during_test.md"},
    {"question": "Can I pause the test once I start?", "expected_source": "during_test.md"},
    {"question": "Can I go back to a previous question?", "expected_source": "during_test.md"},
    {"question": "I accidentally submitted my test", "expected_source": "during_test.md"},
    {"question": "What happens if I run out of time?", "expected_source": "during_test.md"},
    {"question": "Can I take a break during the test?", "expected_source": "during_test.md"},

    {"question": "I can't log in to my test", "expected_source": "login_and_access.md"},
    {"question": "I forgot my password", "expected_source": "login_and_access.md"},
    {"question": "My username or password is not working", "expected_source": "login_and_access.md"},
    {"question": "My activation link is not working", "expected_source": "login_and_access.md"},
    {"question": "I'm locked out of my account", "expected_source": "login_and_access.md"},

    {"question": "When will I get my results?", "expected_source": "results_and_general.md"},
    {"question": "Can you tell me my score?", "expected_source": "results_and_general.md"},
    {"question": "I want to dispute my result", "expected_source": "results_and_general.md"},
    {"question": "What is FOT?", "expected_source": "results_and_general.md"},
    {"question": "What is Scholastica?", "expected_source": "results_and_general.md"},
    {"question": "Who is Dragnet?", "expected_source": "results_and_general.md"},
    {"question": "Is there a fee to take the test?", "expected_source": "results_and_general.md"},
    {"question": "How do I contact support?", "expected_source": "results_and_general.md"},
    {"question": "What identification do I need?", "expected_source": "results_and_general.md"},
    {"question": "Am I eligible to take this assessment?", "expected_source": "results_and_general.md"},
    {"question": "Is my personal information safe?", "expected_source": "results_and_general.md"},

    {"question": "How can I reschedule my exam?", "expected_source": "scheduling.md"},
    {"question": "I missed my test window", "expected_source": "scheduling.md"},
    {"question": "How much notice do I need to reschedule?", "expected_source": "scheduling.md"},
    {"question": "Can I change my test date myself?", "expected_source": "scheduling.md"},
    {"question": "I have a clash or emergency on my test date", "expected_source": "scheduling.md"},

    {"question": "What browser or device should I use?", "expected_source": "technical.md"},
    {"question": "The test page won't load", "expected_source": "technical.md"},
    {"question": "The test froze or my screen went blank", "expected_source": "technical.md"},
    {"question": "I'm having trouble uploading my passport photograph", "expected_source": "technical.md"},
    {"question": "Do I need a camera or microphone?", "expected_source": "technical.md"},
    {"question": "My internet disconnected during the test", "expected_source": "technical.md"},
    {"question": "The test is very slow", "expected_source": "technical.md"},

    {"question": "How do I receive my test invitation?", "expected_source": "test_invitations.md"},
    {"question": "I did not receive my invitation email", "expected_source": "test_invitations.md"},
    {"question": "Where do I find my test link?", "expected_source": "test_invitations.md"},
    {"question": "How long is my test link valid?", "expected_source": "test_invitations.md"},
    {"question": "Can I use my link on a different device?", "expected_source": "test_invitations.md"},
]

PARAPHRASED_QUESTIONS = [
    {"question": "How long do I have to finish the assessment?", "expected_source": "during_test.md"},
    {"question": "Can I stop halfway through and continue later?", "expected_source": "during_test.md"},
    {"question": "I can't sign into my account for the test", "expected_source": "login_and_access.md"},
    {"question": "I don't remember my login password", "expected_source": "login_and_access.md"},
    {"question": "When do I find out if I passed?", "expected_source": "results_and_general.md"},
    {"question": "What company is running this assessment?", "expected_source": "results_and_general.md"},
    {"question": "Do I have to pay anything to take this test?", "expected_source": "results_and_general.md"},
    {"question": "I need to move my exam to a different day", "expected_source": "scheduling.md"},
    {"question": "I wasn't able to make my scheduled test time", "expected_source": "scheduling.md"},
    {"question": "What kind of computer do I need for this?", "expected_source": "technical.md"},
    {"question": "The website isn't loading for my test", "expected_source": "technical.md"},
    {"question": "My screen froze during the exam", "expected_source": "technical.md"},
    {"question": "Do I need a webcam for this assessment?", "expected_source": "technical.md"},
    {"question": "I haven't gotten the email with my test link", "expected_source": "test_invitations.md"},
    {"question": "Can I take the test on my phone instead of my laptop?", "expected_source": "test_invitations.md"},
    {"question": "What's this FOT thing Dragnet uses?", "expected_source": "results_and_general.md"},
]

OUT_OF_SCOPE_QUESTIONS = [
    "What's the capital of France?",
    "Can you help me file my taxes?",
    "What's the weather like today?",
    "Can you recommend a good recipe for dinner?",
    "What's the stock price of Apple?",
    "Tell me a joke.",
    "Who won the last World Cup?",
    "Can you help me write my resume?",
    "How do I get a visa to travel to the US?",
    "What time zone is it in Tokyo right now?",
]

# A smaller sample for the slower, LLM-judged generation-quality pass. Spans
# every KB file plus two escalation cases (one out-of-scope, one where the KB
# explicitly refuses on policy grounds rather than lacking the content).
GENERATION_SAMPLE = [
    {
        "question": "How long does the test take?",
        "expects_answer": True,
        "notes": None,
    },
    {
        "question": "I forgot my password",
        "expects_answer": True,
        "notes": None,
    },
    {
        "question": "Who is Dragnet?",
        "expects_answer": True,
        "notes": None,
    },
    {
        "question": "How can I reschedule my exam?",
        "expects_answer": True,
        "notes": None,
    },
    {
        "question": "The test froze or my screen went blank",
        "expects_answer": True,
        "notes": None,
    },
    {
        "question": "Where do I find my test link?",
        "expects_answer": True,
        "notes": None,
    },
    {
        # Content IS retrieved (the KB explicitly covers this), but the policy
        # is to refuse — the generation must not invent or leak a score.
        "question": "Can you tell me my score?",
        "expects_answer": True,
        "notes": "must decline to share a specific score/result",
    },
    {
        "question": "What's the capital of France?",
        "expects_answer": False,
        "notes": None,
    },
]
