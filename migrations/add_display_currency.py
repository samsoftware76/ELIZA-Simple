"""
Database migration script to add users.display_currency - a PERSONAL
display-currency viewing preference on the user's OWN row. Explicitly NOT
owner-resolved (unlike default_currency and the other tenant-level fields):
a team member can view staff-side amounts in their own currency without
changing anything about the tenant's data. Purely display-layer - stored
currencies, PesaPal charging, and every real amount stay exactly as they
are; the column only drives the approximate "(~ USD 135.20)" text shown
next to real amounts (see filters.approx_display / utils/exchange_rates.py).
Nullable/additive, NULL means "off", no backfill needed - same idempotent
convention as migrations/add_invoice_defaults.py.
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

# (column_name, DDL type) - matches the column type on User in
# models/models.py exactly, so the live schema and the ORM model agree.
NEW_COLUMNS = [
    ('display_currency', 'VARCHAR(10)'),
]


def run_migration():
    """Add the display_currency column to users (idempotent, nullable, no backfill)."""
    print("Starting migration to add display_currency to users...")

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
