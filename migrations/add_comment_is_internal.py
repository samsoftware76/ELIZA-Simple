"""
Database migration script to add comments.is_internal - the staff-only vs
client-visible distinction on task comments. See Comment.is_internal in
models/models.py.

Added NOT NULL DEFAULT TRUE directly (not nullable-then-backfill like
clients/projects.owner_id in add_tenant_ownership.py): every existing
comment on this app was authored by a staff member before the client portal
comment feature existed, so treating every pre-existing row as internal
(the safe default - not newly, silently visible to a client) is correct,
not just convenient. The DEFAULT TRUE on the ALTER TABLE itself backfills
every existing row in the same statement, so there's no separate UPDATE
step needed here.
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
    """Add comments.is_internal (idempotent)."""
    print("Starting migration to add comments.is_internal...")

    # Connect to the database
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()

        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'comments'")
        comments_columns = [col[0] for col in cursor.fetchall()]

        is_internal_added = 'is_internal' not in comments_columns
        if is_internal_added:
            print("Adding is_internal column to comments table...")
            cursor.execute(
                "ALTER TABLE comments ADD COLUMN is_internal BOOLEAN NOT NULL DEFAULT TRUE"
            )
        else:
            print("is_internal column already exists on comments.")

        conn.commit()
        print("Migration completed successfully!")
        print("Summary:")
        print(f"  comments.is_internal column: {'added' if is_internal_added else 'already present'}")

    except Exception as e:
        print(f"ERROR: {str(e)}")
        conn.rollback()
        sys.exit(1)
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    run_migration()
