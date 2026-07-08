"""Parsing of candidate bulk-upload files (CSV/XLSX) into candidate records.

Reads a user-uploaded spreadsheet of candidates, normalizes column names and
cell values, and converts each row into a CampaignCandidateCreate ready to be
persisted via campaign_service. Includes Nigeria-specific phone number
normalization since candidates are expected to be dialed via a Nigerian
outbound voice provider.
"""

import pandas as pd
from io import BytesIO

from app.schemas.campaign import CampaignCandidateCreate
from pathlib import Path



SUPPORTED_EXTENSIONS = {".csv", ".xlsx"}


def normalize_column_name(column: str) -> str:
    """Lowercase, trim, and snake_case a spreadsheet column header for matching."""
    return column.strip().lower().replace(" ", "_")


def normalize_nigerian_phone(value):
    """
    Normalize a raw phone cell value to E.164-ish Nigerian format.

    Strips spaces/hyphens, then: leaves values already starting with "+" as
    is, prefixes "234"-leading numbers with "+", and rewrites local
    "0"-leading numbers (e.g. "0803...") to "+234..." by dropping the
    leading 0. Returns None if the cell is empty/NaN; returns the
    best-effort cleaned string unchanged if it matches none of these
    patterns (e.g. malformed input).
    """
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
    """
    Coerce a pandas cell value to a trimmed string, or None if it is NaN
    (missing) or blank after stripping.
    """
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
    """
    Parse an uploaded CSV or XLSX file of candidates into a list of
    CampaignCandidateCreate objects.

    The file is selected by `filename`'s extension (must be .csv or .xlsx).
    All columns are read as strings (dtype=str) to avoid pandas' numeric/
    date coercion mangling phone numbers and IDs, then column headers are
    normalized (see normalize_column_name) so header casing/spacing in the
    uploaded file doesn't matter. An "email" column is required; other
    fields are optional and fall back to `default_tool_name`/
    `default_campaign_name` when blank. Performs no I/O beyond parsing the
    provided bytes; does not persist anything.

    Raises ValueError for an unsupported file extension or a missing email
    column.
    """
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
