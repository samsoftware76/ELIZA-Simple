"""
Database migration script to add users.is_active - the account deactivation
(soft delete) flag. See the is_active column on User in models/models.py.

Until now User.is_active was a hardcoded @property returning True, present
only to satisfy Flask-Login's UserMixin contract. This makes it a real column
so an account can be switched off (kept, hidden, unable to log in) instead of
being hard-deleted along with its whole history.

Pure additive ALTER TABLE ADD COLUMN, idempotent, nothing dropped or renamed -
same convention as migrations/add_role_title.py and
migrations/add_display_currency.py.

THE ONE THING THAT MATTERS MOST HERE: every EXISTING row must end up TRUE.
Getting that wrong locks real users out of a live app. Postgres's
`ADD COLUMN ... BOOLEAN NOT NULL DEFAULT TRUE` fills every existing row with
TRUE as part of the same statement (and, since PG 11, without a table
rewrite), so there is no window in which an existing row is NULL or FALSE.
The verification block at the end re-reads the live table and prints the real
per-row values rather than trusting that the ALTER did what was intended.

Re-run safe in every partial state: if the column already exists this fills
any NULLs, (re)asserts the DEFAULT TRUE, and (re)asserts NOT NULL - so a
half-applied earlier run converges to the same end state rather than being
left alone.
"""
import os
import sys
import psycopg2
from dotenv import load_dotenv

# Add the parent directory to sys.path
parent_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

# Load environment variables
dotenv_path = os.path.join(parent_dir, '.env')
load_dotenv(dotenv_path)

# Get database URL from environment variable
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    print("ERROR: DATABASE_URL environment variable not set.")
    sys.exit(1)

TABLE_NAME = 'users'
COLUMN_NAME = 'is_active'


def run_migration():
    """Add users.is_active (BOOLEAN NOT NULL DEFAULT TRUE), idempotent."""
    print("Starting migration to add users.is_active...")

    conn = None
    cursor = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()

        # How many rows exist BEFORE we touch anything - printed next to the
        # active/inactive tally at the end so "every existing user is still
        # able to log in" is a checkable statement, not a claim.
        cursor.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}")
        rows_before = cursor.fetchone()[0]
        print(f"users rows before: {rows_before}")

        cursor.execute(
            "SELECT EXISTS (SELECT FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = %s)",
            (TABLE_NAME, COLUMN_NAME)
        )
        column_exists = cursor.fetchone()[0]

        if not column_exists:
            print("Adding users.is_active BOOLEAN NOT NULL DEFAULT TRUE...")
            # DEFAULT TRUE is what backfills the existing rows: Postgres
            # applies the default to every existing row as part of this one
            # statement, so no separate UPDATE is needed and no row is ever
            # observed NULL.
            cursor.execute(
                "ALTER TABLE users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE"
            )
        else:
            print("users.is_active already exists - converging it to the intended state.")
            # Any of these may already be true; all three are no-ops if so.
            cursor.execute("UPDATE users SET is_active = TRUE WHERE is_active IS NULL")
            print(f"  filled {cursor.rowcount} NULL is_active row(s) with TRUE")
            cursor.execute("ALTER TABLE users ALTER COLUMN is_active SET DEFAULT TRUE")
            cursor.execute("ALTER TABLE users ALTER COLUMN is_active SET NOT NULL")

        conn.commit()

        # Report what the live table actually looks like now, rather than
        # assuming the ALTER did what was intended (same reporting convention
        # as migrations/add_account_statements.py).
        cursor.execute(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
            (TABLE_NAME, COLUMN_NAME)
        )
        live = cursor.fetchall()

        cursor.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}")
        rows_after = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_active IS TRUE")
        active_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_active IS NOT TRUE")
        not_active_count = cursor.fetchone()[0]

        cursor.execute("SELECT id, username, email, is_active FROM users ORDER BY id")
        per_row = cursor.fetchall()

        print("Migration completed successfully!")
        print("Summary:")
        print("  live column definition:")
        for row in live:
            print(f"    {row[0]}: type={row[1]}, nullable={row[2]}, default={row[3]}")
        print(f"  users rows before: {rows_before}, after: {rows_after}")
        print(f"  is_active TRUE: {active_count}, NOT TRUE (false or null): {not_active_count}")
        print("  per-row is_active:")
        for row in per_row:
            print(f"    id={row[0]} username={row[1]} email={row[2]} is_active={row[3]}")

        if not_active_count:
            print("  NOTE: at least one row is not TRUE - that account cannot log in.")

    except SystemExit:
        raise
    except Exception as e:
        print(f"ERROR: {str(e)}")
        if conn:
            conn.rollback()
        sys.exit(1)
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


if __name__ == "__main__":
    run_migration()
