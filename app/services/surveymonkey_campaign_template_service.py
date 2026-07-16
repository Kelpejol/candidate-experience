from sqlmodel import Session

from app.core.config import get_settings
from app.integrations.surveymonkey_client import SurveyMonkeyClient
from app.models.campaign import Campaign


READ_ONLY_SURVEYMONKEY_KEYS = {"id", "href"}
PAGE_PAYLOAD_FIELDS = ("title", "description", "position")
QUESTION_PAYLOAD_FIELDS = (
    "family",
    "subtype",
    "position",
    "visible",
    "headings",
    "answers",
    "required",
    "validation",
    "forced_ranking",
    "sorting",
    "layout",
)
CHOICE_PAYLOAD_FIELDS = {"text", "position", "visible", "quiz_options"}
MATRIX_RATING_CHOICE_PAYLOAD_FIELDS = {"text", "position", "weight"}
ROW_PAYLOAD_FIELDS = {"text", "position"}
OTHER_ANSWER_PAYLOAD_FIELDS = {
    "num_lines",
    "num_chars",
    "text",
    "is_answer_choice",
    "error_text",
}


def build_campaign_survey_title(campaign: Campaign) -> str:
    return f"{campaign.name} - Assessment Experience Feedback"


def build_campaign_template_variables(campaign: Campaign) -> dict[str, str]:
    return {
        "campaign_name": campaign.name,
        "campaign_title": campaign.name,
        "assessment_name": campaign.name,
        "tool_name": campaign.tool_name or "",
    }


