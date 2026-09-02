"""
Database migration script to add the contracts table (see models/billing.py
Contract - same document family and conventions as Quote/Invoice: CT-00001
numbering, tokenized no-login portal link via public_token, and the
body_snapshot frozen at send time that becomes the legal text the client
signs). Pure additive CREATE TABLE, idempotent, no existing tables altered
and no rows backfilled - same convention as migrations/add_team_membership.py.
Column types match the Contract model in models/billing.py exactly, so the
live schema and the ORM model agree.
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


def run_migration():
    """Create the contracts table (idempotent)."""
    print("Starting migration to add the contracts table...")

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()

        cursor.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'contracts')")
        if not cursor.fetchone()[0]:
            print("Creating contracts table...")
            cursor.execute("""
                CREATE TABLE contracts (
                    id SERIAL PRIMARY KEY,
                    contract_number VARCHAR(20) UNIQUE NOT NULL,
                    client_id INTEGER NOT NULL REFERENCES clients(id),
                    project_id INTEGER REFERENCES projects(id),
                    created_by_id INTEGER NOT NULL REFERENCES users(id),
                    title VARCHAR(150) NOT NULL,
                    body TEXT NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'draft',
                    public_token VARCHAR(64) UNIQUE NOT NULL,
                    body_snapshot TEXT,
                    sent_at TIMESTAMP,
                    signed_at TIMESTAMP,
                    declined_at TIMESTAMP,
                    decline_reason TEXT,
                    signer_name VARCHAR(150),
                    signature_image TEXT,
                    signer_ip VARCHAR(64),
                    signer_user_agent VARCHAR(300),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        else:
            print("contracts table already exists.")

        conn.commit()
        print("Migration completed successfully!")

    except Exception as e:
        print(f"ERROR: {str(e)}")
        conn.rollback()
        sys.exit(1)
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    run_migration()
