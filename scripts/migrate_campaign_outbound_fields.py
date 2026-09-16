"""Add the outbound call-context columns to an existing `campaign` table.

SQLModel's create_all() never alters an existing table, so fields added to the
Campaign model don't reach a DB that already has the table. This adds the new
nullable columns if they're missing. Idempotent — safe to run repeatedly.

    PYTHONPATH=. .venv/bin/python scripts/migrate_campaign_outbound_fields.py
"""

from sqlalchemy import text

from app.core.database import engine

# column name -> SQLite column type
NEW_COLUMNS = {
    "call_reason": "VARCHAR",
    "organization_name": "VARCHAR",
    "assessment_at": "DATETIME",
    "assessment_location": "VARCHAR",
    "practice_test_url": "VARCHAR",
    "contact_info": "VARCHAR",
}


def main() -> None:
    with engine.begin() as conn:
        existing = {
            row[1] for row in conn.execute(text("PRAGMA table_info(campaign)")).all()
        }
        added = []
        for column, col_type in NEW_COLUMNS.items():
            if column in existing:
                continue
            conn.execute(
                text(f"ALTER TABLE campaign ADD COLUMN {column} {col_type}")
            )
            added.append(column)

        if added:
            print(f"Added columns: {', '.join(added)}")
        else:
            print("All outbound columns already present — nothing to do.")


if __name__ == "__main__":
    main()
