"""Print the current helpdesk automation summary without starting the server.

Run with: python scripts/check_helpdesk_reports.py
"""

import json

from sqlmodel import Session

from app.core.database import engine
from app.services.helpdesk_reporting_service import get_helpdesk_summary

with Session(engine) as session:
    print(json.dumps(get_helpdesk_summary(session), indent=2))
