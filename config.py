"""
Configuration settings for ELIZA Project Management App
"""
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Flask configuration
SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key')
DEBUG = os.getenv('FLASK_ENV') != 'production'

# Database configuration
SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'sqlite:///eliza.db')
SQLALCHEMY_TRACK_MODIFICATIONS = False

# Security configuration
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
PERMANENT_SESSION_LIFETIME = 3600  # 1 hour session timeout

# PesaPal configuration
PESAPAL_BASE_URL = os.getenv('PESAPAL_BASE_URL', 'https://pay.pesapal.com/v3')
PESAPAL_CONSUMER_KEY = os.getenv('PESAPAL_CONSUMER_KEY')
PESAPAL_CONSUMER_SECRET = os.getenv('PESAPAL_CONSUMER_SECRET')
PESAPAL_IPN_ID = os.getenv('PESAPAL_IPN_ID')

# Email configuration
MAIL_SERVER = os.getenv('MAIL_SERVER')
MAIL_PORT = int(os.getenv('MAIL_PORT', 587))
MAIL_USE_TLS = os.getenv('MAIL_USE_TLS', 'True').lower() in ('true', 'yes', '1')
MAIL_USERNAME = os.getenv('MAIL_USERNAME')
MAIL_PASSWORD = os.getenv('MAIL_PASSWORD')
MAIL_DEFAULT_SENDER = os.getenv('MAIL_DEFAULT_SENDER')

# Application configuration
APP_NAME = 'ELIZA Project Management'
ADMIN_EMAIL = os.getenv('ADMIN_EMAIL', 'admin@example.com')
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB max upload size
