from io import BytesIO

import pandas as pd
import pytest

from app.services.candidate_import_service import (
    normalize_nigerian_phone,
    parse_candidate_upload,
)

def test_parse_candidate_upload_supports_csv():
    file_bytes = (
        "candidate_name,email,phone,external_candidate_id\n"
        "Ada Lovelace,ada@example.com,2348012345678,cand_001\n"
    ).encode("utf-8")

    candidates = parse_candidate_upload(
        file_bytes=file_bytes,
        filename="candidates.csv",
        default_tool_name="FOT",
        default_campaign_name="Graduate Test",
    )

    assert len(candidates) == 1
    assert candidates[0].candidate_name == "Ada Lovelace"
    assert str(candidates[0].email) == "ada@example.com"
    assert candidates[0].phone == "+2348012345678"
    assert candidates[0].tool_name == "FOT"
    assert candidates[0].campaign_name == "Graduate Test"
    assert candidates[0].external_candidate_id == "cand_001"


def test_parse_candidate_upload_supports_xlsx():
    output = BytesIO()

    dataframe = pd.DataFrame(
        [
            {
                "candidate_name": "Grace Hopper",
                "email": "grace@example.com",
                "phone": "+2348098765432",
                "external_candidate_id": "cand_002",
            }
        ]
    )

    dataframe.to_excel(output, index=False)

    candidates = parse_candidate_upload(
        file_bytes=output.getvalue(),
        filename="candidates.xlsx",
        default_tool_name="FOT",
        default_campaign_name="Graduate Test",
    )

    assert len(candidates) == 1
    assert candidates[0].candidate_name == "Grace Hopper"
    assert str(candidates[0].email) == "grace@example.com"


def test_parse_candidate_upload_rejects_missing_email_column():
  
    file_bytes = (
        "candidate_name,phone,external_candidate_id\n"
        "Ada Lovelace,2348012345678,cand_001\n"
    ).encode("utf-8")

    with pytest.raises(ValueError, match="email column"):
        parse_candidate_upload(
            file_bytes=file_bytes,
            filename="candidates.csv",
        )


def test_parse_candidate_upload_rejects_unknown_file_type():
    with pytest.raises(ValueError, match="Unsupported file type"):
        parse_candidate_upload(
            file_bytes=b"hello",
            filename="candidates.txt",
        )


def test_normalize_nigerian_phone():
    assert normalize_nigerian_phone("2348012345678") == "+2348012345678"
    assert normalize_nigerian_phone("08012345678") == "+2348012345678"
    assert normalize_nigerian_phone("+2348012345678") == "+2348012345678"
    assert normalize_nigerian_phone(None) is None