"""
Database migration script to add tenant-level invoice default fields to the
users table: default_currency, default_tax_rate, default_payment_terms_days.
Set on the OWNER row via the /profile invoice-defaults card (owner-only) and
read through get_owner_id() to prefill empty quote/invoice forms and the
auto-invoice created when a client accepts a quote - defaults only ever fill
initial values, explicit user input always wins. All nullable/additive - same
idempotent convention as migrations/add_mobile_money_fields.py, no backfill
needed since NULL simply means "no default set".
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

# (column_name, DDL type) - matches the column types on User in
# models/models.py exactly, so the live schema and the ORM model agree.
NEW_COLUMNS = [
    ('default_currency', 'VARCHAR(10)'),
    ('default_tax_rate', 'DOUBLE PRECISION'),
    ('default_payment_terms_days', 'INTEGER'),
]


def run_migration():
    """Add the 3 invoice-default columns to users (idempotent, nullable, no backfill)."""
    print("Starting migration to add invoice default fields to users...")

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()

        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'users'")
        existing_columns = [col[0] for col in cursor.fetchall()]

        added = []
        skipped = []
        for column_name, ddl_type in NEW_COLUMNS:
            if column_name not in existing_columns:
                print(f"Adding {column_name} column to users table...")
                cursor.execute(f"ALTER TABLE users ADD COLUMN {column_name} {ddl_type}")
                added.append(column_name)
            else:
                print(f"{column_name} column already exists on users.")
                skipped.append(column_name)

        conn.commit()
        print("Migration completed successfully!")
        print("Summary:")
        print(f"  columns added: {added if added else 'none'}")
        print(f"  columns already present: {skipped if skipped else 'none'}")

    except Exception as e:
        print(f"ERROR: {str(e)}")
        conn.rollback()
        sys.exit(1)
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    run_migration()
