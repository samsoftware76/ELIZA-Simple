"""
Regression coverage for the actual subscription/billing bugs fixed this
session, plus the plan-limit enforcement that leans on the same models:

  * The PesaPal callback payload shape - get_transaction_status() in
    api/payment.py returns {'payment_status': ..., ...}, never a 'status'
    key. api/subscription.py's payment_callback() used to read only
    payment_status.get('status') (always None/UNKNOWN), so no payment ever
    activated a subscription. A successful callback must actually flip the
    subscription onto the new plan.
  * Clicking a different plan while an ACTIVE PAID subscription exists must
    NOT mutate plan_id/is_active/is_trial - subscribe() used to deactivate
    the paid subscription and drop the customer into a fresh trial, losing
    the plan they had paid for. Byte-compared before/after.
  * A project/client create is blocked once a plan's max_projects/
    max_clients is reached, and allowed when unlimited (0 or no
    subscription at all).
"""
from datetime import datetime, timedelta

from conftest import login_as, get_csrf_token
from models.subscription import Subscription


def _token(client, url='/clients/create'):
    return get_csrf_token(client, url)


# ---------------------------------------------------------------------------
# PesaPal callback payload shape - 'payment_status', not 'status'
# ---------------------------------------------------------------------------

def test_successful_callback_flips_subscription_to_the_new_plan(
        client, owner_user, make_subscription, make_subscription_plan, monkeypatch):
    import api.subscription as subscription_module

    current_plan = make_subscription_plan(name='Starter Plan For This Test')
    target_plan = make_subscription_plan(name='Target Plan For This Test', price_monthly=49.99)
    subscription = make_subscription(
        owner_user, plan=current_plan,
        is_active=True, is_trial=False, billing_cycle='monthly',
        end_date=datetime.utcnow() + timedelta(days=20),
        pending_plan_id=target_plan.id,
        pending_billing_cycle='monthly',
        pending_change_at=None,  # NULL = upgrade awaiting payment (see models/subscription.py)
    )

    # The EXACT shape PesaPalPayment.get_transaction_status() returns in
    # api/payment.py - only 'payment_status', never 'status'. This is the
    # real regression surface: reading the wrong key here is precisely the
    # bug that shipped and was fixed this session.
    fake_status = {
        'payment_status': 'COMPLETED',
        'payment_method': 'Visa',
        'amount': 49.99,
        'created_date': '2026-01-01T00:00:00',
        'confirmation_code': 'ABC123',
    }
    monkeypatch.setattr(subscription_module.pesapal, 'get_transaction_status', lambda order_tracking_id: fake_status)

    login_as(client, owner_user)
    resp = client.get('/payment/callback?OrderTrackingId=fake-tracking-id', follow_redirects=True)
    assert resp.status_code == 200

    refreshed = Subscription.query.get(subscription.id)
    assert refreshed.plan_id == target_plan.id
    assert refreshed.is_active is True
    assert refreshed.is_trial is False
    assert refreshed.pending_plan_id is None
    assert refreshed.pending_billing_cycle is None


def test_callback_with_only_the_old_status_key_reads_it_as_a_fallback(
        client, owner_user, make_subscription, make_subscription_plan, monkeypatch):
    """The fix keeps reading 'status' as a fallback (for anything that
    somehow still depended on it) - it just checks 'payment_status' FIRST.
    A payload with ONLY the legacy key must still activate the plan change."""
    import api.subscription as subscription_module

    current_plan = make_subscription_plan(name='Starter Plan B')
    target_plan = make_subscription_plan(name='Target Plan B', price_monthly=49.99)
    subscription = make_subscription(
        owner_user, plan=current_plan,
        is_active=True, is_trial=False, billing_cycle='monthly',
        end_date=datetime.utcnow() + timedelta(days=20),
        pending_plan_id=target_plan.id,
        pending_billing_cycle='monthly',
        pending_change_at=None,
    )

    fake_status = {'status': 'COMPLETED'}  # legacy shape only, no 'payment_status' key at all
    monkeypatch.setattr(subscription_module.pesapal, 'get_transaction_status', lambda order_tracking_id: fake_status)

    login_as(client, owner_user)
    resp = client.get('/payment/callback?OrderTrackingId=fake-tracking-id', follow_redirects=True)
    assert resp.status_code == 200

    refreshed = Subscription.query.get(subscription.id)
    assert refreshed.plan_id == target_plan.id


