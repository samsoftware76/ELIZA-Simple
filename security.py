"""
Security configuration for ELIZA Project Management App
Implements protection against common web vulnerabilities
"""
import os
from flask_talisman import Talisman

# CSRF protection is initialized once in api/index.py (CSRFProtect(app)) —
# not duplicated here to avoid double-registering the before_request hook.


def configure_security(app):
    """Configure security headers (via Flask-Talisman) for the Flask application.

    HTTPS-forcing and secure-cookie behavior only activate in production
    (FLASK_ENV=production or when running on Vercel), so local development
    over plain http:// still works.
    """
    is_production = (
        os.environ.get('FLASK_ENV') == 'production'
        or os.environ.get('VERCEL') == '1'
    )

    # Configure Content Security Policy.
    # Hosts here must match every CDN actually referenced in templates/ -
    # stackpath.bootstrapcdn.com (Bootstrap) and cdnjs.cloudflare.com
    # (Font Awesome, used on nearly every page) are the two that matter
    # most: leaving either out silently breaks the whole app's layout,
    # since the browser drops the blocked stylesheet/script with no
    # visible error - only a CSP violation in the browser console.
    csp = {
        'default-src': "'self'",
        'script-src': [
            "'self'", "'unsafe-inline'",
            "https://cdn.jsdelivr.net", "https://code.jquery.com",
            "https://stackpath.bootstrapcdn.com", "https://cdnjs.cloudflare.com",
        ],
        'style-src': [
            "'self'", "'unsafe-inline'",
            "https://cdn.jsdelivr.net", "https://fonts.googleapis.com",
            "https://stackpath.bootstrapcdn.com", "https://cdnjs.cloudflare.com",
        ],
        'font-src': [
            "'self'",
            "https://cdn.jsdelivr.net", "https://fonts.gstatic.com",
            "https://stackpath.bootstrapcdn.com", "https://cdnjs.cloudflare.com",
        ],
        'img-src': ["'self'", "data:", "https:"],
        # cdn.jsdelivr.net/stackpath.bootstrapcdn.com here aren't for the app
        # itself - they're so the browser's devtools can fetch .map files for
        # those CDN scripts/styles without a console warning. Harmless either
        # way; only affects developers with devtools open.
        'connect-src': ["'self'", "https://pay.pesapal.com", "https://cdn.jsdelivr.net", "https://stackpath.bootstrapcdn.com"],
        'frame-src': ["'self'", "https://pay.pesapal.com"]
    }

    # Initialize Talisman (security headers).
    # nonce_in covers script-src only, not style-src: per the CSP spec, a
    # nonce in a directive makes the browser ignore 'unsafe-inline' for that
    # directive entirely. The nonce only ever attaches to <script>/<style>
    # tags (Talisman rewrites those automatically) - it can't attach to an
    # inline style="..." attribute on an ordinary element, and this app uses
    # those throughout. Nonce-ing style-src as well silently broke every
    # inline style in the app; scripts are the higher-value thing to lock
    # down with a nonce anyway.
    Talisman(
        app,
        content_security_policy=csp,
        content_security_policy_nonce_in=['script-src'],
        force_https=is_production,  # Redirect HTTP to HTTPS in production only
        strict_transport_security=is_production,  # Enable HSTS in production only
        session_cookie_secure=is_production,  # Cookies only sent over HTTPS in production
        session_cookie_http_only=True,  # Prevent JavaScript access to cookies
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
    app.config['SESSION_COOKIE_SECURE'] = is_production
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['PERMANENT_SESSION_LIFETIME'] = 3600  # 1 hour session timeout

    # Warn if still using a placeholder secret key
    if app.config.get('SECRET_KEY') in (None, 'dev-secret-key', 'default-secret-key-for-dev'):
        if is_production:
            raise RuntimeError(
                "SECRET_KEY is not set. Set a real SECRET_KEY environment variable before deploying to production."
            )
        print("WARNING: Using a placeholder SECRET_KEY. Set SECRET_KEY in your environment for production.")

    return app
