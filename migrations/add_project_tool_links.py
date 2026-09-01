"""
Database migration script to add third-party tool link columns
(zoom_link, drive_link, whatsapp_contact) to the projects table.
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
    """Add zoom_link/drive_link/whatsapp_contact to projects (idempotent)."""
    print("Starting migration to add project tool link columns...")

    # Connect to the database
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()

        # Check if columns exist before adding them
        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'projects'")
        existing_columns = [col[0] for col in cursor.fetchall()]

        # All nullable: these are optional links a project may or may not use,
        # not required at creation time, and existing rows have no value to backfill.
        if 'zoom_link' not in existing_columns:
            print("Adding zoom_link column to projects table...")
            cursor.execute("ALTER TABLE projects ADD COLUMN zoom_link VARCHAR(255)")
        else:
            print("zoom_link column already exists.")

        if 'drive_link' not in existing_columns:
            print("Adding drive_link column to projects table...")
            cursor.execute("ALTER TABLE projects ADD COLUMN drive_link VARCHAR(255)")
        else:
            print("drive_link column already exists.")

        if 'whatsapp_contact' not in existing_columns:
            print("Adding whatsapp_contact column to projects table...")
            cursor.execute("ALTER TABLE projects ADD COLUMN whatsapp_contact VARCHAR(255)")
        else:
            print("whatsapp_contact column already exists.")

        # Commit the changes
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
