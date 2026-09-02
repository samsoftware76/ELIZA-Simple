"""Template filters for the ELIZA application"""
from markupsafe import Markup, escape

def status_badge(status):
    """Convert a status name to a Bootstrap badge class"""
    status_map = {
        'PENDING': 'secondary',
        'IN_PROGRESS': 'primary',
        'COMPLETED': 'success',
        'ON_HOLD': 'warning',
        'CANCELLED': 'danger'
    }
    return status_map.get(status, 'secondary')

def nl2br(value):
    """Convert newlines to <br> tags.

    Escapes the raw value FIRST, then substitutes real <br> tags into the
    escaped text, then returns the result as Markup - so the output is safe
    to render even without |safe (every call site already chains |safe for
    the line breaks; Markup + |safe together is harmless, since Jinja's
    |safe just wraps a value in Markup and wrapping an already-Markup value
    doesn't re-escape it). Fixes stored XSS at the source: content used to
    go straight to the page as raw HTML, so a <script> tag typed into any
    field rendered through this filter (task/project descriptions, comments,
    invoice/quote notes) would execute in whoever viewed it next.
    """
    if not value:
        return ""
    return escape(value).replace('\n', Markup('<br>'))

def billing_status_badge(status):
    """Convert a quote/invoice status string to a Bootstrap badge class"""
    status_map = {
        'draft': 'secondary',
        'sent': 'primary',
        'accepted': 'success',
        'paid': 'success',
        'declined': 'danger',
        'cancelled': 'danger',
    }
    return status_map.get(status, 'secondary')

def register_filters(app):
    """Register all filters with the Flask app"""
    app.jinja_env.filters['status_badge'] = status_badge
    app.jinja_env.filters['nl2br'] = nl2br
    app.jinja_env.filters['billing_status_badge'] = billing_status_badge
