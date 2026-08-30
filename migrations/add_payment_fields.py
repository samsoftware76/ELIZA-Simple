"""
Migration script to add payment_account and merchant_reference columns to payments table
"""
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
import os
from dotenv import load_dotenv
import sys

# Add the project root directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

# Load environment variables
load_dotenv()

# Create a minimal Flask app
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

def run_migration():
    """Run the migration to add the new columns to the payments table"""
    with app.app_context():
        # Connect to the database
        conn = db.engine.connect()
        
        try:
            print("Starting migration...")
            
            # Check if merchant_reference column exists
            check_merchant_ref = conn.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='payments' AND column_name='merchant_reference'
            """)).fetchone()
            
            # Check if payment_account column exists
            check_payment_account = conn.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='payments' AND column_name='payment_account'
            """)).fetchone()
            
            # Add merchant_reference column if it doesn't exist
            if not check_merchant_ref:
                print("Adding merchant_reference column...")
                conn.execute(text("""
                    ALTER TABLE payments 
                    ADD COLUMN merchant_reference VARCHAR(100)
                """))
                print("merchant_reference column added successfully.")
            else:
                print("merchant_reference column already exists.")
            
            # Add payment_account column if it doesn't exist
            if not check_payment_account:
                print("Adding payment_account column...")
                conn.execute(text("""
                    ALTER TABLE payments 
                    ADD COLUMN payment_account VARCHAR(100)
                """))
                print("payment_account column added successfully.")
            else:
                print("payment_account column already exists.")
            
            print("Migration completed successfully!")
            
        except Exception as e:
            print(f"Error during migration: {str(e)}")
            raise
        finally:
            conn.close()

if __name__ == '__main__':
    run_migration()
