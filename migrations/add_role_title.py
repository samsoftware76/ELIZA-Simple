"""
Database migration script to add a DISPLAY-ONLY custom role title to
team_memberships and team_invites: role_title VARCHAR(60), nullable.

Why a free-text column instead of new UserRole enum values: users asked for
roles like Accountant / Finance / Marketer / Designer, but UserRole is a
Postgres enum whose values carry real security meaning - ADMIN is what
api/admin.py's admin_required checks for platform super-admin, and CLIENT is
what the before_request portal gate in api/index.py checks. Adding values to
that enum (or letting a typed title reach it) would put arbitrary strings
into a security-relevant type. So role_title is a LABEL only: it is never
read by any permission check, it only changes what is printed. The machine
role column stays exactly as it is and remains the only thing that decides
access.

Both tables get the column because the title is chosen at invite time and has
to survive through to acceptance: api/team.py invite() writes it on the
TeamInvite, and accept_invite() in api/index.py copies it onto the
TeamMembership it creates.

Additive and nullable, no backfill: NULL simply means "no custom title", and
every display site falls back to the humanized machine role (see
TeamMembership.display_role / TeamInvite.display_role in models/models.py).
Idempotent, same convention as migrations/add_invoice_defaults.py.
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

# (table_name, column_name, DDL type) - the type matches the column declared
# on TeamMembership/TeamInvite in models/models.py exactly, so the live schema
# and the ORM model agree.
NEW_COLUMNS = [
    ('team_memberships', 'role_title', 'VARCHAR(60)'),
    ('team_invites', 'role_title', 'VARCHAR(60)'),
]


def run_migration():
    """Add role_title to team_memberships and team_invites (idempotent, nullable, no backfill)."""
    print("Starting migration to add role_title to team_memberships and team_invites...")

    conn = None
    cursor = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()

        added = []
        skipped = []
        missing_tables = []
        for table_name, column_name, ddl_type in NEW_COLUMNS:
            cursor.execute(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s)",
                (table_name,)
            )
            if not cursor.fetchone()[0]:
                # Never create the table here - migrations/add_team_membership.py
                # owns these two tables and must run first.
                print(f"WARNING: table {table_name} does not exist - run migrations/add_team_membership.py first.")
                missing_tables.append(table_name)
                continue

            cursor.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
                (table_name,)
            )
            existing_columns = [col[0] for col in cursor.fetchall()]

            if column_name not in existing_columns:
                print(f"Adding {column_name} column to {table_name} table...")
                cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl_type}")
                added.append(f"{table_name}.{column_name}")
            else:
                print(f"{column_name} column already exists on {table_name}.")
                skipped.append(f"{table_name}.{column_name}")

        conn.commit()
        print("Migration completed successfully!")
        print("Summary:")
        print(f"  columns added: {added if added else 'none'}")
        print(f"  columns already present: {skipped if skipped else 'none'}")
        if missing_tables:
            print(f"  tables missing (skipped): {missing_tables}")

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
