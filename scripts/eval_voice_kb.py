"""Evaluate the voice-agent KB pipeline for quality — not just correctness.

Answers the question "is the AI good enough" with two separate checks:

  Part A — retrieval/grounding precision & recall against a golden set built
    from the REAL kb_voice/ content (exact headings, natural paraphrases, and
    clearly out-of-scope questions). Deterministic, no LLM judge — this is
    hard evidence about the embedding + grounding threshold, not a vibe check.

  Part B — MODEL SANITY CHECK, not an agent eval. The real outbound agent's
    behavior is driven by a system prompt configured in ElevenLabs' dashboard,
    running inside ElevenLabs' own conversation orchestration — neither lives
    in this repo, and there's no API/browser access to exercise it headlessly
    from here. This section instead asks OUR gateway's underlying model (the
    same one the real agent's Custom LLM points at) to follow a much simpler,
    hand-written stand-in instruction ("answer only from this content"), and
    scores THAT for relevancy/faithfulness/policy compliance. It tells you
    whether the model is capable of following KB-grounded instructions well —
    it does NOT tell you the real deployed agent will behave the same way,
    since it skips ElevenLabs' actual prompt and orchestration entirely.
    Once real calls exist, the honest version of this check is scoring the
    REAL transcripts already captured in CallRecord.transcription /
    OutboundCallAttempt.transcript — not a simulation.

Part A tells you the retrieval/grounding layer is solid. Part B tells you the
underlying model is capable of good, faithful, policy-compliant answers when
given good instructions — a useful pre-flight sanity check, not proof the
deployed agent behaves this way. If Part A comes back clean, the remaining
gap really is "get real content into the KB" (the SharePoint/document-
structuring problem) — Part B doesn't add confidence about the deployed agent
itself.

Run with: PYTHONPATH=. .venv/bin/python scripts/eval_voice_kb.py
Needs: pip install -r requirements-eval.txt (not in the main requirements.txt
— see that file for why).
"""

import os
import sys

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")

from app.services.voice_kb_retrieval import retrieve_for_answer
from evals.voice_kb_golden_set import (
    GENERATION_SAMPLE,
    OUT_OF_SCOPE_QUESTIONS,
    PARAPHRASED_QUESTIONS,
    REAL_QUESTIONS,
)


# --- Part A: deterministic retrieval/grounding -----------------------------

def _check_positive(question: str, expected_source: str) -> dict:
    result = retrieve_for_answer(question)
    correct = bool(result["answer_available"]) and expected_source in (
        result["sources"] or []
    )
    return {
        "question": question,
        "expected_source": expected_source,
        "answer_available": result["answer_available"],
        "sources": result["sources"],
        "best_distance": result["best_distance"],
        "correct": correct,
    }


def _check_negative(question: str) -> dict:
    result = retrieve_for_answer(question)
    correct = not result["answer_available"]
    return {
        "question": question,
        "answer_available": result["answer_available"],
        "best_distance": result["best_distance"],
        "correct": correct,
    }


def run_retrieval_eval() -> dict:
    real = [_check_positive(q["question"], q["expected_source"]) for q in REAL_QUESTIONS]
    paraphrased = [
        _check_positive(q["question"], q["expected_source"]) for q in PARAPHRASED_QUESTIONS
    ]
    out_of_scope = [_check_negative(q) for q in OUT_OF_SCOPE_QUESTIONS]
    return {"real": real, "paraphrased": paraphrased, "out_of_scope": out_of_scope}


def _print_tier(name: str, rows: list[dict]) -> None:
    passed = sum(1 for r in rows if r["correct"])
    print(f"\n{name}: {passed}/{len(rows)} correct")
    for r in rows:
        if r["correct"]:
            continue
        if "expected_source" in r:
            print(
                f"  ✗ {r['question']!r}\n"
                f"      expected source: {r['expected_source']}\n"
                f"      got: answer_available={r['answer_available']} "
                f"sources={r['sources']} distance={r['best_distance']}"
            )
        else:
            print(
                f"  ✗ {r['question']!r}\n"
                f"      expected to escalate, but answer_available=True "
                f"(distance={r['best_distance']})"
            )


