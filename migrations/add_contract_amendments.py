"""
Database migration script to add the contract AMENDMENT / VOID columns to the
contracts table (see models/billing.py Contract):

  supersedes_id INTEGER REFERENCES contracts(id)  - self-referential, nullable
  version       INTEGER DEFAULT 1                 - 1 for an original, N+1 for an amendment
  voided_at     TIMESTAMP                         - when the sender withdrew it

Why these exist: a contract is editable only while draft, and stays immutable
once sent - a signature is meaningless if the text can change under it. So
"changing" a sent/signed/declined contract means issuing a NEW draft that
points back at the one it replaces (supersedes_id) and carries the next
version number, leaving the original's status, signature and audit trail
completely untouched as permanent history.

NO ENUM DDL IS NEEDED for the new 'voided' status. Contract.status is a plain
VARCHAR(20) column (see migrations/add_contracts_table.py) and ContractStatus
in models/billing.py is a plain constants class like QuoteStatus/InvoiceStatus
- NOT a Postgres enum type like ProjectStatus/TaskStatus/UserRole in
models/models.py. The database already accepts any string that fits, so adding
the VOIDED constant is purely an application-level change and this migration
only has to add the three columns above.

Additive and nullable throughout, no rows rewritten by hand: version is added
with DEFAULT 1, which Postgres applies to existing rows too, so every contract
that already exists reads as version 1 (and models/billing.py's
Contract.version_number treats a NULL as 1 anyway, for safety). Idempotent,
same convention as migrations/add_role_title.py.
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

TABLE_NAME = 'contracts'

# (column_name, DDL) - the types match the columns declared on Contract in
# models/billing.py exactly, so the live schema and the ORM model agree.
NEW_COLUMNS = [
    ('supersedes_id', 'INTEGER REFERENCES contracts(id)'),
    ('version', 'INTEGER DEFAULT 1'),
    ('voided_at', 'TIMESTAMP'),
]


def run_migration():
    """Add supersedes_id / version / voided_at to contracts (idempotent, nullable)."""
    print("Starting migration to add contract amendment/void columns...")

    conn = None
    cursor = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()

        cursor.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s)",
            (TABLE_NAME,)
        )
        if not cursor.fetchone()[0]:
            # Never create the table here - migrations/add_contracts_table.py
            # owns the contracts table and must run first.
            print(f"ERROR: table {TABLE_NAME} does not exist - run migrations/add_contracts_table.py first.")
            sys.exit(1)

        cursor.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
            (TABLE_NAME,)
        )
        existing_columns = [col[0] for col in cursor.fetchall()]

        added = []
        skipped = []
        for column_name, ddl_type in NEW_COLUMNS:
            if column_name not in existing_columns:
                print(f"Adding {column_name} column to {TABLE_NAME} table...")
                cursor.execute(f"ALTER TABLE {TABLE_NAME} ADD COLUMN {column_name} {ddl_type}")
                added.append(column_name)
            else:
                print(f"{column_name} column already exists on {TABLE_NAME}.")
                skipped.append(column_name)

        conn.commit()

        # Report what the live table actually looks like now, rather than
        # assuming the ALTERs did what was intended.
        cursor.execute(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_name = %s AND column_name IN "
            "('supersedes_id', 'version', 'voided_at') ORDER BY column_name",
            (TABLE_NAME,)
        )
        live = cursor.fetchall()

        cursor.execute("SELECT COUNT(*) FROM contracts")
        total = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM contracts WHERE version IS NULL")
        null_versions = cursor.fetchone()[0]

        print("Migration completed successfully!")
        print("Summary:")
        print(f"  columns added: {added if added else 'none'}")
        print(f"  columns already present: {skipped if skipped else 'none'}")
        print("  live column definitions:")
        for row in live:
            print(f"    {row[0]}: type={row[1]}, nullable={row[2]}, default={row[3]}")
        print(f"  contracts rows: {total} (version IS NULL on {null_versions})")
        print("  status column: unchanged VARCHAR(20) - 'voided' needs no enum DDL")

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
