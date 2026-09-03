"""
Security configuration for ELIZA Project Management App
Implements protection against common web vulnerabilities
"""
import os
from urllib.parse import urlsplit

from flask import request
from flask_talisman import Talisman
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address


# --------------------------------------------------------------------------
# PesaPal origin, derived from the SAME env var api/payment.py reads
# (PESAPAL_BASE_URL, e.g. https://pay.pesapal.com/v3) rather than hardcoded a
# second time. The CSP below has to name the exact origin the browser will be
# sent to; if someone ever points the app at PesaPal's sandbox
# (cybqa.pesapal.com) via that env var, a hardcoded pay.pesapal.com here
# would silently start blocking the payment redirect. Deriving it keeps the
# policy and the actual payment target from drifting apart.
# --------------------------------------------------------------------------
def _pesapal_origin():
    raw = os.environ.get('PESAPAL_BASE_URL', 'https://pay.pesapal.com/v3')
    parts = urlsplit(raw)
    if parts.scheme in ('http', 'https') and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return 'https://pay.pesapal.com'


PESAPAL_ORIGIN = _pesapal_origin()


def client_ip_key():
    """Per-client key for rate limiting - the address a TRUSTED proxy reported.

    WHY NOT flask_limiter's get_remote_address (which is just
    request.remote_addr): this app runs on Vercel, behind Vercel's edge
    proxy, and there is no ProxyFix in the WSGI stack (verified - nothing in
    this repo wraps app.wsgi_app). So in production request.remote_addr is
    the PROXY's address, identical for every visitor on Earth. Keying on it
    does not just fail to protect anyone - it is actively worse than no
    limit, because all clients share one counter and the first handful of
    requests from anybody locks out everybody (including, on the portal,
    a paying client trying to settle an invoice).

    WHY THE RIGHTMOST X-Forwarded-For ENTRY, NOT THE LEFTMOST: X-Forwarded-For
    is "client, proxy1, proxy2..." and ANY part a client sent itself is
    attacker-controlled. The leftmost entry is exactly the part a client can
    forge, so keying on it lets an attacker rotate a header value and get a
    fresh rate-limit bucket per request - i.e. bypass the limit completely.
    The rightmost entry is the one appended by the proxy closest to us, which
    a client cannot influence. (This is the same value werkzeug's
    ProxyFix(x_for=1) picks: see its _get_real_value -> values[-trusted].)
    Vercel additionally overwrites X-Forwarded-For at the edge and does not
    pass an external client's value through, so on this deployment the header
    is a single, trustworthy client address.

    NOTE - deliberately different from api/portal.py's _client_ip(), which
    takes the LEFTMOST entry. That one is best-effort audit-trail metadata
    ("what did the client claim") and its own docstring says it is never used
    for an authorization decision. This one IS a security decision, so it
    takes the non-forgeable end of the header instead.

    Falls back to remote_addr when no proxy header is present - i.e. local
    development and the test suite, where remote_addr is the real peer.
    """
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded:
        parts = [p.strip() for p in forwarded.split(',') if p.strip()]
        if parts:
            return parts[-1][:64]
    return get_remote_address()

# CSRF protection is initialized once in api/index.py (CSRFProtect(app)) —
# not duplicated here to avoid double-registering the before_request hook.

# Rate limiter for brute-force-sensitive routes (currently just login).
# Created here (unbound) and init_app()'d against the real app in
# configure_limiter() below, the same split Flask-SQLAlchemy's db object
# uses - lets api/index.py `from security import limiter` and decorate the
# login view with @limiter.limit(...) without an import cycle.
#
# HONEST LIMITATION: default storage is in-memory (per Python process). This
# app deploys to Vercel, where each serverless invocation can land on a cold
# start or a different instance with its own empty counter - there is no
# shared store (e.g. Redis) wired up. So this meaningfully slows down a
# single-IP brute-force script hitting one warm instance, but it is NOT a
# hard guarantee once Vercel scales to multiple instances; a determined
# attacker distributed across cold starts can still exceed 5/minute overall.
# Wiring a shared storage_uri (Redis) would close that gap if it's ever
# needed - not done here to avoid adding a new required service/env var for
# a first pass at this protection.
limiter = Limiter(key_func=client_ip_key)


