"""
Database migration script to add the team_memberships and team_invites
tables (schema foundation for letting an account owner invite a second User
login to share access to the same data - see models/models.py
TeamMembership/TeamInvite and User.get_owner_id). No existing tables are
altered and no rows are backfilled: with zero TeamMembership rows, every
user still resolves to themselves via get_owner_id(), so this is a pure
additive, zero-behavior-change migration for today's users.
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
    """Create team_memberships and team_invites (idempotent)."""
    print("Starting migration to add team_memberships and team_invites tables...")

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()

        # --- team_memberships ---
        cursor.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'team_memberships')")
        if not cursor.fetchone()[0]:
            print("Creating team_memberships table...")
            # role is typed `userrole` (not a plain VARCHAR) so it reuses the
            # exact enum type Postgres already has for users.role, rather
            # than creating a second, drifting definition of the same set of
            # values - see the ('userrole', ...) rows already in pg_enum.
            cursor.execute("""
                CREATE TABLE team_memberships (
                    id SERIAL PRIMARY KEY,
                    account_owner_id INTEGER NOT NULL REFERENCES users(id),
                    member_user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
                    role userrole NOT NULL DEFAULT 'DEVELOPER',
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        else:
            print("team_memberships table already exists.")

        # --- team_invites ---
        cursor.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'team_invites')")
        if not cursor.fetchone()[0]:
            print("Creating team_invites table...")
            cursor.execute("""
                CREATE TABLE team_invites (
                    id SERIAL PRIMARY KEY,
                    inviter_id INTEGER NOT NULL REFERENCES users(id),
                    invitee_email VARCHAR(120) NOT NULL,
                    role userrole NOT NULL DEFAULT 'DEVELOPER',
                    status VARCHAR(20) DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    accepted_at TIMESTAMP
                )
            """)
        else:
            print("team_invites table already exists.")

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
