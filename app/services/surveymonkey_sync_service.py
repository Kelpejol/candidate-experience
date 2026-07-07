def summarize_collector_responses(
    recipients: list[dict],
    responses: list[dict],
    collector_id: str,
) -> dict:
    responses_for_collector = [
        response
        for response in responses
        if str(response.get("collector_id")) == str(collector_id)
    ]

    completed_responses = [
        response
        for response in responses_for_collector
        if response.get("response_status") == "completed"
    ]

    partial_responses = [
        response
        for response in responses_for_collector
        if response.get("response_status") == "partial"
    ]

    responded_by_recipient_id = {
        str(response.get("recipient_id")): response
        for response in completed_responses + partial_responses
        if response.get("recipient_id") is not None
    }

    completed_recipient_ids = {
        str(response.get("recipient_id"))
        for response in completed_responses
        if response.get("recipient_id") is not None
    }

    partial_recipient_ids = {
        str(response.get("recipient_id"))
        for response in partial_responses
        if response.get("recipient_id") is not None
    }

    non_responder_recipients = [
        recipient
        for recipient in recipients
        if str(recipient.get("id")) not in responded_by_recipient_id
    ]

    return {
        "total_recipients": len(recipients),
        "completed": len(completed_recipient_ids),
        "partial": len(partial_recipient_ids),
        "non_responders": len(non_responder_recipients),
        "responded_by_recipient_id": responded_by_recipient_id,
        "non_responder_recipients": non_responder_recipients,
    }


def build_candidate_survey_updates(
    candidates: list,
    recipients: list[dict],
    response_summary: dict,
) -> list[dict]:
    recipients_by_email = {
        str(recipient.get("email")).lower(): recipient
        for recipient in recipients
        if recipient.get("email")
    }

    responded_by_recipient_id = response_summary["responded_by_recipient_id"]

    updates = []

    for candidate in candidates:
        candidate_email = getattr(candidate, "email", None)
        if not candidate_email:
            continue

        recipient = recipients_by_email.get(candidate_email.lower())
        if not recipient:
            continue

        recipient_id = str(recipient.get("id"))
        response = responded_by_recipient_id.get(recipient_id)

        if response:
            response_status = response.get("response_status")
            survey_status = (
                "responded"
                if response_status == "completed"
                else "partial_response"
            )

            updates.append(
                {
                    "candidate_id": candidate.id,
                    "surveymonkey_recipient_id": recipient_id,
                    "surveymonkey_response_id": str(response.get("id")),
                    "surveymonkey_response_status": response_status,
                    "survey_status": survey_status,
                    "survey_responded_at": response.get("date_modified"),
                }
            )
        else:
            updates.append(
                {
                    "candidate_id": candidate.id,
                    "surveymonkey_recipient_id": recipient_id,
                    "surveymonkey_response_id": None,
                    "surveymonkey_response_status": None,
                    "survey_status": "non_responder",
                    "survey_responded_at": None,
                }
            )

    return updates