def configure_limiter(app):
    """Attach the rate limiter to the Flask app. Call once, after app is created."""
    limiter.init_app(app)
    return limiter


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
        # 'self' covers every <img> in templates/ (all of them are
        # url_for('static', ...)); data: covers the signed-contract signature
        # images, which are stored and rendered as data:image/png;base64,...
        # URLs (see portal/contract.html + print/contract.html, and the
        # signaturePad canvas.toDataURL that produces them).
        # The previous blanket `https:` was dropped after inventorying every
        # <img src> and every css url() in templates/ and static/ - none
        # point at an external host, so nothing needs it. (Bootstrap/Font
        # Awesome's own inline icon images are data: URIs, already allowed.)
        'img-src': ["'self'", "data:"],
        # cdn.jsdelivr.net/stackpath.bootstrapcdn.com here aren't for the app
        # itself - they're so the browser's devtools can fetch .map files for
        # those CDN scripts/styles without a console warning. Harmless either
        # way; only affects developers with devtools open.
        'connect-src': ["'self'", PESAPAL_ORIGIN, "https://cdn.jsdelivr.net", "https://stackpath.bootstrapcdn.com"],
        'frame-src': ["'self'", PESAPAL_ORIGIN],
        # Clickjacking defence, and the modern replacement for the
        # X-Frame-Options header set in add_security_headers() below (that
        # one is kept for old browsers that never implemented
        # frame-ancestors). 'self' matches the existing SAMEORIGIN value.
        'frame-ancestors': "'self'",
        # No template contains a <base> tag (verified), so nothing legitimate
        # needs to set one - and an injected <base href> would silently
        # repoint every relative form/link in the page at an attacker.
        'base-uri': "'self'",
        # WHY PesaPal IS LISTED HERE AND NOT JUST 'self': the pay button is a
        # real <form method="POST"> to /portal/invoice/<token>/pay, and that
        # route answers with a 302 to PesaPal's hosted checkout. Chrome and
        # Safari apply form-action to the redirect CHAIN of a form
        # submission, not only to the form's own action attribute, so a bare
        # form-action 'self' would block the redirect and break payment in
        # those browsers while looking fine in Firefox and completely
        # invisible to curl. Same applies to the subscription checkout in
        # api/subscription.py, which redirects to PesaPal the same way.
        'form-action': ["'self'", PESAPAL_ORIGIN],
    }

    # Initialize Talisman (security headers).
    # No nonce_in at all: per the CSP spec, a nonce in a directive makes the
    # browser ignore 'unsafe-inline' for that directive entirely. The nonce
    # only ever attaches to <script>/<style> TAGS (Talisman rewrites those
    # automatically) - it can't attach to an inline style="..." attribute or
    # an inline onclick=/onsubmit="..." event handler on an ordinary element.
    # This app uses both throughout (21 onclick/onsubmit handlers across 11
    # templates, e.g. the portal's Accept Quote button and every delete
    # confirmation in the app) - nonce-ing script-src broke every one of them
    # silently, the same way nonce-ing style-src broke every inline style.
    # curl-based testing never caught this because curl doesn't execute JS;
    # the backend route was always fine, the browser-side handler just never
    # ran. Trade-off: no nonce-based script hardening at all now, since this
    # codebase isn't written in a nonce-compatible style (that would mean
    # converting all 21+ inline handlers to addEventListener - a real
    # refactor, not something to do silently inside a bug fix).
    talisman = Talisman(
        app,
        content_security_policy=csp,
        force_https=is_production,  # Redirect HTTP to HTTPS in production only
        # HSTS. Only emitted when the request actually arrived over TLS -
        # Talisman's _set_hsts_headers checks (request.is_secure or
        # X-Forwarded-Proto == 'https'), which is exactly right behind
        # Vercel's TLS-terminating proxy and is also why plain
        # http://localhost development never receives the header (which would
        # otherwise pin a developer's browser to https for localhost and be
        # painful to undo).
        strict_transport_security=is_production,  # Enable HSTS in production only
        # These three are Talisman's own defaults, restated explicitly so the
        # values are visible here and cannot drift if a future Talisman
        # release changes its defaults.
        strict_transport_security_max_age=31556926,  # 1 year (already live)
        strict_transport_security_include_subdomains=True,
        # NEVER turn this on without the owner's explicit decision: preload
        # submits the domain to a list baked into browser binaries and is
        # effectively irreversible for months.
        strict_transport_security_preload=False,
        session_cookie_secure=is_production,  # Cookies only sent over HTTPS in production
        session_cookie_http_only=True,  # Prevent JavaScript access to cookies
        feature_policy={  # Legacy Feature-Policy header, for old browsers
            'geolocation': "'none'",
            'microphone': "'none'",
            'camera': "'none'",
            'payment': "'self'",
        },
        # The MODERN header. Without this argument Talisman emits its own
        # default Permissions-Policy of just 'browsing-topics=()', and
        # because Talisman's after_request runs AFTER the
        # add_security_headers one below (Flask runs after_request handlers
        # in reverse registration order, so the earlier-registered Talisman
        # hook gets the last word), that default was silently OVERWRITING
        # the geolocation/microphone/camera restrictions this app sets
        # below - they never reached the browser. Setting them here puts
        # them on the header that actually wins.
        permissions_policy={
            'geolocation': '()',
            'microphone': '()',
            'camera': '()',
            # self + PesaPal: frame-src permits PesaPal's hosted checkout, so
            # if it is ever framed rather than redirected to, the Payment
            # Request API must not be blocked inside it.
            'payment': f'(self "{PESAPAL_ORIGIN}")',
            'browsing-topics': '()',  # keep Talisman's own opt-out
        },
    )

    # flask-talisman does not register itself in app.extensions the way most
    # Flask extensions do, so stash it here. HSTS is only switched on when
    # is_production is true, which it is not under pytest - without a handle
    # on this object a test could not exercise the real HSTS code path at all
    # (see tests/test_security_headers_and_rate_limits.py, which flips this
    # flag and puts it back).
    app.extensions['talisman'] = talisman

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
        # NOTE: Permissions-Policy is deliberately NOT set here any more.
        # Talisman's after_request runs after this one and overwrites it, so
        # a value set here never reached the browser. It is configured on the
        # Talisman(...) call above instead - see the comment there.
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