def interpolate_template_placeholders(value, variables: dict[str, str]):
    if isinstance(value, str):
        interpolated = value

        for key, replacement in variables.items():
            interpolated = interpolated.replace(f"{{{{{key}}}}}", replacement)

        return interpolated

    if isinstance(value, dict):
        return {
            key: interpolate_template_placeholders(item, variables)
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [
            interpolate_template_placeholders(item, variables)
            for item in value
        ]

    return value


def remove_surveymonkey_read_only_fields(value):
    if isinstance(value, dict):
        cleaned = {}

        for key, item in value.items():
            if key in READ_ONLY_SURVEYMONKEY_KEYS:
                continue

            cleaned_item = remove_surveymonkey_read_only_fields(item)
            if cleaned_item is not None:
                cleaned[key] = cleaned_item

        return cleaned

    if isinstance(value, list):
        return [
            cleaned_item
            for item in value
            if (cleaned_item := remove_surveymonkey_read_only_fields(item)) is not None
        ]

    return value


def build_page_payload(
    template_page: dict,
    variables: dict[str, str] | None = None,
) -> dict:
    payload = {
        field: remove_surveymonkey_read_only_fields(template_page[field])
        for field in PAGE_PAYLOAD_FIELDS
        if template_page.get(field) is not None
    }

    if variables:
        payload = interpolate_template_placeholders(payload, variables)

    return payload


def build_question_payload(
    template_question: dict,
    variables: dict[str, str] | None = None,
) -> dict:
    payload = {
        field: remove_surveymonkey_read_only_fields(template_question[field])
        for field in QUESTION_PAYLOAD_FIELDS
        if template_question.get(field) is not None
    }

    if variables:
        payload = interpolate_template_placeholders(payload, variables)

    answers = payload.get("answers")
    if not isinstance(answers, dict):
        return payload

    choices = answers.get("choices")
    rows = answers.get("rows")
    other = answers.get("other")

    if isinstance(rows, list):
        answers["rows"] = [
            {
                key: value
                for key, value in row.items()
                if key in ROW_PAYLOAD_FIELDS and value is not None
            }
            for row in rows
            if isinstance(row, dict)
        ]

    if isinstance(other, dict):
        answers["other"] = [
            {
                key: value
                for key, value in other.items()
                if key in OTHER_ANSWER_PAYLOAD_FIELDS and value is not None
            }
        ]
    elif isinstance(other, list):
        answers["other"] = [
            {
                key: value
                for key, value in other_item.items()
                if key in OTHER_ANSWER_PAYLOAD_FIELDS and value is not None
            }
            for other_item in other
            if isinstance(other_item, dict)
        ]

    if not isinstance(choices, list):
        return payload

    allowed_choice_fields = CHOICE_PAYLOAD_FIELDS
    if payload.get("family") == "matrix" and payload.get("subtype") == "rating":
        allowed_choice_fields = MATRIX_RATING_CHOICE_PAYLOAD_FIELDS

    answers["choices"] = [
        {
            key: value
            for key, value in choice.items()
            if key in allowed_choice_fields and value is not None
        }
        for choice in choices
        if isinstance(choice, dict)
    ]

    return payload


def extract_surveymonkey_survey_id(payload: dict) -> str:
    survey_id = payload.get("id") or payload.get("survey_id")

    if not survey_id and isinstance(payload.get("data"), dict):
        survey_id = payload["data"].get("id") or payload["data"].get("survey_id")

    if not survey_id:
        raise RuntimeError("SurveyMonkey did not return a survey id")

    return str(survey_id)


def extract_surveymonkey_page_id(payload: dict) -> str:
    page_id = payload.get("id") or payload.get("page_id")

    if not page_id and isinstance(payload.get("data"), dict):
        page_id = payload["data"].get("id") or payload["data"].get("page_id")

    if not page_id:
        raise RuntimeError("SurveyMonkey did not return a page id")

    return str(page_id)


def get_http_error_body(exc: Exception) -> str:
    response = getattr(exc, "response", None)

    if response is None:
        return str(exc)

    return getattr(response, "text", "") or str(exc)


def clone_surveymonkey_survey_from_template(
    *,
    client: SurveyMonkeyClient,
    template_survey_id: str,
    title: str,
    variables: dict[str, str] | None = None,
) -> str:
    created_survey_id = None

    try:
        template_survey = client.get_survey_details(template_survey_id)
        created_survey = client.create_survey(
            title=title,
            category=template_survey.get("category"),
        )
        created_survey_id = extract_surveymonkey_survey_id(created_survey)
        created_survey_details = client.get_survey_details(created_survey_id)
        created_pages = created_survey_details.get("pages", [])

        for index, template_page in enumerate(template_survey.get("pages", [])):
            page_payload = build_page_payload(template_page, variables)

            if index == 0 and created_pages:
                created_page_id = str(created_pages[0]["id"])
                client.update_page(created_survey_id, created_page_id, page_payload)
            else:
                created_page = client.create_page(created_survey_id, page_payload)
                created_page_id = extract_surveymonkey_page_id(created_page)

            for question_index, template_question in enumerate(
                template_page.get("questions", []),
                start=1,
            ):
                try:
                    client.create_question(
                        created_survey_id,
                        created_page_id,
                        build_question_payload(template_question, variables),
                    )
                except Exception as exc:
                    raise RuntimeError(
                        "Failed to create SurveyMonkey question "
                        f"page={index + 1} question={question_index} "
                        f"family={template_question.get('family')} "
                        f"subtype={template_question.get('subtype')}: "
                        f"{get_http_error_body(exc)}"
                    ) from exc

        return created_survey_id
    except Exception:
        if created_survey_id:
            try:
                client.delete_survey(created_survey_id)
            except Exception:
                pass

        raise


def get_configured_campaign_template_survey_id(
    *,
    selected_template_survey_id: str | None = None,
) -> str:
    if selected_template_survey_id:
        return selected_template_survey_id

    settings = get_settings()

    if not settings.surveymonkey_campaign_template_survey_id:
        raise RuntimeError("SurveyMonkey campaign template survey id is not configured")

    return settings.surveymonkey_campaign_template_survey_id


def list_surveymonkey_template_surveys() -> list[dict]:
    settings = get_settings()

    if not settings.surveymonkey_access_token:
        raise RuntimeError("SurveyMonkey access token is not configured")

    client = SurveyMonkeyClient(
        base_url=settings.surveymonkey_base_url,
        access_token=settings.surveymonkey_access_token,
    )

    return [
        {
            "id": str(survey["id"]),
            "title": survey.get("title", ""),
            "nickname": survey.get("nickname"),
        }
        for survey in client.list_all_surveys()
        if survey.get("title", "").startswith("TEMPLATE -")
    ]


def create_campaign_survey_from_template(
    *,
    campaign: Campaign,
    session: Session,
    template_survey_id: str | None = None,
) -> Campaign:
    settings = get_settings()

    if not settings.surveymonkey_access_token:
        raise RuntimeError("SurveyMonkey access token is not configured")

    if campaign.survey_id:
        return campaign

    resolved_template_survey_id = get_configured_campaign_template_survey_id(
        selected_template_survey_id=template_survey_id,
    )

    client = SurveyMonkeyClient(
        base_url=settings.surveymonkey_base_url,
        access_token=settings.surveymonkey_access_token,
    )

    campaign.survey_id = clone_surveymonkey_survey_from_template(
        client=client,
        template_survey_id=resolved_template_survey_id,
        title=build_campaign_survey_title(campaign),
        variables=build_campaign_template_variables(campaign),
    )
    session.add(campaign)
    session.commit()
    session.refresh(campaign)

    return campaign
