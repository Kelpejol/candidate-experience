from app.services.helpdesk_classifier import classify_ticket


class _FakeResponse:
    def __init__(self, output):
        self._output = output

    def raise_for_status(self):
        return None

    def json(self):
        return {"output": self._output}


def test_classify_ticket_repairs_malformed_json_once(monkeypatch):
    calls = []
    repaired = (
        '{"issue_category":"technical_issue","tool_name":null,'
        '"campaign_name":null,"sensitivity_detected":false,'
        '"confidence_label":"high","reason":"Candidate has a login issue."}'
    )

    def fake_post(url, headers, json, timeout):
        calls.append(json["messages"])
        if len(calls) == 1:
            return _FakeResponse("technical issue, high confidence")
        return _FakeResponse(repaired)

    monkeypatch.setattr("app.services.helpdesk_classifier.httpx.post", fake_post)

    classification = classify_ticket("Login problem", "I cannot access my test")

    assert classification.issue_category == "technical_issue"
    assert classification.confidence_label == "high"
    assert len(calls) == 2
    assert "Malformed classifier output" in calls[1][1]["content"]

