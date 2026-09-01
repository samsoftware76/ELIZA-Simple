"""
Database migration script to add a tenant ownership (owner_id) column to the
activity_logs table.

No backfill is performed for existing rows, on purpose: ActivityLog.entity_id
is polymorphic (its meaning depends on entity_type - 'task', 'project',
'client', 'time_entry', ...), so there is no single safe join back to an
owner that works for every row. Guessing per entity_type here would risk
mis-attributing old log entries to the wrong tenant. Old rows are left with
owner_id NULL; only entries created from this deploy onward (via
api/index.py, which now passes owner_id=current_user.id on every
ActivityLog(...) construction) will have it populated.
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
    """Add owner_id to activity_logs (idempotent). No backfill - see module docstring."""
    print("Starting migration to add activity_logs.owner_id...")

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()

        cursor.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'activity_logs'"
        )
        activity_log_columns = [col[0] for col in cursor.fetchall()]

        if 'owner_id' not in activity_log_columns:
            print("Adding owner_id column to activity_logs table...")
            # Nullable on purpose, same reasoning as clients/projects.owner_id
            # in add_tenant_ownership.py: in-flight requests mid-deploy must
            # not start failing ActivityLog inserts.
            cursor.execute(
                "ALTER TABLE activity_logs ADD COLUMN owner_id INTEGER "
                "REFERENCES users(id)"
            )
        else:
            print("owner_id column already exists on activity_logs.")

        conn.commit()
        print("Migration completed successfully!")
        print("Summary:")
        print(f"  activity_logs.owner_id column: {'added' if 'owner_id' not in activity_log_columns else 'already present'}")
        print("  activity_logs rows backfilled: 0 (intentionally none - entity_type/entity_id is polymorphic, see module docstring)")

    except Exception as e:
        print(f"ERROR: {str(e)}")
        conn.rollback()
        sys.exit(1)
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    run_migration()
