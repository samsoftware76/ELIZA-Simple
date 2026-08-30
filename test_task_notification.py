"""
Test task notification emails for ELIZA Project Management App
"""
import os
import sys
from dotenv import load_dotenv

# Add the parent directory to sys.path to allow importing from sibling packages
parent_dir = os.path.dirname(os.path.realpath(__file__))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

# Load environment variables from .env file
dotenv_path = os.path.join(parent_dir, '.env')
load_dotenv(dotenv_path)

from flask import Flask
from flask_mail import Mail
from models import db
from models.models import User, Task, Project
from utils.email_utils import mail, send_task_assignment_notification

# Create a simple Flask app for testing
app = Flask(__name__, template_folder='templates')

# Configure email settings from environment variables
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'True').lower() in ('true', 'yes', '1')
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER')
app.config['BASE_URL'] = os.getenv('BASE_URL', 'https://localhost:5000')

# Configure database
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize extensions
db.init_app(app)
mail.init_app(app)

def test_task_notification():
    """Test sending a task assignment notification"""
    with app.app_context():
        # Get a user from the database
        user = User.query.first()
        if not user:
            print("❌ No users found in the database!")
            return False
        
        # Get a task from the database
        task = Task.query.first()
        if not task:
            print("❌ No tasks found in the database!")
            return False
        
        print(f"Sending test notification for task '{task.title}' to {user.email}")
        
        try:
            # Send the notification
            send_task_assignment_notification(task, user)
            print(f"✅ Task notification sent successfully to {user.email}!")
            return True
        except Exception as e:
            print(f"❌ Failed to send task notification: {str(e)}")
            return False

if __name__ == '__main__':
    print("ELIZA Project Management App - Task Notification Test")
    print("---------------------------------------------------")
    print(f"Mail Server: {os.getenv('MAIL_SERVER')}")
    print(f"Mail Username: {os.getenv('MAIL_USERNAME')}")
    print(f"Database URL: {os.getenv('DATABASE_URL')[:20]}...")
    print("---------------------------------------------------")
    
    # Test task notification
    with app.app_context():
        success = test_task_notification()
    
    if success:
        print("\n✅ Task notification test completed successfully!")
        print("Check your email inbox for the notification.")
    else:
        print("\n❌ Task notification test failed.")
        print("Check the error messages above for details.")
