"""
Subscription plan limit enforcement for ELIZA.

SubscriptionPlan.max_projects / max_clients / max_users were advertised on the
pricing page but enforced nowhere except team seats (api/team.py). This module
is the single place those limits are resolved, so the plans page, the
clients/projects list banners and the create routes all agree on what "you are
at your limit" means instead of each re-deriving it.

Conventions, all inherited from api/team.py's existing seat cap so the three
limits behave identically:

  * 0 means unlimited (matching the column comments in models/subscription.py).
  * NO active subscription also means unlimited - a brand-new account with no
    plan at all is never blocked from using the app.
  * An active TRIAL subscription IS limited, by its plan's numbers. This is
    deliberately the same as api/team.py, which has always applied max_users to
    a trial (it looks up the active subscription without checking is_trial). A
    trial is supposed to be the paid plan you're evaluating, so it has to have
    the paid plan's ceilings or the trial teaches the customer the wrong thing
    and the limit only appears the day they start paying. See the note in the
    batch summary - if trials should instead be unlimited, this is the one
    place to change it (and api/team.py alongside).

Everything here is scoped by owner_id (User.get_owner_id()), never by
current_user.id: subscriptions, clients and projects all belong to the account
owner, and an invited team member shares them.
"""
from datetime import datetime, timedelta

from models import db
# TeamMembership is no longer imported here - "an active team member" is now
# defined in exactly one place (api.team.active_memberships_query, imported
# lazily inside count_team_seats below) so this module can't drift from it.
from models.models import Client, Project, TeamInvite
from models.subscription import Subscription

# 0 in any max_* column means "no ceiling" - see models/subscription.py.
UNLIMITED = 0

# A usage row is flagged "near" at 80% of its limit, which is what the
# non-blocking banner on the clients/projects list pages keys off.
NEAR_LIMIT_RATIO = 0.8

# Days in a billing cycle, matching the arithmetic already used in
# api/subscription.py's trial/callback end_date calculations.
CYCLE_DAYS = {'monthly': 30, 'yearly': 365}


def cycle_length(billing_cycle):
    """Days in a billing cycle - 'monthly' -> 30, anything else -> 365.

    Kept here rather than repeated inline so the plan-change path and the
    payment callback in api/subscription.py can't drift apart.
    """
    return CYCLE_DAYS.get(billing_cycle, CYCLE_DAYS['yearly'])


def apply_due_plan_change(subscription):
    """Apply a downgrade that was scheduled for the end of the current cycle.

    A downgrade from a PAID plan doesn't take effect immediately (the customer
    paid through the end of the cycle), so subscribe() stores the target on the
    row itself - pending_plan_id / pending_billing_cycle / pending_change_at -
    and this applies it once that date has passed.

    There is no scheduler or cron in this app, so it is applied lazily on read
    instead: get_active_subscription() below is what every limit check and the
    plans page goes through, which is exactly where the plan actually matters.

    pending_change_at is NULL for the OTHER kind of pending change - an upgrade
    waiting on a PesaPal payment - and those are never applied here, only by
    subscription.payment_callback() on a confirmed payment. That distinction is
    the whole safety property: nothing on this read path can hand out a
    higher-priced plan that was never paid for.

    Wrapped in its own try/except: this runs on ordinary page loads, and a
    failure to apply a scheduled downgrade must never 500 a page. Returns the
    subscription either way.
    """
    if subscription is None:
        return None
    if not subscription.pending_plan_id or not subscription.pending_change_at:
        return subscription
    if datetime.utcnow() < subscription.pending_change_at:
        return subscription

    try:
        effective_at = subscription.pending_change_at
        subscription.plan_id = subscription.pending_plan_id
        subscription.billing_cycle = subscription.pending_billing_cycle or subscription.billing_cycle
        # The new (cheaper) cycle starts where the paid-for one ended, so the
        # dates stay contiguous rather than the row keeping a stale end_date
        # in the past.
        subscription.start_date = effective_at
        subscription.end_date = effective_at + timedelta(days=cycle_length(subscription.billing_cycle))
        subscription.pending_plan_id = None
        subscription.pending_billing_cycle = None
        subscription.pending_change_at = None
        db.session.commit()
    except Exception:
        db.session.rollback()
    return subscription


def get_active_subscription(owner_id):
    """The account's active subscription, or None.

    Same lookup home() and inject_sidebar_plan() use in api/index.py, plus the
    lazy scheduled-downgrade application above.
    """
    subscription = Subscription.query.filter_by(user_id=owner_id, is_active=True).first()
    return apply_due_plan_change(subscription)


def count_projects(owner_id):
    """Projects owned by this tenant - same owner_id scoping as projects()."""
    return Project.query.filter_by(owner_id=owner_id).count()


def count_clients(owner_id):
    """Clients owned by this tenant - same owner_id scoping as clients()."""
    return Client.query.filter_by(owner_id=owner_id).count()


