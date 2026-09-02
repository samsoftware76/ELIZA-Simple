"""Template filters for the ELIZA application"""
from datetime import datetime

from markupsafe import Markup, escape

def status_badge(status):
    """Convert a ProjectStatus/TaskStatus (or its .name) to a Bootstrap badge class.

    Accepts the enum member itself as well as the plain name string. Most call
    sites pass the enum directly (`project.status|status_badge`) while a few
    pass `.name` - and an enum member never matched a string key, so every one
    of the former silently fell through to the default 'secondary' and every
    project/task badge in the app rendered grey regardless of status. Reading
    .name when it exists is what makes the map actually apply; the .name call
    sites behave exactly as before.
    """
    status_map = {
        'PENDING': 'secondary',
        'IN_PROGRESS': 'primary',
        'COMPLETED': 'success',
        'ON_HOLD': 'warning',
        'CANCELLED': 'danger',
        # TaskStatus members that ProjectStatus doesn't share.
        'TODO': 'secondary',
        'REVIEW': 'info',
    }
    key = getattr(status, 'name', status)
    return status_map.get(key, 'secondary')

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
    """Convert a quote/invoice/contract status string to a Bootstrap badge class"""
    status_map = {
        'draft': 'secondary',
        'sent': 'primary',
        'accepted': 'success',
        'paid': 'success',
        'signed': 'success',
        'declined': 'danger',
        'cancelled': 'danger',
        # A withdrawn contract isn't a failure (that's 'declined', red) and
        # isn't in play either - dark reads as "closed, deliberately".
        'voided': 'dark',
    }
    return status_map.get(status, 'secondary')

def timeago(value):
    """Relative time for feed rows: '2 hours ago', 'yesterday', ...

    Model timestamps in this codebase are naive UTC (datetime.utcnow
    defaults on created_at columns), so this compares against utcnow, not
    local now - comparing a UTC timestamp against local time would shift
    every row by the server's UTC offset.
    Falls back to an absolute date past ~30 days, where relative wording
    stops being useful.
    """
    if not value:
        return ''
    seconds = (datetime.utcnow() - value).total_seconds()
    if seconds < 60:
        return 'just now'
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = int(seconds // 3600)
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = int(seconds // 86400)
    if days == 1:
        return 'yesterday'
    if days < 30:
        return f'{days} days ago'
    return value.strftime('%b %d, %Y')

def approx_display(amount, from_currency):
    """Approximate display-currency equivalent, e.g. "~ USD 135.20" - or ''.

    Display-layer ONLY (see User.display_currency in models/models.py): the
    real amount is always rendered by the template itself, this only supplies
    the muted approximate text shown NEXT TO it. Returns '' (never an error,
    never an unmarked stale fabrication) unless ALL of:
    - current_user is authenticated and has display_currency set on their
      OWN row (a personal preference, deliberately not owner-resolved),
    - display_currency differs from the amount's real currency,
    - amount is a real number,
    - a live-ish rate is available (utils/exchange_rates.get_rate returns
      None on any failure, which simply hides the conversion).

    Safe to call inside table loops: get_rate() hits a module-level cache,
    so a whole request costs at most one rate lookup per distinct
    (from_currency, display_currency) pair, not one per row.
    """
    from flask_login import current_user
    from utils.exchange_rates import get_rate
    try:
        if not current_user.is_authenticated:
            return ''
        to_currency = getattr(current_user, 'display_currency', None)
        if not to_currency or not from_currency:
            return ''
        if str(to_currency).upper() == str(from_currency).upper():
            return ''
        if amount is None:
            return ''
        amount = float(amount)
        rate = get_rate(from_currency, to_currency)
        if rate is None:
            return ''
        return f"~ {to_currency} {amount * rate:,.2f}"
    except Exception:
        # A display nicety must never be the thing that 500s a money page.
        return ''


def register_filters(app):
    """Register all filters with the Flask app"""
    # Imported inside the function, not at module scope: filters.py is
    # imported by api/index.py while the app is being built, and keeping the
    # models import lazy here matches how the rest of this module avoids
    # importing app-level objects at import time (see approx_display).
    from models.models import humanize_role

    app.jinja_env.filters['status_badge'] = status_badge
    app.jinja_env.filters['nl2br'] = nl2br
    app.jinja_env.filters['billing_status_badge'] = billing_status_badge
    app.jinja_env.filters['timeago'] = timeago
    # UserRole.PROJECT_MANAGER -> 'Project Manager'. The one implementation of
    # that transform (models.models.humanize_role), shared with
    # TeamMembership.display_role/TeamInvite.display_role so a template
    # showing the raw ACCESS LEVEL formats it identically to the fallback
    # those properties produce.
    app.jinja_env.filters['humanize_role'] = humanize_role
    # A global (not a filter): called as approx_display(amount, currency) in
    # templates next to real amounts. Returns '' whenever no conversion
    # should be shown, so call sites just {% if %} on the result.
    app.jinja_env.globals['approx_display'] = approx_display
