"""Add `zoho_modified_at` to an existing `helpdeskticketmirror` table.

SQLModel's create_all() never alters an existing table. This column is what
tells the pipeline a ticket actually CHANGED (as opposed to merely being
re-synced), so without it every open ticket is reprocessed on every cron tick.

Idempotent — safe to run repeatedly. Existing rows are backfilled to NULL,
which reads as "unchanged": already-decided tickets stay put until Zoho reports
a real modification, and never-decided tickets are picked up as usual.

    PYTHONPATH=. .venv/bin/python scripts/migrate_ticket_mirror_modified_at.py
"""

from sqlalchemy import text

from app.core.database import engine

TABLE = "helpdeskticketmirror"
COLUMN = "zoho_modified_at"


def main() -> None:
    with engine.begin() as conn:
        existing = {
            row[1] for row in conn.execute(text(f"PRAGMA table_info({TABLE})")).all()
        }
        if not existing:
            print(f"Table '{TABLE}' does not exist yet — create_all will build it.")
            return
        if COLUMN in existing:
            print(f"Column '{COLUMN}' already present — nothing to do.")
            return

        conn.execute(text(f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} DATETIME"))
        print(f"Added column '{COLUMN}' to {TABLE}.")


if __name__ == "__main__":
    main()
