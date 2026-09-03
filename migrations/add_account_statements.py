"""
Database migration script to add the account_statements table (see
models/billing.py AccountStatement) - a lightweight chain-of-custody audit
log for generated account statements: who generated one, for which tenant
and date range, when, and with what unique reference number (STMT-00001
style, same numbering convention as invoices/quotes/contracts).

Pure additive CREATE TABLE, idempotent, no existing tables altered and no
rows backfilled - same convention as migrations/add_contracts_table.py.
Column types match the AccountStatement model in models/billing.py exactly,
so the live schema and the ORM model agree.

This table stores ONLY the audit metadata of the generation event (reference
number, who, when, for what range) - never the computed money figures
themselves, which are always recomputed live from the real Invoice/Contract
rows at view/print/export time. See the AccountStatement docstring in
models/billing.py for why that is the deliberate, honest choice here.
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

TABLE_NAME = 'account_statements'


def run_migration():
    """Create the account_statements table (idempotent)."""
    print("Starting migration to add the account_statements table...")

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
            print(f"Creating {TABLE_NAME} table...")
            cursor.execute("""
                CREATE TABLE account_statements (
                    id SERIAL PRIMARY KEY,
                    reference_number VARCHAR(20) UNIQUE NOT NULL,
                    owner_id INTEGER NOT NULL REFERENCES users(id),
                    generated_by_id INTEGER NOT NULL REFERENCES users(id),
                    client_id INTEGER REFERENCES clients(id),
                    period_start DATE NOT NULL,
                    period_end DATE NOT NULL,
                    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    format VARCHAR(10)
                )
            """)
        else:
            print(f"{TABLE_NAME} table already exists.")

        conn.commit()

        # Report what the live table actually looks like now, rather than
        # assuming the CREATE did what was intended (same reporting
        # convention as migrations/add_contract_amendments.py).
        cursor.execute(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_name = %s ORDER BY ordinal_position",
            (TABLE_NAME,)
        )
        live = cursor.fetchall()

        cursor.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}")
        total = cursor.fetchone()[0]

        print("Migration completed successfully!")
        print("Summary:")
        print("  live column definitions:")
        for row in live:
            print(f"    {row[0]}: type={row[1]}, nullable={row[2]}, default={row[3]}")
        print(f"  account_statements rows: {total}")

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
