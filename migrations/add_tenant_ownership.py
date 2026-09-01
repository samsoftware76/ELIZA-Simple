"""
Database migration script to add tenant ownership (owner_id) columns to the
clients and projects tables, and backfill existing rows to a single known
owner so multi-tenancy can be layered on without orphaning pre-existing data.
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

# Every row that exists before multi-tenancy was added belongs to the original
# (and, at the time of writing, only) user of the app. Backfilling to their
# account keeps existing clients/projects visible to their current owner
# instead of disappearing behind a NULL once tenant filtering is switched on.
BACKFILL_OWNER_EMAIL = 'smugabi22@gmail.com'

def run_migration():
    """Add owner_id to clients/projects (idempotent) and backfill NULLs."""
    print("Starting migration to add tenant ownership columns...")

    # Connect to the database
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()

        # --- clients.owner_id ---
        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'clients'")
        clients_columns = [col[0] for col in cursor.fetchall()]

        if 'owner_id' not in clients_columns:
            print("Adding owner_id column to clients table...")
            # Nullable on purpose: any in-flight code from before this deploy
            # (or a request mid-flight during the deploy) that inserts a
            # client without setting owner_id must not start failing inserts.
            cursor.execute(
                "ALTER TABLE clients ADD COLUMN owner_id INTEGER "
                "REFERENCES users(id)"
            )
        else:
            print("owner_id column already exists on clients.")

        # --- projects.owner_id ---
        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'projects'")
        projects_columns = [col[0] for col in cursor.fetchall()]

        if 'owner_id' not in projects_columns:
            print("Adding owner_id column to projects table...")
            cursor.execute(
                "ALTER TABLE projects ADD COLUMN owner_id INTEGER "
                "REFERENCES users(id)"
            )
        else:
            print("owner_id column already exists on projects.")

        # --- Backfill ---
        cursor.execute("SELECT id FROM users WHERE email = %s", (BACKFILL_OWNER_EMAIL,))
        owner_row = cursor.fetchone()
        if not owner_row:
            print(f"ERROR: no user found with email {BACKFILL_OWNER_EMAIL}; cannot backfill.")
            conn.rollback()
            sys.exit(1)
        owner_id = owner_row[0]
        print(f"Backfilling existing rows to owner_id={owner_id} ({BACKFILL_OWNER_EMAIL})...")

        cursor.execute("UPDATE clients SET owner_id = %s WHERE owner_id IS NULL", (owner_id,))
        clients_updated = cursor.rowcount
        print(f"Backfilled {clients_updated} row(s) in clients.")

        cursor.execute("UPDATE projects SET owner_id = %s WHERE owner_id IS NULL", (owner_id,))
        projects_updated = cursor.rowcount
        print(f"Backfilled {projects_updated} row(s) in projects.")

        # Commit the changes
        conn.commit()
        print("Migration completed successfully!")
        print("Summary:")
        print(f"  clients.owner_id column: {'added' if 'owner_id' not in clients_columns else 'already present'}")
        print(f"  projects.owner_id column: {'added' if 'owner_id' not in projects_columns else 'already present'}")
        print(f"  clients rows backfilled: {clients_updated}")
        print(f"  projects rows backfilled: {projects_updated}")
        print(f"  backfill owner_id used: {owner_id}")

    except Exception as e:
        print(f"ERROR: {str(e)}")
        conn.rollback()
        sys.exit(1)
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    run_migration()
