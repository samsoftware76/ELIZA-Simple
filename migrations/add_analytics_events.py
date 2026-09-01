"""
Database migration script to create the analytics_events table used for
platform-wide product-usage analytics (see models/models.py's AnalyticsEvent
and utils/analytics.py's log_event()). Idempotent and additive only, same
convention as migrations/add_tenant_ownership.py.
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
    """Create analytics_events (idempotent) if it does not already exist."""
    print("Starting migration to add analytics_events table...")

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()

        cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_name = 'analytics_events'")
        table_exists = cursor.fetchone() is not None

        if not table_exists:
            print("Creating analytics_events table...")
            cursor.execute(
                """
                CREATE TABLE analytics_events (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id),
                    event_type VARCHAR(100) NOT NULL,
                    event_metadata TEXT,
                    created_at TIMESTAMP
                )
                """
            )
            cursor.execute(
                "CREATE INDEX ix_analytics_events_created_at ON analytics_events (created_at)"
            )
        else:
            print("analytics_events table already exists.")

        conn.commit()
        print("Migration completed successfully!")
        print("Summary:")
        print(f"  analytics_events table: {'created' if not table_exists else 'already present'}")

    except Exception as e:
        print(f"ERROR: {str(e)}")
        conn.rollback()
        sys.exit(1)
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    run_migration()