# ---------------------------------------------------------------------------
# Clicking a different plan on an ACTIVE PAID subscription must not mutate it
# ---------------------------------------------------------------------------

def test_clicking_a_different_plan_on_active_paid_subscription_does_not_mutate_it(
        client, owner_user, make_subscription, make_subscription_plan):
    current_plan = make_subscription_plan(name='Whatever Current Plan')
    subscription = make_subscription(
        owner_user, plan=current_plan,
        is_active=True, is_trial=False, billing_cycle='monthly',
        start_date=datetime.utcnow() - timedelta(days=5),
        end_date=datetime.utcnow() + timedelta(days=25),
    )
    before = (
        subscription.plan_id, subscription.is_active, subscription.is_trial,
        subscription.billing_cycle, subscription.start_date, subscription.end_date,
        subscription.pending_plan_id, subscription.pending_billing_cycle, subscription.pending_change_at,
    )

    login_as(client, owner_user)
    resp = client.get('/subscribe/professional/monthly', follow_redirects=False)
    # Case 3 in subscribe(): never destructive, never immediate - it redirects
    # to change_plan() rather than mutating anything itself.
    assert resp.status_code == 302
    assert '/subscription/change/professional/monthly' in resp.headers.get('Location', '')

    refreshed = Subscription.query.get(subscription.id)
    after = (
        refreshed.plan_id, refreshed.is_active, refreshed.is_trial,
        refreshed.billing_cycle, refreshed.start_date, refreshed.end_date,
        refreshed.pending_plan_id, refreshed.pending_billing_cycle, refreshed.pending_change_at,
    )
    assert before == after, "clicking a different plan mutated the active paid subscription row"


def test_clicking_the_same_plan_already_subscribed_to_is_a_no_op(
        client, owner_user, make_subscription, make_subscription_plan):
    """Belt-and-braces: even the same-plan/cycle short-circuit in subscribe()
    must not touch the row. Uses the real 'Basic' catalog entry (see
    api/subscription.py's PLAN_CATALOG) so /subscribe/basic/monthly resolves
    to the SAME plan row this subscription is already on."""
    current_plan = make_subscription_plan(
        name='Basic', price_monthly=9.99, price_yearly=99.99,
        max_projects=5, max_users=3, max_clients=10,
    )
    subscription = make_subscription(
        owner_user, plan=current_plan,
        is_active=True, is_trial=False, billing_cycle='monthly',
        end_date=datetime.utcnow() + timedelta(days=25),
    )
    before = (subscription.plan_id, subscription.is_active, subscription.is_trial, subscription.billing_cycle)

    login_as(client, owner_user)
    resp = client.get('/subscribe/basic/monthly', follow_redirects=True)
    assert resp.status_code == 200
    assert 'You are already on the Basic plan' in resp.get_data(as_text=True)

    refreshed = Subscription.query.get(subscription.id)
    after = (refreshed.plan_id, refreshed.is_active, refreshed.is_trial, refreshed.billing_cycle)
    assert before == after


# ---------------------------------------------------------------------------
# Plan limits actually enforced on the create routes
# ---------------------------------------------------------------------------

