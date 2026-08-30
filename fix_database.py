"""
Script to directly fix the database schema for the payments table
"""
import psycopg2
import os
from dotenv import load_dotenv
import sys

# Load environment variables
load_dotenv()

# Get database connection details from environment variables
DATABASE_URL = os.getenv('DATABASE_URL')

def fix_database():
    """Fix the database schema by adding missing columns"""
    try:
        print("Connecting to database...")
        
        # Parse the DATABASE_URL to get connection parameters
        # Example: postgresql://username:password@localhost:5432/dbname
        if DATABASE_URL.startswith('postgresql://'):
            # Remove the protocol part
            conn_string = DATABASE_URL[len('postgresql://'):]
            
            # Split username:password from the rest
            if '@' in conn_string:
                auth, rest = conn_string.split('@', 1)
                
                # Extract username and password
                if ':' in auth:
                    username, password = auth.split(':', 1)
                else:
                    username = auth
                    password = ''
                
                # Extract host, port, and database name
                if '/' in rest:
                    host_port, dbname = rest.split('/', 1)
                    
                    # Extract host and port
                    if ':' in host_port:
                        host, port = host_port.split(':', 1)
                        port = int(port)
                    else:
                        host = host_port
                        port = 5432  # Default PostgreSQL port
                else:
                    host = rest
                    port = 5432
                    dbname = ''
            else:
                # No authentication provided
                username = ''
                password = ''
                if '/' in conn_string:
                    host_port, dbname = conn_string.split('/', 1)
                    
                    # Extract host and port
                    if ':' in host_port:
                        host, port = host_port.split(':', 1)
                        port = int(port)
                    else:
                        host = host_port
                        port = 5432
                else:
                    host = conn_string
                    port = 5432
                    dbname = ''
        else:
            print(f"Unsupported database URL format: {DATABASE_URL}")
            return
        
        # Connect to the database
        conn = psycopg2.connect(
            host=host,
            port=port,
            user=username,
            password=password,
            dbname=dbname
        )
        conn.autocommit = True
        cursor = conn.cursor()
        
        print(f"Connected to database: {dbname} on {host}:{port}")
        
        # Check if merchant_reference column exists
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='payments' AND column_name='merchant_reference'
        """)
        merchant_ref_exists = cursor.fetchone() is not None
        
        # Check if payment_account column exists
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='payments' AND column_name='payment_account'
        """)
        payment_account_exists = cursor.fetchone() is not None
        
        # Add merchant_reference column if it doesn't exist
        if not merchant_ref_exists:
            print("Adding merchant_reference column...")
            cursor.execute("""
                ALTER TABLE payments 
                ADD COLUMN merchant_reference VARCHAR(100)
            """)
            print("merchant_reference column added successfully.")
        else:
            print("merchant_reference column already exists.")
        
        # Add payment_account column if it doesn't exist
        if not payment_account_exists:
            print("Adding payment_account column...")
            cursor.execute("""
                ALTER TABLE payments 
                ADD COLUMN payment_account VARCHAR(100)
            """)
            print("payment_account column added successfully.")
        else:
            print("payment_account column already exists.")
        
        print("Database schema fixed successfully!")
        
    except Exception as e:
        print(f"Error fixing database schema: {str(e)}")
        import traceback
        print(traceback.format_exc())
    finally:
        if 'conn' in locals() and conn:
            conn.close()
            print("Database connection closed.")

if __name__ == '__main__':
    fix_database()
