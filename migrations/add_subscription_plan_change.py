"""
Database migration script to add the pending-plan-change fields to the
subscriptions table: pending_plan_id, pending_billing_cycle, pending_change_at.

These let an account change plan WITHOUT its current subscription row being
mutated first. Before this, api/subscription.py's subscribe() deactivated the
existing subscription and created a fresh 14-day trial, so a paying customer
who clicked another plan lost the plan they had paid for. Now the target plan
is parked in these columns and only moved onto plan_id when it is legitimately
due:

  * pending_change_at NULL  -> an UPGRADE waiting on a PesaPal payment, applied
    by subscription.payment_callback() when the payment is confirmed.
  * pending_change_at SET   -> a DOWNGRADE scheduled for the end of the cycle
    the customer already paid for, applied lazily on read by
    utils/plan_limits.apply_due_plan_change() once that date passes.

All three columns are nullable and additive with no backfill - NULL across all
three is "no plan change in flight", which is correct for every existing row.
Same idempotent convention as migrations/add_invoice_defaults.py.
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

# (column_name, DDL type) - matches the column types on Subscription in
# models/subscription.py exactly, so the live schema and the ORM model agree.
# pending_plan_id carries the same REFERENCES subscription_plans(id) the model
# declares, following migrations/add_tenant_ownership.py's inline-REFERENCES
# style for a nullable FK column.
NEW_COLUMNS = [
    ('pending_plan_id', 'INTEGER REFERENCES subscription_plans(id)'),
    ('pending_billing_cycle', 'VARCHAR(10)'),
    ('pending_change_at', 'TIMESTAMP'),
]


def run_migration():
    """Add the 3 pending-plan-change columns to subscriptions (idempotent, nullable, no backfill)."""
    print("Starting migration to add pending plan change fields to subscriptions...")

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()

        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'subscriptions'")
        existing_columns = [col[0] for col in cursor.fetchall()]

        added = []
        skipped = []
        for column_name, ddl_type in NEW_COLUMNS:
            if column_name not in existing_columns:
                print(f"Adding {column_name} column to subscriptions table...")
                cursor.execute(f"ALTER TABLE subscriptions ADD COLUMN {column_name} {ddl_type}")
                added.append(column_name)
            else:
                print(f"{column_name} column already exists on subscriptions.")
                skipped.append(column_name)

        conn.commit()
        print("Migration completed successfully!")
        print("Summary:")
        print(f"  columns added: {added if added else 'none'}")
        print(f"  columns already present: {skipped if skipped else 'none'}")

    except Exception as e:
        print(f"ERROR: {str(e)}")
        conn.rollback()
        sys.exit(1)
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    run_migration()
