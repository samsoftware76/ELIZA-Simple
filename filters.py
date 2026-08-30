"""Template filters for the ELIZA application"""

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
    """Convert newlines to <br> tags"""
    if not value:
        return ""
    return value.replace('\n', '<br>')

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
