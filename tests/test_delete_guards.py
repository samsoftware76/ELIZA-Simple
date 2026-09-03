"""
Regression coverage for the "delete X without checking every NOT NULL FK
into it" bug class found this bug-hunt session - the same root cause
(no ON DELETE CASCADE anywhere in the schema, see the grep in the task
report) surfaced independently in four different delete routes:

  * client_delete() only checked client.projects / an active portal login -
    a client with a quote, invoice, contract, or portal invite on file (all
    NOT NULL client_id FKs) would otherwise hit an unhandled IntegrityError
    trying to null that FK out from under it. Now guarded the same way the
    projects check already was, plus a defense-in-depth try/except.
  * project_delete() only checked project.tasks - a project with zero tasks
    but a team member assigned (ProjectMember.project_id is NOT NULL) hit
    the same class of error, just with an existing (uninformative) generic
    try/except catching it instead of a friendly guard message.
  * delete_plan() only counted is_active=True subscriptions - a plan with
    any INACTIVE subscription history (the normal residue of every plan
    switch - subscribe() sets is_active=False rather than deleting) or any
    subscription only referencing it via pending_plan_id still hit the DB's
    own FK constraint despite the guard reporting "0 active subscriptions".
  * delete_user() had no protection at all - ActivityLog/Comment/TimeEntry/
    TeamMembership/Subscription/Quote/Invoice/Contract/TeamInvite/
    ClientInvite all have NOT NULL FKs to users.id. Deciding what SHOULD
    happen to that history is a real product decision (out of scope for a
    bug-hunt pass), so this is only a safety net: a clean rollback and a
    friendly flash instead of a raw 500.

NOTE on delete_user/delete_plan test data: the real "Delete" modals for both
(templates/admin/users.html, templates/admin/subscriptions.html) only ever
submit the row id + csrf_token, but api/admin.py's delete_user()/delete_plan()
gate on validate_on_submit() of the FULL edit form (UserForm/
SubscriptionPlanForm), which has DataRequired on several other fields -
first_name/last_name/username/email/role, or name/price_monthly/
price_yearly/features respectively. That means the delete buttons as
currently wired never actually reach the delete code at all (a separate,
already-flagged bug - see the task report's confirmed_real_but_deferred).
These tests submit a full, validating form payload so the ROUTE's own
delete-then-handle-the-FK-error logic is exercised directly, independent of
that separate wiring gap.
"""
from conftest import login_as, get_csrf_token
from models.models import Client, Project, User, UserRole
from models.subscription import Subscription, SubscriptionPlan


# ---------------------------------------------------------------------------
# client_delete() - quotes / invoices / contracts / invites guards
# ---------------------------------------------------------------------------

def test_client_delete_blocked_when_client_has_a_quote(client, owner_user, make_client, make_quote):
    a_client = make_client(owner_user)
    make_quote(a_client, owner_user)
    login_as(client, owner_user)

    token = get_csrf_token(client, '/clients/create')
    resp = client.post(f'/clients/{a_client.id}/delete', data={'csrf_token': token}, follow_redirects=True)
    assert resp.status_code == 200
    assert 'still has quotes' in resp.get_data(as_text=True)
    assert Client.query.get(a_client.id) is not None


def test_client_delete_blocked_when_client_has_an_invoice(client, owner_user, make_client, make_invoice):
    a_client = make_client(owner_user)
    make_invoice(a_client, owner_user)
    login_as(client, owner_user)

    token = get_csrf_token(client, '/clients/create')
    resp = client.post(f'/clients/{a_client.id}/delete', data={'csrf_token': token}, follow_redirects=True)
    assert resp.status_code == 200
    assert 'still has invoices' in resp.get_data(as_text=True)
    assert Client.query.get(a_client.id) is not None


def test_client_delete_blocked_when_client_has_a_contract(client, owner_user, make_client, make_contract):
    a_client = make_client(owner_user)
    make_contract(a_client, owner_user)
    login_as(client, owner_user)

    token = get_csrf_token(client, '/clients/create')
    resp = client.post(f'/clients/{a_client.id}/delete', data={'csrf_token': token}, follow_redirects=True)
    assert resp.status_code == 200
    assert 'still has contracts' in resp.get_data(as_text=True)
    assert Client.query.get(a_client.id) is not None


def test_client_delete_blocked_when_client_has_a_portal_invite(client, owner_user, make_client, db_session):
    from models.models import ClientInvite

    a_client = make_client(owner_user)
    invite = ClientInvite(inviter_id=owner_user.id, client_id=a_client.id, invitee_email='someone@example.com', status='pending')
    db_session.add(invite)
    db_session.commit()
    login_as(client, owner_user)

    token = get_csrf_token(client, '/clients/create')
    resp = client.post(f'/clients/{a_client.id}/delete', data={'csrf_token': token}, follow_redirects=True)
    assert resp.status_code == 200
    assert 'still has portal invites' in resp.get_data(as_text=True)
    assert Client.query.get(a_client.id) is not None


def test_client_delete_still_works_with_no_related_records(client, owner_user, make_client):
    """Regression guard: a genuinely empty client must still delete cleanly."""
    a_client = make_client(owner_user)
    login_as(client, owner_user)

    token = get_csrf_token(client, '/clients/create')
    resp = client.post(f'/clients/{a_client.id}/delete', data={'csrf_token': token}, follow_redirects=True)
    assert resp.status_code == 200
    assert Client.query.get(a_client.id) is None


# ---------------------------------------------------------------------------
# project_delete() - project_members guard
# ---------------------------------------------------------------------------

