
import pandas as pd
from io import BytesIO

from app.schemas.campaign import CampaignCandidateCreate
from pathlib import Path



SUPPORTED_EXTENSIONS = {".csv", ".xlsx"}


def normalize_column_name(column: str) -> str:
    return column.strip().lower().replace(" ", "_")


def normalize_nigerian_phone(value):
    phone = clean_cell(value)

    if phone is None:
        return None

    phone = phone.replace(" ", "").replace("-", "")

    if phone.startswith("+"):
        return phone

    if phone.startswith("234"):
        return f"+{phone}"

    if phone.startswith("0"):
        return f"+234{phone[1:]}"

    return phone


def clean_cell(value):
    if pd.isna(value):
        return None
    
    value = str(value).strip()
    return value or None


def parse_candidate_upload(
    file_bytes:  bytes,
    filename: str,
    default_tool_name: str | None = None,
    default_campaign_name: str | None = None
) -> list[CampaignCandidateCreate]:
    extension = Path(filename).suffix.lower()

    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError("Unsupported file type. Please upload a CSV or XLSX file.")
    
    if extension == ".csv":
        dataframe = pd.read_csv(BytesIO(file_bytes), dtype=str)
    else:
        dataframe = pd.read_excel(BytesIO(file_bytes), dtype=str)

    dataframe = dataframe.rename(columns=normalize_column_name)

    if "email" not in dataframe.columns:
        raise ValueError("Upload must include an email column.")
    
    candidates: list[CampaignCandidateCreate] = []

    for row in dataframe.to_dict(orient="records"):
        candidate = CampaignCandidateCreate(
            candidate_name=clean_cell(row.get("candidate_name")),
            email=clean_cell(row.get("email")),
            phone=normalize_nigerian_phone(row.get("phone")),
            tool_name=clean_cell(row.get("tool_name")) or default_tool_name,
            campaign_name=clean_cell(row.get("campaign_name")) or default_campaign_name,
            external_candidate_id=clean_cell(row.get("external_candidate_id")),
            opted_out_call=False,
            opted_out_email=False,
        )

        candidates.append(candidate)

    return candidates
