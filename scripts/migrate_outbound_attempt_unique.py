"""Add the unique index on OutboundCallAttempt(campaign_id, candidate_id,
attempt_number) to an existing database.

SQLModel's create_all() creates missing tables but never alters an existing
one, so a constraint added to the model doesn't reach a DB that already has the
table. This applies it directly. On SQLite a UNIQUE INDEX enforces exactly what
the model's UniqueConstraint does.

Safe to run repeatedly (IF NOT EXISTS). It refuses to run if duplicate rows
already exist, printing them so you can clean up first.

    PYTHONPATH=. .venv/bin/python scripts/migrate_outbound_attempt_unique.py
"""

from sqlalchemy import text

from app.core.database import engine

INDEX_NAME = "uq_outbound_attempt_campaign_candidate_number"
FIND_DUPLICATES = text(
    """
    SELECT campaign_id, candidate_id, attempt_number, COUNT(*) AS n
    FROM outboundcallattempt
    GROUP BY campaign_id, candidate_id, attempt_number
    HAVING n > 1
    """
)
CREATE_INDEX = text(
    f"""
    CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_NAME}
    ON outboundcallattempt (campaign_id, candidate_id, attempt_number)
    """
)


def main() -> None:
    with engine.begin() as conn:
        duplicates = conn.execute(FIND_DUPLICATES).all()
        if duplicates:
            print("Refusing to add the unique index — duplicate attempts exist:")
            for campaign_id, candidate_id, attempt_number, count in duplicates:
                print(
                    f"  campaign={campaign_id} candidate={candidate_id} "
                    f"attempt_number={attempt_number} count={count}"
                )
            print("Clean these up (keep one of each), then re-run.")
            return

        conn.execute(CREATE_INDEX)
        print(f"Unique index '{INDEX_NAME}' is in place.")


if __name__ == "__main__":
    main()
