"""
Test email configuration for ELIZA Project Management App
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
from utils.email_utils import test_email_configuration

# Create a simple Flask app for testing
app = Flask(__name__)

# Configure email settings from environment variables
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'True').lower() in ('true', 'yes', '1')
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER')
app.config['BASE_URL'] = os.getenv('BASE_URL', 'https://localhost:5000')

# Initialize mail extension
mail = Mail(app)

if __name__ == '__main__':
    print("ELIZA Project Management App - Email Test")
    print("----------------------------------------")
    print(f"Mail Server: {app.config['MAIL_SERVER']}")
    print(f"Mail Port: {app.config['MAIL_PORT']}")
    print(f"Mail Use TLS: {app.config['MAIL_USE_TLS']}")
    print(f"Mail Username: {app.config['MAIL_USERNAME']}")
    print(f"Mail Password: {'*' * 8 if app.config['MAIL_PASSWORD'] else 'Not set'}")
    print(f"Mail Default Sender: {app.config['MAIL_DEFAULT_SENDER']}")
    print("----------------------------------------")
    
    # Ask for recipient email
    recipient = input("Enter recipient email address (or press Enter to use your configured email): ").strip()
    if not recipient:
        recipient = app.config['MAIL_USERNAME']
    
    print(f"Sending test email to: {recipient}")
    
    # Send test email
    with app.app_context():
        success = test_email_configuration(recipient)
    
    if success:
        print("✅ Test email sent successfully! Check your inbox.")
        print("If you don't see the email, check your spam folder.")
    else:
        print("❌ Failed to send test email. Check your email configuration.")
        print("Common issues:")
        print("1. Incorrect email credentials")
        print("2. Using regular password instead of App Password for Gmail")
        print("3. Less secure app access not enabled (for non-Gmail providers)")
        print("4. Network or firewall issues blocking SMTP connections")