def test_project_delete_blocked_when_project_has_a_team_member_but_no_tasks(
        client, owner_user, make_client, make_project, make_user, db_session):
    from models.models import ProjectMember

    a_client = make_client(owner_user)
    project = make_project(a_client)
    member_user = make_user(role=UserRole.DEVELOPER)
    pm = ProjectMember(project_id=project.id, user_id=member_user.id, role='developer')
    db_session.add(pm)
    db_session.commit()

    login_as(client, owner_user)
    token = get_csrf_token(client, '/clients/create')
    resp = client.post(f'/projects/{project.id}/delete', data={'csrf_token': token}, follow_redirects=True)
    assert resp.status_code == 200
    assert 'still has team members assigned' in resp.get_data(as_text=True)
    assert Project.query.get(project.id) is not None


# ---------------------------------------------------------------------------
# delete_plan() - must count ALL referencing subscriptions, not just active
# ones, and must also catch pending_plan_id references.
# ---------------------------------------------------------------------------

def test_delete_plan_blocked_by_an_inactive_historical_subscription(client, make_user, make_subscription_plan, make_subscription):
    """The normal residue of a plan switch: subscribe() sets is_active=False
    rather than deleting the old row. The pre-fix guard only counted
    is_active=True and would have let this delete through to crash."""
    admin = make_user(role=UserRole.ADMIN)
    plan = make_subscription_plan(name='Plan With Churned Customer')
    make_subscription(admin, plan=plan, is_active=False)

    login_as(client, admin)
    token = get_csrf_token(client, '/admin/subscriptions')
    resp = client.post('/admin/subscriptions/plans/delete', data={
        'plan_id': str(plan.id), 'name': plan.name, 'price_monthly': '9.99', 'price_yearly': '99.99',
        'features': 'x', 'csrf_token': token,
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert 'still reference it' in resp.get_data(as_text=True)
    assert SubscriptionPlan.query.get(plan.id) is not None


def test_delete_plan_blocked_by_a_pending_plan_id_reference(client, make_user, make_subscription_plan, make_subscription):
    """A subscription can reference a plan ONLY via pending_plan_id (an
    in-flight upgrade target) while plan_id points elsewhere entirely - the
    pre-fix guard (filtered on plan_id only) never saw this at all."""
    admin = make_user(role=UserRole.ADMIN)
    current_plan = make_subscription_plan(name='Current Plan For Pending Test')
    target_plan = make_subscription_plan(name='Target Plan For Pending Test')
    make_subscription(admin, plan=current_plan, is_active=True, pending_plan_id=target_plan.id, pending_change_at=None)

    login_as(client, admin)
    token = get_csrf_token(client, '/admin/subscriptions')
    resp = client.post('/admin/subscriptions/plans/delete', data={
        'plan_id': str(target_plan.id), 'name': target_plan.name, 'price_monthly': '9.99', 'price_yearly': '99.99',
        'features': 'x', 'csrf_token': token,
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert 'still reference it' in resp.get_data(as_text=True)
    assert SubscriptionPlan.query.get(target_plan.id) is not None


def test_delete_plan_with_zero_references_still_works(client, make_user, make_subscription_plan):
    admin = make_user(role=UserRole.ADMIN)
    plan = make_subscription_plan(name='Totally Unused Plan')

    login_as(client, admin)
    token = get_csrf_token(client, '/admin/subscriptions')
    resp = client.post('/admin/subscriptions/plans/delete', data={
        'plan_id': str(plan.id), 'name': plan.name, 'price_monthly': '9.99', 'price_yearly': '99.99',
        'features': 'x', 'csrf_token': token,
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert SubscriptionPlan.query.get(plan.id) is None


# ---------------------------------------------------------------------------
# delete_user() - must degrade to a clean rollback + friendly message, never
# a raw 500, when the user still has related records.
# ---------------------------------------------------------------------------

def test_delete_user_with_related_activity_log_degrades_gracefully_instead_of_500(
        client, make_user, db_session):
    from models.models import ActivityLog

    admin = make_user(role=UserRole.ADMIN)
    victim = make_user(role=UserRole.DEVELOPER)
    # ActivityLog.user_id is a NOT NULL FK with no cascade - this is the
    # single most common row type in the app (written on nearly every
    # action), so it stands in for "any user who has ever done anything".
    log_row = ActivityLog(user_id=victim.id, action_type='created', entity_type='client', entity_id=1)
    db_session.add(log_row)
    db_session.commit()

    login_as(client, admin)
    token = get_csrf_token(client, '/admin/users')
    resp = client.post('/admin/users/delete', data={
        'user_id': str(victim.id), 'first_name': victim.first_name, 'last_name': victim.last_name,
        'username': victim.username, 'email': victim.email, 'role': victim.role.name,
        'csrf_token': token,
    }, follow_redirects=True)

    # The real regression: this must NOT be an unhandled 500.
    assert resp.status_code == 200
    assert 'still have related records' in resp.get_data(as_text=True)
    # And the user must genuinely still exist - a clean rollback, not a
    # partial delete.
    assert User.query.get(victim.id) is not None


def test_delete_user_with_no_related_records_still_works(client, make_user):
    admin = make_user(role=UserRole.ADMIN)
    victim = make_user(role=UserRole.DEVELOPER)

    login_as(client, admin)
    token = get_csrf_token(client, '/admin/users')
    resp = client.post('/admin/users/delete', data={
        'user_id': str(victim.id), 'first_name': victim.first_name, 'last_name': victim.last_name,
        'username': victim.username, 'email': victim.email, 'role': victim.role.name,
        'csrf_token': token,
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert User.query.get(victim.id) is None
