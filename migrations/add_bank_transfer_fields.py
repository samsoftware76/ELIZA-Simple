"""
Database migration script to add manual "pay by bank transfer" fields to the
users table: bank_name, bank_account_name, bank_account_number,
bank_swift_code, bank_transfer_fee. Tenant-level settings shown to clients on
the invoice portal as an alternative to the PesaPal button (see
models/models.py's User class). All nullable/additive - same idempotent
convention as migrations/add_tenant_ownership.py, no backfill needed since
this is a purely opt-in feature.
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

# (column_name, DDL type) - matches the String/Float lengths on User in
# models/models.py exactly, so the live schema and the ORM model agree.
NEW_COLUMNS = [
    ('bank_name', 'VARCHAR(150)'),
    ('bank_account_name', 'VARCHAR(150)'),
    ('bank_account_number', 'VARCHAR(50)'),
    ('bank_swift_code', 'VARCHAR(20)'),
    ('bank_transfer_fee', 'DOUBLE PRECISION'),
]


def run_migration():
    """Add the 5 bank-transfer columns to users (idempotent, nullable, no backfill)."""
    print("Starting migration to add bank transfer fields to users...")

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