def count_team_seats(owner_id):
    """Seats used: active memberships + still-pending invites.

    Shares api/team.py's active_memberships_query() rather than re-deriving
    "active member" here, so the number shown on the plans page is the same
    number invite() enforces - a burst of pending invites counts against the
    cap there, so it has to count here too. The owner themselves is NOT
    counted: max_users is advertised everywhere as how many people can be
    invited, not the account's total headcount.

    A DEACTIVATED member consumes no seat. Their TeamMembership row is still
    is_active=True (an admin deactivating an account never touches the
    owner's team roster), so counting memberships alone would keep billing
    the owner for someone who cannot log in - which is exactly the drift
    active_memberships_query() exists to prevent.

    Imported inside the function rather than at module scope on purpose:
    api/team.py imports a Blueprint and registers routes at import time, and
    this module is imported by api/index.py during app setup. Keeping the
    dependency lazy means utils.plan_limits stays importable on its own (as
    the tests import it) and can never participate in an import cycle if
    api/team.py later wants a helper from here.
    """
    from api.team import active_memberships_query

    active_members = active_memberships_query(owner_id).count()
    pending_invites = TeamInvite.query.filter_by(inviter_id=owner_id, status='pending').count()
    return active_members + pending_invites


def _plan_limit(subscription, attr):
    """A max_* value off the active plan, normalised so None -> UNLIMITED."""
    if subscription is None or subscription.plan is None:
        return UNLIMITED
    return getattr(subscription.plan, attr, None) or UNLIMITED


def _can_add(owner_id, limit_attr, counter, subscription=None):
    """(allowed, limit, current) for one countable resource.

    subscription is accepted so a caller that already loaded it (the plans
    page, which needs all three rows) doesn't re-query per resource.
    """
    if subscription is None:
        subscription = get_active_subscription(owner_id)
    limit = _plan_limit(subscription, limit_attr)
    current = counter(owner_id)
    if limit == UNLIMITED:
        return True, limit, current
    return current < limit, limit, current


def can_add_project(owner_id, subscription=None):
    """(allowed, limit, current) - limit 0 means unlimited and allowed is True."""
    return _can_add(owner_id, 'max_projects', count_projects, subscription)


def can_add_client(owner_id, subscription=None):
    """(allowed, limit, current) - limit 0 means unlimited and allowed is True."""
    return _can_add(owner_id, 'max_clients', count_clients, subscription)


def can_add_team_member(owner_id, subscription=None):
    """(allowed, limit, current) for team seats.

    api/team.py keeps its own inline counting (it needs the member/invite split
    for its own messaging); this exists so the plans page can show the seat row
    using exactly the same arithmetic.
    """
    return _can_add(owner_id, 'max_users', count_team_seats, subscription)


def _usage_row(key, label, allowed, limit, current, plan_name):
    """One row of the plans-page usage summary / list-page banner."""
    unlimited = (limit == UNLIMITED)
    if unlimited:
        percent = 0
    else:
        percent = min(100, int(round((current / float(limit)) * 100))) if limit else 0
    at_limit = (not unlimited) and current >= limit
    near_limit = (not unlimited) and current >= (limit * NEAR_LIMIT_RATIO)
    if at_limit:
        bar_class = 'bg-danger'
    elif near_limit:
        bar_class = 'bg-warning'
    else:
        bar_class = 'bg-success'
    return {
        'key': key,
        'label': label,
        'current': current,
        'limit': limit,
        'unlimited': unlimited,
        'percent': percent,
        'at_limit': at_limit,
        'near_limit': near_limit,
        'bar_class': bar_class,
        'plan_name': plan_name,
        'allowed': allowed,
    }


def usage_summary(owner_id):
    """Everything the plans page needs to render "Projects 4 of 5" with bars.

    Returns a dict with the active subscription (or None), its plan name, and
    a `rows` list of the three countable limits. With no active subscription
    every row is unlimited, which is what an account with no plan actually has.
    """
    subscription = get_active_subscription(owner_id)
    plan = subscription.plan if subscription else None
    plan_name = plan.name if plan else None

    rows = []
    for key, label, checker in (
        ('projects', 'Projects', can_add_project),
        ('clients', 'Clients', can_add_client),
        ('team', 'Team', can_add_team_member),
    ):
        allowed, limit, current = checker(owner_id, subscription=subscription)
        rows.append(_usage_row(key, label, allowed, limit, current, plan_name))

    return {
        'subscription': subscription,
        'plan': plan,
        'plan_name': plan_name,
        'is_trial': bool(subscription and subscription.is_trial),
        'rows': rows,
    }


def limit_notice(owner_id, key):
    """Banner data for the clients/projects LIST pages, or None.

    Only returned once usage is at or above NEAR_LIMIT_RATIO of a real (non-
    zero) limit, so the banner stays quiet for accounts nowhere near their cap
    and for unlimited/no-plan accounts. Purely informational - it never blocks
    anything, the create routes do that.
    """
    subscription = get_active_subscription(owner_id)
    if subscription is None:
        return None
    checker = {'projects': can_add_project, 'clients': can_add_client}.get(key)
    if checker is None:
        return None
    label = {'projects': 'Projects', 'clients': 'Clients'}[key]
    allowed, limit, current = checker(owner_id, subscription=subscription)
    row = _usage_row(key, label, allowed, limit, current,
                     subscription.plan.name if subscription.plan else None)
    if row['unlimited'] or not row['near_limit']:
        return None
    return row
