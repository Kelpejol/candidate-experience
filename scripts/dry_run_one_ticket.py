"""Run ONE real Zoho ticket through the full AI pipeline as a dry run, and
print exactly what it decided — without sending or writing anything to Zoho.

Safe by construction, not by care taken when running it: this only works
because helpdesk_draft_execute / helpdesk_tag_execute /
helpdesk_whatsapp_auto_reply_execute are False, which execute_ticket_action
checks before writing anything real. This script does not touch those flags.
It DOES write locally (a HelpdeskAIAction row + the mirror's classification
fields) — that's the same audit trail a real pipeline run leaves, and is what
lets you inspect the exact draft this ticket produced.

Run: PYTHONPATH=. .venv/bin/python scripts/dry_run_one_ticket.py <zoho_ticket_id>
"""

import sys

from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.database import engine
from app.integrations.zoho_desk_client import build_zoho_desk_client
from app.models.helpdesk_ticket_mirror import HelpdeskTicketMirror
from app.services.helpdesk_ai_service import process_ticket

if len(sys.argv) != 2:
    raise SystemExit("Usage: python scripts/dry_run_one_ticket.py <zoho_ticket_id>")

zoho_ticket_id = sys.argv[1]
settings = get_settings()

for flag, name in [
    (settings.helpdesk_draft_execute, "HELPDESK_DRAFT_EXECUTE"),
    (settings.helpdesk_whatsapp_auto_reply_execute, "HELPDESK_WHATSAPP_AUTO_REPLY_EXECUTE"),
]:
    if flag:
        raise SystemExit(
            f"{name} is currently True — this script is for a DRY RUN. "
            "Set it back to False before using this to preview a draft."
        )

with Session(engine) as session:
    mirror = session.exec(
        select(HelpdeskTicketMirror).where(HelpdeskTicketMirror.zoho_ticket_id == zoho_ticket_id)
    ).first()
    if mirror is None:
        raise SystemExit(
            f"No local mirror for ticket {zoho_ticket_id!r} — run the backfill/sync first."
        )

    zoho_client = build_zoho_desk_client(settings)
    action = process_ticket(session, zoho_client, mirror)
    session.commit()

    print(f"Ticket:          {zoho_ticket_id}")
    print(f"Subject:         {mirror.subject!r}")
    print(f"Channel:         {mirror.channel}")
    print(f"Candidate email: {mirror.candidate_email}")
    print()
    print(f"Classification:  issue_category={action.issue_category!r} "
          f"tool_name={action.tool_name!r} campaign_name={action.campaign_name!r} "
          f"confidence={action.confidence_label!r}")
    print(f"Decision:        action={action.action_type!r} rule={action.rule!r}")
    print(f"Reason:          {action.reason}")
    print(f"Grounding:       {action.grounding_status}")
    tag_note = " (tags/priority — HELPDESK_TAG_EXECUTE is on)" if action.executed else ""
    print(f"Executed:        {action.executed}{tag_note}")
    print("                 (drafts/replies never sent regardless — this script refuses to run "
          "if those flags are on)")
    print()
    if action.draft_text:
        print("=" * 70)
        print("DRAFT TEXT (this is what would be sent/drafted):")
        print("=" * 70)
        print(action.draft_text)
    else:
        print("(no draft text — action was not draft_reply/auto_reply)")
