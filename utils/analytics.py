"""
Lightweight product-usage analytics for the ELIZA Project Management App.

Platform-wide (not owner_id-scoped) - see models.models.AnalyticsEvent. The
single entry point here, log_event(), is meant to be sprinkled into
call sites all over the app (registration, login, client/project creation,
quote/invoice lifecycle, subscription trial/conversion, team invites) so we
can later see where trial users drop off and what correlates with converting
to a paying subscriber.
"""
import json
import logging

from models import db
from models.models import AnalyticsEvent

logger = logging.getLogger(__name__)


def log_event(event_type, user_id=None, **metadata):
    """Record a single analytics event. Never raises.

    Same defensive spirit as send_email-style functions in
    utils/email_utils.py: analytics is a nice-to-have, and a DB hiccup or bad
    metadata value here must never turn into a 500 on the real user-facing
    request that triggered it. Every failure mode - building the event,
    json.dumps()-ing metadata, committing - is caught and swallowed as a
    logged warning, so this is safe to call from anywhere with no
    try/except at the call site.

    Args:
        event_type (str): e.g. 'user_registered', 'invoice_paid'.
        user_id (int, optional): the acting/subject user, if known.
        **metadata: small bits of extra context (invoice_id=5, amount=100, ...),
            JSON-encoded into AnalyticsEvent.event_metadata if any are passed.
    """
    try:
        event_metadata = json.dumps(metadata) if metadata else None
        event = AnalyticsEvent(
            user_id=user_id,
            event_type=event_type,
            event_metadata=event_metadata,
        )
        db.session.add(event)
        db.session.commit()
    except Exception as e:
        # Roll back so a broken analytics write can't leave the session in a
        # dirty state for whatever real request logic runs next.
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.warning(f"Failed to log analytics event '{event_type}' (user_id={user_id}): {str(e)}")
