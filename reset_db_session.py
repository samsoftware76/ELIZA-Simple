"""
Script to reset the database session and clear any pending transactions
"""
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
import os
from dotenv import load_dotenv
import sys

# Add the project root directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

# Import the db object from the app
from app import db, create_app

def reset_db_session():
    """Reset the database session to clear any pending transactions"""
    try:
        print("Creating app context...")
        app = create_app()
        
        with app.app_context():
            print("Resetting database session...")
            db.session.rollback()
            db.session.remove()
            db.engine.dispose()
            print("Database session reset successfully!")
            
            # Verify the payment_account column exists
            from sqlalchemy import text
            result = db.session.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='payments' AND column_name='payment_account'
            """)).fetchone()
            
            if result:
                print("Confirmed: payment_account column exists in the database.")
            else:
                print("WARNING: payment_account column does not exist in the database!")
                
    except Exception as e:
        print(f"Error resetting database session: {str(e)}")
        import traceback
        print(traceback.format_exc())

if __name__ == '__main__':
    reset_db_session()
