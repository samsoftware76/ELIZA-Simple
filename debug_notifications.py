"""
Debug email notifications for ELIZA Project Management App
"""
import os
import sys
import logging
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.DEBUG, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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
from models.models import User, Task, Project, Comment
from utils.email_utils import mail, send_task_assignment_notification, send_task_update_notification, send_task_comment_notification

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

def debug_task_assignment():
    """Debug task assignment notifications"""
    with app.app_context():
        # Get a user from the database
        user = User.query.first()
        if not user:
            logger.error("No users found in the database!")
            return False
        
        # Get a task from the database
        task = Task.query.first()
        if not task:
            logger.error("No tasks found in the database!")
            return False
        
        logger.info(f"Testing task assignment notification for task '{task.title}' to {user.email}")
        
        try:
            # Monkey patch the task to use our test user
            task.assigned_to = user
            task.assignee_id = user.id
            
            # Send the notification
            send_task_assignment_notification(task, user)
            logger.info(f"Task assignment notification sent successfully to {user.email}!")
            return True
        except Exception as e:
            logger.error(f"Failed to send task assignment notification: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return False

def debug_task_update():
    """Debug task update notifications"""
    with app.app_context():
        # Get a user from the database
        user = User.query.first()
        if not user:
            logger.error("No users found in the database!")
            return False
        
        # Get a task from the database
        task = Task.query.first()
        if not task:
            logger.error("No tasks found in the database!")
            return False
        
        logger.info(f"Testing task update notification for task '{task.title}'")
        
        try:
            # Send the notification
            send_task_update_notification(task, "status_change", user)
            logger.info(f"Task update notification sent successfully!")
            return True
        except Exception as e:
            logger.error(f"Failed to send task update notification: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return False

def debug_task_comment():
    """Debug task comment notifications"""
    with app.app_context():
        # Get a user from the database
        user = User.query.first()
        if not user:
            logger.error("No users found in the database!")
            return False
        
        # Get a task from the database
        task = Task.query.first()
        if not task:
            logger.error("No tasks found in the database!")
            return False
        
        # Get or create a comment
        comment = Comment.query.filter_by(task_id=task.id).first()
        if not comment:
            logger.info("No comment found, creating a test comment")
            comment = Comment(
                content="This is a test comment for notification debugging",
                task_id=task.id,
                user_id=user.id
            )
        
        logger.info(f"Testing task comment notification for task '{task.title}'")
        
        try:
            # Send the notification
            send_task_comment_notification(task, comment, user)
            logger.info(f"Task comment notification sent successfully!")
            return True
        except Exception as e:
            logger.error(f"Failed to send task comment notification: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return False

def check_email_config():
    """Check email configuration"""
    logger.info("Checking email configuration...")
    logger.info(f"MAIL_SERVER: {app.config['MAIL_SERVER']}")
    logger.info(f"MAIL_PORT: {app.config['MAIL_PORT']}")
    logger.info(f"MAIL_USE_TLS: {app.config['MAIL_USE_TLS']}")
    logger.info(f"MAIL_USERNAME: {app.config['MAIL_USERNAME']}")
    logger.info(f"MAIL_PASSWORD: {'*' * 8 if app.config['MAIL_PASSWORD'] else 'Not set'}")
    logger.info(f"MAIL_DEFAULT_SENDER: {app.config['MAIL_DEFAULT_SENDER']}")
    logger.info(f"BASE_URL: {app.config['BASE_URL']}")

if __name__ == '__main__':
    print("ELIZA Project Management App - Notification Debugging")
    print("----------------------------------------------------")
    
    # Check email configuration
    check_email_config()
    
    print("\nSelect a test to run:")
    print("1. Test task assignment notification")
    print("2. Test task update notification")
    print("3. Test task comment notification")
    print("4. Run all tests")
    
    choice = input("Enter your choice (1-4): ").strip()
    
    if choice == '1':
        debug_task_assignment()
    elif choice == '2':
        debug_task_update()
    elif choice == '3':
        debug_task_comment()
    elif choice == '4':
        print("\nRunning all notification tests...")
        debug_task_assignment()
        debug_task_update()
        debug_task_comment()
    else:
        print("Invalid choice. Please run the script again with a valid option.")
