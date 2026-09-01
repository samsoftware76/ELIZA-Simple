"""
Database migration script to:

  1. Add clients.unsubscribe_token (idempotent) and backfill every existing
     row with a unique token, then add a UNIQUE constraint on it. This
     replaces the current bare-email unsubscribe link (?email=...), which
     lets anyone unsubscribe any client of any tenant just by knowing or
     guessing their email address - no auth or token required at all.

  2. Replace the bare UNIQUE(email) constraint on clients (found via
     information_schema as clients_email_key - confirmed at migration-run
     time rather than assumed, in case an earlier deploy already renamed or
     recreated it) with a composite UNIQUE(owner_id, email). Right now two
     different tenants can't use a client with the same email: tenant B
     creating a client with an email tenant A already has gets a false
     "already exists" error, which leaks that the email exists in someone
     else's client list. Scoping the uniqueness to (owner_id, email) fixes
     that while still stopping one tenant from creating duplicate clients
     for themselves.
"""
import os
import sys
import secrets
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

# Same secrets.token_urlsafe(32) pattern as generate_public_token() in
# models/billing.py / generate_client_unsubscribe_token() in models/models.py,
# duplicated here rather than imported so this standalone script doesn't need
# the Flask app/DB context to run.
def generate_token():
    return secrets.token_urlsafe(32)


def run_migration():
    """Add clients.unsubscribe_token + backfill + UNIQUE, and swap the email
    uniqueness constraint from global to per-tenant (idempotent)."""
    print("Starting migration to add clients.unsubscribe_token and re-scope email uniqueness...")

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()

        # --- clients.unsubscribe_token column ---
        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'clients'")
        clients_columns = [col[0] for col in cursor.fetchall()]

        column_added = 'unsubscribe_token' not in clients_columns
        if column_added:
            print("Adding unsubscribe_token column to clients table...")
            cursor.execute(
                "ALTER TABLE clients ADD COLUMN unsubscribe_token VARCHAR(64)"
            )
        else:
            print("unsubscribe_token column already exists on clients.")

        # --- Backfill every row missing a token ---
        cursor.execute("SELECT id FROM clients WHERE unsubscribe_token IS NULL")
        rows_to_backfill = [row[0] for row in cursor.fetchall()]
        print(f"Backfilling unsubscribe_token for {len(rows_to_backfill)} client row(s)...")
        for client_id in rows_to_backfill:
            # Generated one at a time (not one shared value) since the column
            # is about to become UNIQUE.
            cursor.execute(
                "UPDATE clients SET unsubscribe_token = %s WHERE id = %s",
                (generate_token(), client_id)
            )
        tokens_backfilled = len(rows_to_backfill)

        # --- UNIQUE constraint on unsubscribe_token ---
        cursor.execute("""
            SELECT constraint_name FROM information_schema.table_constraints
            WHERE table_name = 'clients' AND constraint_type = 'UNIQUE'
        """)
        existing_unique_constraints = [row[0] for row in cursor.fetchall()]

        token_unique_added = 'clients_unsubscribe_token_key' not in existing_unique_constraints
        if token_unique_added:
            print("Adding UNIQUE constraint on clients.unsubscribe_token...")
            cursor.execute(
                "ALTER TABLE clients ADD CONSTRAINT clients_unsubscribe_token_key UNIQUE (unsubscribe_token)"
            )
        else:
            print("UNIQUE constraint on clients.unsubscribe_token already exists.")

        # --- Find the real name of the existing bare UNIQUE(email) constraint ---
        # Confirmed via information_schema rather than assumed, per the task's
        # instruction - a prior deploy could in principle have renamed it.
        cursor.execute("""
            SELECT tc.constraint_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name AND tc.table_name = kcu.table_name
            WHERE tc.table_name = 'clients'
              AND tc.constraint_type = 'UNIQUE'
              AND kcu.column_name = 'email'
        """)
        email_unique_rows = cursor.fetchall()
        # A composite UNIQUE(owner_id, email) also has an 'email' row in
        # key_column_usage, so filter down to a *bare* single-column
        # constraint on email only (the thing we actually want to replace).
        old_email_constraint_name = None
        for (constraint_name,) in email_unique_rows:
            cursor.execute("""
                SELECT column_name FROM information_schema.key_column_usage
                WHERE constraint_name = %s AND table_name = 'clients'
            """, (constraint_name,))
            cols = [c[0] for c in cursor.fetchall()]
            if cols == ['email']:
                old_email_constraint_name = constraint_name
                break

        print(f"Found existing bare email UNIQUE constraint: {old_email_constraint_name}")

        if old_email_constraint_name:
            print(f"Dropping constraint {old_email_constraint_name}...")
            cursor.execute(f'ALTER TABLE clients DROP CONSTRAINT "{old_email_constraint_name}"')
        else:
            print("No bare UNIQUE(email) constraint found - nothing to drop (already migrated?).")

        # --- Composite UNIQUE(owner_id, email) ---
        cursor.execute("""
            SELECT constraint_name FROM information_schema.table_constraints
            WHERE table_name = 'clients' AND constraint_type = 'UNIQUE'
        """)
        existing_unique_constraints = [row[0] for row in cursor.fetchall()]

        composite_added = 'clients_owner_id_email_key' not in existing_unique_constraints
        if composite_added:
            print("Adding composite UNIQUE(owner_id, email) constraint...")
            cursor.execute(
                "ALTER TABLE clients ADD CONSTRAINT clients_owner_id_email_key UNIQUE (owner_id, email)"
            )
        else:
            print("Composite UNIQUE(owner_id, email) constraint already exists.")

        conn.commit()
        print("Migration completed successfully!")
        print("Summary:")
        print(f"  unsubscribe_token column: {'added' if column_added else 'already present'}")
        print(f"  unsubscribe_token rows backfilled: {tokens_backfilled}")
        print(f"  unsubscribe_token UNIQUE constraint: {'added' if token_unique_added else 'already present'}")
        print(f"  old bare email UNIQUE constraint found and dropped: {old_email_constraint_name}")
        print(f"  composite UNIQUE(owner_id, email) constraint: {'added' if composite_added else 'already present'}")

    except Exception as e:
        print(f"ERROR: {str(e)}")
        conn.rollback()
        sys.exit(1)
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    run_migration()
