"""
Security configuration for ELIZA Project Management App
Implements protection against common web vulnerabilities
"""
from flask import Flask
from flask_talisman import Talisman
from flask_wtf.csrf import CSRFProtect
import secrets

def configure_security(app):
    """Configure security features for the Flask application"""
    # Initialize CSRF protection
    csrf = CSRFProtect(app)
    
    # Configure Content Security Policy
    csp = {
        'default-src': "'self'",
        'script-src': ["'self'", "'unsafe-inline'", "https://cdn.jsdelivr.net", "https://code.jquery.com"],
        'style-src': ["'self'", "'unsafe-inline'", "https://cdn.jsdelivr.net", "https://fonts.googleapis.com"],
        'font-src': ["'self'", "https://cdn.jsdelivr.net", "https://fonts.gstatic.com"],
        'img-src': ["'self'", "data:", "https:"],
        'connect-src': ["'self'", "https://pay.pesapal.com"],
        'frame-src': ["'self'", "https://pay.pesapal.com"]
    }
    
    # Initialize Talisman (security headers)
    talisman = Talisman(
        app,
        content_security_policy=csp,
        content_security_policy_nonce_in=['script-src', 'style-src'],
        force_https=True,  # Redirect HTTP to HTTPS
        strict_transport_security=True,  # Enable HSTS
        session_cookie_secure=True,  # Cookies only sent over HTTPS
        session_cookie_httponly=True,  # Prevent JavaScript access to cookies
        session_cookie_samesite='Lax',  # Prevent CSRF
        feature_policy={  # Restrict browser features
            'geolocation': "'none'",
            'microphone': "'none'",
            'camera': "'none'",
            'payment': "'self'",
        }
    )
    
    # Configure additional security settings
    @app.after_request
    def add_security_headers(response):
        # Prevent clickjacking
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        # Prevent MIME type sniffing
        response.headers['X-Content-Type-Options'] = 'nosniff'
        # Enable XSS protection
        response.headers['X-XSS-Protection'] = '1; mode=block'
        # Referrer policy
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        # Permissions policy (formerly Feature-Policy)
        response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
        return response
    
    # Configure secure session
    app.config['SESSION_COOKIE_SECURE'] = True
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['PERMANENT_SESSION_LIFETIME'] = 3600  # 1 hour session timeout
    
    # Generate a strong secret key if not set
    if app.config.get('SECRET_KEY') == 'dev-secret-key':
        app.config['SECRET_KEY'] = secrets.token_hex(32)
        print("WARNING: Using auto-generated secret key. Set SECRET_KEY in your environment for production.")
    
    return app