# --- Part B: model sanity check (NOT the real ElevenLabs agent — see the
# module docstring for why this is a proxy, not an agent eval) -------------

ANSWER_PROMPT = """You are a phone support assistant. Answer the candidate's \
question using ONLY the information below. Keep it brief and conversational, \
as if speaking on a phone call. If the information below doesn't actually \
answer the question, say you'll have a colleague follow up — do not guess or \
invent anything.

Information:
{content}

Candidate's question: {question}

Your spoken answer:"""

ESCALATE_PROMPT = """You are a phone support assistant. The candidate asked a \
question, but you have NO information available to answer it. Respond \
briefly saying you'll have a colleague follow up — do not guess or invent an \
answer.

Candidate's question: {question}

Your spoken answer:"""


def run_generation_eval() -> None:
    from deepeval import evaluate
    from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric, GEval
    from deepeval.test_case import LLMTestCase, SingleTurnParams

    from evals.gateway_model import GatewayModel

    model = GatewayModel()
    test_cases = []
    metrics_by_case: dict[int, list] = {}

    for i, item in enumerate(GENERATION_SAMPLE):
        question = item["question"]
        retrieval = retrieve_for_answer(question)
        content = retrieval["content"] or []

        if content:
            prompt = ANSWER_PROMPT.format(content="\n\n".join(content), question=question)
        else:
            prompt = ESCALATE_PROMPT.format(question=question)
        actual_output = model.generate(prompt)

        case = LLMTestCase(
            input=question,
            actual_output=actual_output,
            retrieval_context=content or None,
        )
        test_cases.append(case)

        metrics = []
        if item["expects_answer"] and content:
            metrics.append(AnswerRelevancyMetric(threshold=0.7, model=model))
            metrics.append(FaithfulnessMetric(threshold=0.7, model=model))
        if item.get("notes") and "decline" in item["notes"]:
            metrics.append(
                GEval(
                    name="no_score_leaked",
                    criteria=(
                        "The response must NOT state, imply, or invent any "
                        "specific test score, percentage, pass/fail result, or "
                        "ranking. It should politely explain that scores or "
                        "results aren't shared over the phone."
                    ),
                    evaluation_params=[SingleTurnParams.ACTUAL_OUTPUT],
                    model=model,
                    threshold=0.7,
                )
            )
        if not item["expects_answer"]:
            metrics.append(
                GEval(
                    name="declines_and_escalates",
                    criteria=(
                        "The response must NOT attempt to answer the question. "
                        "It must say it doesn't know and will have a colleague "
                        "follow up, without fabricating any factual answer."
                    ),
                    evaluation_params=[
                        SingleTurnParams.INPUT,
                        SingleTurnParams.ACTUAL_OUTPUT,
                    ],
                    model=model,
                    threshold=0.7,
                )
            )
        metrics_by_case[i] = metrics

    print(
        "\n--- Part B: MODEL SANITY CHECK — a stand-in prompt, NOT the real "
        "ElevenLabs agent (see module docstring) ---"
    )
    for i, case in enumerate(test_cases):
        metrics = metrics_by_case[i]
        print(f"\nQ: {case.input}")
        print(f"A: {case.actual_output}")
        for metric in metrics:
            metric.measure(case)
            mark = "✓" if metric.is_successful() else "✗"
            print(f"  {mark} {metric.__name__}: {metric.score:.2f} — {metric.reason}")


if __name__ == "__main__":
    print("=== Part A: retrieval / grounding precision ===")
    results = run_retrieval_eval()
    _print_tier("Real questions (exact KB headings)", results["real"])
    _print_tier("Paraphrased questions (natural caller wording)", results["paraphrased"])
    _print_tier("Out-of-scope questions (should escalate)", results["out_of_scope"])

    total = sum(len(v) for v in results.values())
    passed = sum(r["correct"] for v in results.values() for r in v)
    print(f"\nPart A total: {passed}/{total} correct")

    if "--skip-generation" not in sys.argv:
        run_generation_eval()
    else:
        print(
            "\n(skipped Part B, the model sanity check — pass without "
            "--skip-generation to run it)"
        )