def test_client_create_blocked_once_at_plan_limit(client, owner_user, make_subscription, make_subscription_plan, make_client):
    plan = make_subscription_plan(max_clients=1)
    make_subscription(owner_user, plan=plan)
    make_client(owner_user)  # fills the one available slot

    login_as(client, owner_user)
    from models.models import Client
    before_count = Client.query.filter_by(owner_id=owner_user.id).count()
    assert before_count == 1

    token = _token(client)
    resp = client.post('/clients/create', data={
        'name': 'One Too Many Co', 'contact_person': 'Someone', 'phone': '+256700000000',
        'email': '', 'address': '', 'notes': '', 'submit': 'Save', 'csrf_token': token,
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert 'reached your plan limit' in resp.get_data(as_text=True)
    assert Client.query.filter_by(owner_id=owner_user.id).count() == before_count


def test_client_create_allowed_when_unlimited_plan(client, owner_user, make_subscription, make_subscription_plan, make_client):
    plan = make_subscription_plan(max_clients=0)  # 0 = unlimited
    make_subscription(owner_user, plan=plan)
    for _ in range(3):
        make_client(owner_user)

    login_as(client, owner_user)
    from models.models import Client
    before_count = Client.query.filter_by(owner_id=owner_user.id).count()

    token = _token(client)
    resp = client.post('/clients/create', data={
        'name': 'Yet Another Co', 'contact_person': 'Someone', 'phone': '+256700000001',
        'email': '', 'address': '', 'notes': '', 'submit': 'Save', 'csrf_token': token,
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert Client.query.filter_by(owner_id=owner_user.id).count() == before_count + 1


def test_client_create_allowed_with_no_subscription_at_all(client, owner_user, make_client):
    login_as(client, owner_user)
    from models.models import Client
    before_count = Client.query.filter_by(owner_id=owner_user.id).count()

    token = _token(client)
    resp = client.post('/clients/create', data={
        'name': 'No Plan Co', 'contact_person': 'Someone', 'phone': '+256700000002',
        'email': '', 'address': '', 'notes': '', 'submit': 'Save', 'csrf_token': token,
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert Client.query.filter_by(owner_id=owner_user.id).count() == before_count + 1


def test_project_create_blocked_once_at_plan_limit(client, owner_user, make_subscription, make_subscription_plan, make_client, make_project):
    plan = make_subscription_plan(max_projects=1)
    make_subscription(owner_user, plan=plan)
    a_client = make_client(owner_user)
    make_project(a_client)  # fills the one available slot

    login_as(client, owner_user)
    from models.models import Project
    before_count = Project.query.filter_by(owner_id=owner_user.id).count()
    assert before_count == 1

    token = _token(client)
    resp = client.post('/projects/create', data={
        'title': 'One Too Many Project', 'description': '', 'client_id': str(a_client.id),
        'start_date': '2026-01-01', 'end_date': '', 'status': 'PENDING', 'priority': '2',
        'budget': '', 'zoom_link': '', 'drive_link': '', 'whatsapp_contact': '',
        'submit': 'Save', 'csrf_token': token,
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert 'reached your plan limit' in resp.get_data(as_text=True)
    assert Project.query.filter_by(owner_id=owner_user.id).count() == before_count


def test_project_create_allowed_when_unlimited_plan(client, owner_user, make_subscription, make_subscription_plan, make_client, make_project):
    plan = make_subscription_plan(max_projects=0)  # unlimited
    make_subscription(owner_user, plan=plan)
    a_client = make_client(owner_user)
    make_project(a_client)

    login_as(client, owner_user)
    from models.models import Project
    before_count = Project.query.filter_by(owner_id=owner_user.id).count()

    token = _token(client)
    resp = client.post('/projects/create', data={
        'title': 'Another Fine Project', 'description': '', 'client_id': str(a_client.id),
        'start_date': '2026-01-01', 'end_date': '', 'status': 'PENDING', 'priority': '2',
        'budget': '', 'zoom_link': '', 'drive_link': '', 'whatsapp_contact': '',
        'submit': 'Save', 'csrf_token': token,
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert Project.query.filter_by(owner_id=owner_user.id).count() == before_count + 1
