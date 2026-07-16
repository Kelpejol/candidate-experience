from app.services.surveymonkey_campaign_template_service import (
    build_page_payload,
    build_campaign_template_variables,
    build_campaign_survey_title,
    build_question_payload,
    clone_surveymonkey_survey_from_template,
    extract_surveymonkey_survey_id,
    interpolate_template_placeholders,
)


class FakeCampaign:
    name = "Graduate Aptitude Test"
    tool_name = "FOT"


def test_build_campaign_survey_title_uses_campaign_name():
    assert (
        build_campaign_survey_title(FakeCampaign())
        == "Graduate Aptitude Test - Assessment Experience Feedback"
    )


def test_build_campaign_template_variables_uses_campaign_fields():
    assert build_campaign_template_variables(FakeCampaign()) == {
        "campaign_name": "Graduate Aptitude Test",
        "campaign_title": "Graduate Aptitude Test",
        "assessment_name": "Graduate Aptitude Test",
        "tool_name": "FOT",
    }


def test_interpolate_template_placeholders_replaces_known_values_recursively():
    value = {
        "title": "{{campaign_name}}",
        "headings": [
            {
                "heading": "How was the {{tool_name}} assessment for {{campaign_name}}?"
            }
        ],
        "unknown": "{{candidate_name}}",
    }

    assert interpolate_template_placeholders(
        value,
        {
            "campaign_name": "Graduate Aptitude Test",
            "tool_name": "FOT",
        },
    ) == {
        "title": "Graduate Aptitude Test",
        "headings": [
            {
                "heading": "How was the FOT assessment for Graduate Aptitude Test?"
            }
        ],
        "unknown": "{{candidate_name}}",
    }


def test_extract_surveymonkey_survey_id_supports_top_level_id():
    assert extract_surveymonkey_survey_id({"id": "survey_123"}) == "survey_123"


def test_extract_surveymonkey_survey_id_supports_nested_data_id():
    assert extract_surveymonkey_survey_id({"data": {"id": "survey_456"}}) == "survey_456"


def test_build_page_payload_removes_read_only_fields():
    payload = build_page_payload(
        {
            "id": "page_123",
            "href": "https://example.com/page",
            "title": "{{campaign_name}} feedback",
            "description": "Tell us about the {{tool_name}} assessment",
            "position": 1,
            "question_count": 3,
        },
        {
            "campaign_name": "Graduate Aptitude Test",
            "tool_name": "FOT",
        },
    )

    assert payload == {
        "title": "Graduate Aptitude Test feedback",
        "description": "Tell us about the FOT assessment",
        "position": 1,
    }


def test_build_question_payload_removes_nested_read_only_fields():
    payload = build_question_payload(
        {
            "id": "question_123",
            "href": "https://example.com/question",
            "family": "single_choice",
            "subtype": "vertical",
            "position": 1,
            "visible": True,
            "headings": [{"heading": "How was the {{campaign_name}} test?"}],
            "answers": {
                "choices": [
                    {
                        "id": "choice_123",
                        "href": "https://example.com/choice",
                        "text": "{{tool_name}} was good",
                        "position": 1,
                    }
                ]
            },
        },
        {
            "campaign_name": "Graduate Aptitude Test",
            "tool_name": "FOT",
        },
    )

    assert payload == {
        "family": "single_choice",
        "subtype": "vertical",
        "position": 1,
        "visible": True,
        "headings": [{"heading": "How was the Graduate Aptitude Test test?"}],
        "answers": {"choices": [{"text": "FOT was good", "position": 1}]},
    }


def test_build_question_payload_removes_matrix_rating_choice_fields_rejected_by_api():
    payload = build_question_payload(
        {
            "family": "matrix",
            "subtype": "rating",
            "position": 1,
            "visible": True,
            "headings": [{"heading": "Rate your experience"}],
            "answers": {
                "rows": [{"position": 1, "visible": True, "text": ""}],
                "choices": [
                    {
                        "position": 1,
                        "visible": True,
                        "text": "Excellent",
                        "is_na": False,
                        "weight": 5,
                        "description": "",
                    }
                ],
            },
        }
    )

    assert payload["answers"]["choices"] == [
        {
            "position": 1,
            "text": "Excellent",
            "weight": 5,
        }
    ]
    assert payload["answers"]["rows"] == [
        {
            "position": 1,
            "text": "",
        }
    ]


def test_build_question_payload_converts_other_answer_object_to_create_api_array():
    payload = build_question_payload(
        {
            "family": "single_choice",
            "subtype": "vertical_two_col",
            "headings": [{"heading": "What went wrong?"}],
            "answers": {
                "choices": [{"text": "Other", "position": 1, "visible": True}],
                "other": {
                    "position": 0,
                    "visible": True,
                    "text": "If Other, please briefly describe",
                    "num_lines": 1,
                    "num_chars": 50,
                    "is_answer_choice": True,
                    "apply_all_rows": False,
                    "error_text": "Please enter a comment.",
                },
            },
        }
    )

    assert payload["answers"]["other"] == [
        {
            "text": "If Other, please briefly describe",
            "num_lines": 1,
            "num_chars": 50,
            "is_answer_choice": True,
            "error_text": "Please enter a comment.",
        }
    ]


def test_clone_surveymonkey_survey_deletes_created_survey_if_question_creation_fails():
    class FakeClient:
        deleted_survey_id = None

        def get_survey_details(self, survey_id: str):
            if survey_id == "template_123":
                return {
                    "pages": [
                        {
                            "title": "Feedback",
                            "position": 1,
                            "questions": [
                                {
                                    "family": "single_choice",
                                    "subtype": "vertical",
                                    "headings": [{"heading": "Question"}],
                                    "answers": {"choices": [{"text": "Good"}]},
                                }
                            ],
                        }
                    ],
                }

            return {"pages": [{"id": "created_page_123"}]}

        def create_survey(self, title: str, category: str | None = None):
            return {"id": "created_survey_123"}

        def update_page(self, survey_id: str, page_id: str, payload: dict):
            return {"id": page_id}

        def create_question(self, survey_id: str, page_id: str, payload: dict):
            raise RuntimeError("question create failed")

        def delete_survey(self, survey_id: str):
            self.deleted_survey_id = survey_id
            return {}

    client = FakeClient()

    try:
        clone_surveymonkey_survey_from_template(
            client=client,
            template_survey_id="template_123",
            title="Generated survey",
        )
    except RuntimeError as exc:
        assert "page=1 question=1" in str(exc)
        assert "question create failed" in str(exc)
    else:
        raise AssertionError("clone should have raised")

    assert client.deleted_survey_id == "created_survey_123"
