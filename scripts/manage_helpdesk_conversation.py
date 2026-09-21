"""Local operator inspection, reconciliation and explicit handback to automation.

Never sends a message. Resume applies to FUTURE candidate events, not an old reply.
Run with PYTHONPATH=. .venv/bin/python scripts/manage_helpdesk_conversation.py --help
"""

import argparse
import json
from datetime import datetime
from uuid import uuid4

from filelock import FileLock
from pathlib import Path
from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.database import engine
from app.models.helpdesk_ai_action import HelpdeskAIAction
from app.models.helpdesk_conversation_turn import HelpdeskConversationTurn
from app.services.helpdesk_conversation_service import _hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "inspect", "reconcile", "resume"])
    parser.add_argument("--ticket")
    parser.add_argument("--turn")
    parser.add_argument("--operator")
    parser.add_argument("--reason")
    parser.add_argument("--outcome", choices=["sent", "not_sent"])
    parser.add_argument("--reply-id")
    parser.add_argument("--mode", choices=["dry_run", "draft", "email", "whatsapp"], default="dry_run")
    args = parser.parse_args()
    if args.command == "init":
        HelpdeskConversationTurn.__table__.create(engine, checkfirst=True)
        print("Conversation delivery table ready. No execute flags changed.")
        return
    if not args.ticket:
        parser.error("--ticket is required")
    if args.command in {"reconcile", "resume"} and not (args.operator and args.reason):
        parser.error("--operator and --reason are required for audit")
    if args.command == "reconcile" and not (args.turn and args.outcome):
        parser.error("--turn and --outcome are required")
    if args.outcome == "sent" and not args.reply_id:
        parser.error("--reply-id is required when confirming delivery")
    settings = get_settings()
    identity = _hash(settings.zoho_org_id or "default", args.ticket)
    path = Path(settings.helpdesk_checkpoint_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path.parent / f"helpdesk-{identity}.lock"), timeout=0), Session(engine) as session:
        turns = session.exec(select(HelpdeskConversationTurn).join(
            HelpdeskAIAction, HelpdeskAIAction.id == HelpdeskConversationTurn.action_id
        ).where(HelpdeskAIAction.zoho_ticket_id == args.ticket)
         .order_by(HelpdeskConversationTurn.created_at.desc())).all()
        if args.command == "inspect":
            print(json.dumps([{"turn": t.id, "message": t.candidate_message_id,
                               "mode": t.delivery_mode, "status": t.delivery_status,
                               "remote_reply_id": t.remote_reply_id, "action": t.action_id} for t in turns], indent=2))
            return
        if args.command == "reconcile":
            turn = next((t for t in turns if t.id == args.turn), None)
            if not turn or turn.delivery_status not in {"uncertain", "sending"}:
                parser.error("Turn must belong to this ticket and have an uncertain/sending status")
            action = session.get(HelpdeskAIAction, turn.action_id)
            turn.delivery_status = "sent" if args.outcome == "sent" else "cancelled"
            turn.remote_reply_id = args.reply_id
            turn.updated_at = datetime.utcnow()
            action.executed = args.outcome == "sent"
            action.reason = (action.reason or "") + f"\nReconciled by {args.operator}: {args.outcome}. {args.reason}"
            session.add_all([turn, action])
        else:
            if any(t.delivery_status in {"pending", "sending", "uncertain"} for t in turns):
                parser.error("Reconcile outstanding delivery intents before resuming")
            action = HelpdeskAIAction(zoho_ticket_id=args.ticket, action_type="tag_only",
                rule="conversation_resumed", reason=f"Resumed by {args.operator}: {args.reason}")
            turn = HelpdeskConversationTurn(id=str(uuid4()), conversation_id=_hash(identity, args.mode),
                candidate_message_id="operator-resume", action_id=action.id, delivery_status="not_required", delivery_mode=args.mode)
            session.add_all([action, turn])
        session.commit()
        print("Operator decision recorded. No message sent; ownership checks still apply.")


if __name__ == "__main__":
    main()
