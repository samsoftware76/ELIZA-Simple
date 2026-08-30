"""
Database migration script to add missing columns to the tasks table.
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
    """Execute the migration to add missing columns to the tasks table."""
    print("Starting migration to add missing columns to the tasks table...")
    
    # Connect to the database
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        
        # Check if columns exist before adding them
        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'tasks'")
        existing_columns = [col[0] for col in cursor.fetchall()]
        
        # Add started_at column if it doesn't exist
        if 'started_at' not in existing_columns:
            print("Adding started_at column to tasks table...")
            cursor.execute("ALTER TABLE tasks ADD COLUMN started_at TIMESTAMP")
        else:
            print("started_at column already exists.")
        
        # Add completed_at column if it doesn't exist
        if 'completed_at' not in existing_columns:
            print("Adding completed_at column to tasks table...")
            cursor.execute("ALTER TABLE tasks ADD COLUMN completed_at TIMESTAMP")
        else:
            print("completed_at column already exists.")
        
        # Add is_billable column if it doesn't exist
        if 'is_billable' not in existing_columns:
            print("Adding is_billable column to tasks table...")
            cursor.execute("ALTER TABLE tasks ADD COLUMN is_billable BOOLEAN DEFAULT TRUE")
        else:
            print("is_billable column already exists.")
        
        # Add actual_hours column if it doesn't exist
        if 'actual_hours' not in existing_columns:
            print("Adding actual_hours column to tasks table...")
            cursor.execute("ALTER TABLE tasks ADD COLUMN actual_hours FLOAT")
        else:
            print("actual_hours column already exists.")
        
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
