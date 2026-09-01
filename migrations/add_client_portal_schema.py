"""
Database migration script for the schema foundation of the client portal:
clients get a real login, scoped to exactly one Client record - distinct from
the existing owner_id tenant scoping (staff account that owns a client
record) and distinct from TeamMembership sharing (staff sharing a whole
tenant's data). See models/models.py Client.user_id / ClientInvite /
Task.client_approved for the ORM side. No existing rows are touched beyond
adding NULL-able / default-valued columns, and no routes read these columns
yet, so this is a pure additive, zero-behavior-change migration for today's
users.

  1. Add clients.user_id (nullable, UNIQUE, FK to users.id) - the one login
     this client record can sign in as, once invited and accepted.
  2. Create the client_invites table if it doesn't exist.
  3. Add tasks.client_approved (BOOLEAN NOT NULL DEFAULT FALSE) and
     tasks.client_approved_at (nullable TIMESTAMP).
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
    """Add the client-portal schema foundation (idempotent)."""
    print("Starting migration to add client portal schema...")

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()

        # --- clients.user_id column ---
        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'clients'")
        clients_columns = [col[0] for col in cursor.fetchall()]

        user_id_column_added = 'user_id' not in clients_columns
        if user_id_column_added:
            print("Adding user_id column to clients table...")
            # Nullable on purpose: most clients have no portal login at all,
            # only ones invited via client_invites. REFERENCES users(id)
            # matches Client.user_id's FK; the UNIQUE constraint is added
            # separately below (checked independently so re-running this
            # script after a partial failure doesn't error on either step).
            cursor.execute(
                "ALTER TABLE clients ADD COLUMN user_id INTEGER "
                "REFERENCES users(id)"
            )
        else:
            print("user_id column already exists on clients.")

        # --- UNIQUE constraint on clients.user_id ---
        cursor.execute("""
            SELECT constraint_name FROM information_schema.table_constraints
            WHERE table_name = 'clients' AND constraint_type = 'UNIQUE'
        """)
        existing_unique_constraints = [row[0] for row in cursor.fetchall()]

        user_id_unique_added = 'clients_user_id_key' not in existing_unique_constraints
        if user_id_unique_added:
            print("Adding UNIQUE constraint on clients.user_id...")
            cursor.execute(
                "ALTER TABLE clients ADD CONSTRAINT clients_user_id_key UNIQUE (user_id)"
            )
        else:
            print("UNIQUE constraint on clients.user_id already exists.")

        # --- client_invites table ---
        cursor.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'client_invites')")
        if not cursor.fetchone()[0]:
            print("Creating client_invites table...")
            cursor.execute("""
                CREATE TABLE client_invites (
                    id SERIAL PRIMARY KEY,
                    inviter_id INTEGER NOT NULL REFERENCES users(id),
                    client_id INTEGER NOT NULL REFERENCES clients(id),
                    invitee_email VARCHAR(120) NOT NULL,
                    status VARCHAR(20) DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    accepted_at TIMESTAMP
                )
            """)
        else:
            print("client_invites table already exists.")

        # --- tasks.client_approved / client_approved_at columns ---
        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'tasks'")
        tasks_columns = [col[0] for col in cursor.fetchall()]

        client_approved_added = 'client_approved' not in tasks_columns
        if client_approved_added:
            print("Adding client_approved column to tasks table...")
            cursor.execute(
                "ALTER TABLE tasks ADD COLUMN client_approved BOOLEAN NOT NULL DEFAULT FALSE"
            )
        else:
            print("client_approved column already exists on tasks.")

        client_approved_at_added = 'client_approved_at' not in tasks_columns
        if client_approved_at_added:
            print("Adding client_approved_at column to tasks table...")
            cursor.execute("ALTER TABLE tasks ADD COLUMN client_approved_at TIMESTAMP")
        else:
            print("client_approved_at column already exists on tasks.")

        conn.commit()
        print("Migration completed successfully!")
        print("Summary:")
        print(f"  clients.user_id column: {'added' if user_id_column_added else 'already present'}")
        print(f"  clients.user_id UNIQUE constraint: {'added' if user_id_unique_added else 'already present'}")
        print(f"  tasks.client_approved column: {'added' if client_approved_added else 'already present'}")
        print(f"  tasks.client_approved_at column: {'added' if client_approved_at_added else 'already present'}")

    except Exception as e:
        print(f"ERROR: {str(e)}")
        conn.rollback()
        sys.exit(1)
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    run_migration()
