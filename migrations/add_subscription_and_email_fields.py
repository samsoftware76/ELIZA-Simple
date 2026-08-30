"""
Database migration script to add subscription-related tables and email opt-out field to clients table.
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
    """Execute the migration to add subscription tables and email opt-out field"""
    print("Starting migration to add subscription tables and email opt-out field...")
    
    # Connect to the database
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        
        # Check if email_opt_out column exists in clients table
        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'clients' AND column_name = 'email_opt_out'")
        if not cursor.fetchone():
            print("Adding email_opt_out column to clients table...")
            cursor.execute("ALTER TABLE clients ADD COLUMN email_opt_out BOOLEAN DEFAULT FALSE")
        else:
            print("email_opt_out column already exists in clients table.")
        
        # Check if subscription_plans table exists
        cursor.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'subscription_plans')")
        if not cursor.fetchone()[0]:
            print("Creating subscription_plans table...")
            cursor.execute("""
                CREATE TABLE subscription_plans (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(50) NOT NULL,
                    description TEXT,
                    price_monthly FLOAT NOT NULL,
                    price_yearly FLOAT NOT NULL,
                    max_projects INTEGER DEFAULT 0,
                    max_users INTEGER DEFAULT 0,
                    max_clients INTEGER DEFAULT 0,
                    features TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Insert default subscription plans
            print("Adding default subscription plans...")
            cursor.execute("""
                INSERT INTO subscription_plans (name, description, price_monthly, price_yearly, max_projects, max_users, max_clients, features, is_active)
                VALUES 
                ('Basic', 'Basic subscription plan', 9.99, 99.99, 5, 3, 10, 'Basic features', TRUE),
                ('Professional', 'Professional subscription plan', 19.99, 199.99, 15, 10, 30, 'Advanced features', TRUE),
                ('Enterprise', 'Enterprise subscription plan', 49.99, 499.99, 0, 0, 0, 'Enterprise features', TRUE)
            """)
        else:
            print("subscription_plans table already exists.")
        
        # Check if subscriptions table exists
        cursor.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'subscriptions')")
        if not cursor.fetchone()[0]:
            print("Creating subscriptions table...")
            cursor.execute("""
                CREATE TABLE subscriptions (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    plan_id INTEGER NOT NULL REFERENCES subscription_plans(id),
                    start_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    end_date TIMESTAMP,
                    is_active BOOLEAN DEFAULT TRUE,
                    is_trial BOOLEAN DEFAULT FALSE,
                    billing_cycle VARCHAR(10) DEFAULT 'monthly',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        else:
            print("subscriptions table already exists.")
        
        # Check if payments table exists
        cursor.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'payments')")
        if not cursor.fetchone()[0]:
            print("Creating payments table...")
            cursor.execute("""
                CREATE TABLE payments (
                    id SERIAL PRIMARY KEY,
                    subscription_id INTEGER NOT NULL REFERENCES subscriptions(id),
                    amount FLOAT NOT NULL,
                    currency VARCHAR(3) DEFAULT 'KES',
                    payment_method VARCHAR(50),
                    transaction_id VARCHAR(100) UNIQUE,
                    merchant_reference VARCHAR(100) UNIQUE,
                    order_tracking_id VARCHAR(100) UNIQUE,
                    status VARCHAR(20) DEFAULT 'pending',
                    payment_date TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        else:
            print("payments table already exists.")
        
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
