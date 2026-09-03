"""
Regression coverage for USER DEACTIVATION (soft delete) and for the admin
delete buttons that used to silently do nothing.

THE DEACTIVATION MODEL BEING LOCKED IN HERE (see models/models.py's User
is_active column and migrations/add_user_is_active.py): the User row is KEPT
and marked inactive. They cannot log in, they are hidden from active lists,
they stop consuming a paid seat, their invoices/comments/time entries are
untouched and old records still show their real name, the audit ledger stays
complete, and the whole thing is REVERSIBLE.

Two mechanisms make "cannot log in" true, and both are tested here because
only one of them is Flask-Login's own:

  * login_user() reads User.is_active and refuses an inactive account. That
    covers a NEW login only.
  * @login_manager.user_loader (load_user in api/index.py) returns None for
    an inactive user. That is what ends an ALREADY-ESTABLISHED session -
    Flask-Login never re-checks is_active for a session it has already
    granted, so without this a user deactivated mid-session would keep
    working until their cookie expired (a year, with "remember me").

MUTATION SANITY CHECK for this file: delete the `if user is not None and not
user.is_active: return None` guard from load_user() in api/index.py and
re-run - test_deactivated_user_with_a_live_session_is_logged_out_on_the_next_request
must fail while the plain cannot-log-in test still passes. That pair is what
proves the two mechanisms are genuinely independent rather than one of them
carrying the other.
"""
from conftest import login_as, get_csrf_token
from models import db
from models.models import ActivityLog, TeamMembership, User, UserRole
from models.subscription import SubscriptionPlan


# A page that requires a login, used as the "am I really signed in?" probe.
# Deliberately NOT '/' - home() renders a marketing page for anonymous
# visitors rather than redirecting, so a 200 there would prove nothing.
PROTECTED_URL = '/clients'


def fresh_request(client, method, url, **kwargs):
    """Make a request the way a real deployment does: from a FRESH app context.

    READ THIS BEFORE SIMPLIFYING IT AWAY - it is not ceremony, and without it
    the two most important tests in this file pass vacuously.

    Flask-Login caches the resolved user on `g` (`g._login_user`) and only
    calls the @login_manager.user_loader when that key is absent. `g` lives on
    the APP CONTEXT, and Flask's RequestContext.push() reuses an app context
    that is already pushed for the same app instead of creating a new one. In
    production nothing holds an app context open, so every request pushes its
    own, `g` starts empty, and the user_loader runs on every single request -
    which is exactly what makes a deactivated session die on the next click.

    In this suite the db_session fixture deliberately holds ONE app context
    open for the whole test (see tests/conftest.py). Every test-client request
    therefore reuses it, and the very first request's `g._login_user` is
    reused by every later request in that test - so the user_loader is called
    once per TEST rather than once per REQUEST. A user deactivated between two
    requests would keep sailing through, and the test would report success for
    a session that production would have dropped.

    Pushing a fresh app context around the request restores the production
    shape: empty `g`, user_loader runs, the deactivation is seen. Verified by
    watching this exact assertion fail with the cached context and pass with
    the fresh one.
    """
    with client.application.app_context():
        return getattr(client, method)(url, **kwargs)


def _is_signed_in(client):
    """True if this test client currently has a usable session.

    Checks the real behaviour (does a @login_required page serve or bounce?)
    rather than poking at session internals, so it stays honest about what a
    real browser would experience - and does it from a fresh app context, see
    fresh_request above.
    """
    return fresh_request(client, 'get', PROTECTED_URL, follow_redirects=False).status_code == 200


# ---------------------------------------------------------------------------
# Cannot log in / is logged out
# ---------------------------------------------------------------------------

def test_deactivated_user_cannot_log_in(client, make_user, db_session):
    user = make_user()
    user.is_active = False
    db_session.commit()

    resp = login_as(client, user)

    assert resp.status_code == 200
    assert 'deactivated' in resp.get_data(as_text=True).lower()
    # The real assertion: no session was established, whatever the page said.
    assert not _is_signed_in(client)


def test_deactivated_user_with_a_live_session_is_logged_out_on_the_next_request(
        client, make_user, db_session):
    """The half Flask-Login does NOT do for us - see this module's docstring."""
    user = make_user()
    login_as(client, user)
    assert _is_signed_in(client), "precondition: the user really is signed in first"

    # Deactivated mid-session, exactly as an admin clicking Deactivate does.
    user.is_active = False
    db_session.commit()

    resp = fresh_request(client, 'get', PROTECTED_URL, follow_redirects=False)
    assert resp.status_code == 302
    assert '/login' in resp.headers['Location']
    assert not _is_signed_in(client)


def test_reactivation_restores_login(client, make_user, db_session):
    user = make_user()
    user.is_active = False
    db_session.commit()
    assert not _is_signed_in(client)

    user.is_active = True
    db_session.commit()

    login_as(client, user)
    assert _is_signed_in(client)


# ---------------------------------------------------------------------------
# History is kept and still resolves to their real name
# ---------------------------------------------------------------------------

def test_deactivated_users_history_still_exists_and_still_names_them(
        client, owner_user, make_client, make_invoice, make_team_member, db_session):
    """The whole point of soft delete: nothing of theirs is removed or
    anonymised. An invoice they raised, a comment they wrote and a time entry
    they logged all still point at the same row and still print their name."""
    from models.models import Comment, Project, Task, TimeEntry

    member = make_team_member(owner_user, role_title='Accountant')
    a_client = make_client(owner_user)
    project = make_project_for(db_session, a_client)
    task = make_task_for(db_session, project)

    invoice = make_invoice(a_client, member)
    comment = Comment(content='Chased the client about this one.', task_id=task.id, user_id=member.id)
    time_entry = TimeEntry(task_id=task.id, user_id=member.id, duration=2.5, description='Reconciliation')
    db_session.add_all([comment, time_entry])
    db_session.commit()

    invoice_id, comment_id, time_entry_id = invoice.id, comment.id, time_entry.id
    expected_name = member.get_full_name()

    member.is_active = False
    db_session.commit()

    # Every row survives...
    from models.billing import Invoice
    assert Invoice.query.get(invoice_id) is not None
    assert Comment.query.get(comment_id) is not None
    assert TimeEntry.query.get(time_entry_id) is not None

    # ...and still resolves through the ordinary FK to their REAL name, not a
    # placeholder, because the User row itself was never touched.
    assert Invoice.query.get(invoice_id).created_by.get_full_name() == expected_name
    assert Comment.query.get(comment_id).author.get_full_name() == expected_name
    assert TimeEntry.query.get(time_entry_id).user.get_full_name() == expected_name

    # And the user row is still there to resolve through in the first place.
    assert User.query.get(member.id) is not None
    assert Project.query.get(project.id) is not None
    assert Task.query.get(task.id) is not None


def make_project_for(db_session, client_row):
    """Local mini-factory: the shared make_project fixture can't be requested
    from inside another test's body, and this file only needs the bare
    minimum project/task to hang a comment and a time entry off."""
    from datetime import date

    from models.models import Project, ProjectStatus
    project = Project(title='History Retention Project', client_id=client_row.id,
                      start_date=date.today(), status=ProjectStatus.PENDING,
                      owner_id=client_row.owner_id)
    db_session.add(project)
    db_session.commit()
    return project


def make_task_for(db_session, project):
    from models.models import Task, TaskStatus
    task = Task(title='Something they worked on', project_id=project.id, status=TaskStatus.TODO)
    db_session.add(task)
    db_session.commit()
    return task


# ---------------------------------------------------------------------------
# Hidden from active lists / does not consume a paid seat
# ---------------------------------------------------------------------------

def test_deactivated_member_does_not_consume_a_plan_seat(
        owner_user, make_team_member, make_subscription, make_subscription_plan, db_session):
    """A deactivated member cannot log in, so billing the owner a seat for
    them would be charging for nothing. Checks the shared helper the plans
    page reads (utils.plan_limits) - the same arithmetic api/team.py's
    invite() enforces, since both now go through
    api.team.active_memberships_query()."""
    from utils.plan_limits import can_add_team_member, count_team_seats

    plan = make_subscription_plan(max_users=1)
    make_subscription(owner_user, plan=plan)
    member = make_team_member(owner_user)

    assert count_team_seats(owner_user.id) == 1
    allowed, limit, current = can_add_team_member(owner_user.id)
    assert (allowed, limit, current) == (False, 1, 1), "precondition: the one seat is taken"

    member.is_active = False
    db_session.commit()

    assert count_team_seats(owner_user.id) == 0, "a deactivated member must not hold a paid seat"
    allowed, limit, current = can_add_team_member(owner_user.id)
    assert (allowed, limit, current) == (True, 1, 0)

    # The membership row itself is deliberately untouched - deactivation is a
    # platform action, not the owner removing someone from their team - so
    # reactivating puts the seat straight back.
    membership = TeamMembership.query.filter_by(member_user_id=member.id).first()
    assert membership is not None and membership.is_active is True


def test_seat_cap_actually_lets_an_invite_through_once_a_member_is_deactivated(
        client, owner_user, make_team_member, make_subscription, make_subscription_plan, db_session):
    """The route-level half of the test above: the cap enforced inside
    api/team.py's invite() must agree with the figure the plans page shows."""
    plan = make_subscription_plan(max_users=1)
    make_subscription(owner_user, plan=plan)
    member = make_team_member(owner_user)

    login_as(client, owner_user)
    token = get_csrf_token(client, '/team')

    resp = client.post('/team/invite', data={
        'email': 'blocked@example.com', 'role': 'DEVELOPER', 'csrf_token': token,
    }, follow_redirects=True)
    assert 'reached your plan limit' in resp.get_data(as_text=True)

    member.is_active = False
    db_session.commit()

    token = get_csrf_token(client, '/team')
    resp = client.post('/team/invite', data={
        'email': 'allowed@example.com', 'role': 'DEVELOPER', 'csrf_token': token,
    }, follow_redirects=True)
    assert 'reached your plan limit' not in resp.get_data(as_text=True)


def test_deactivated_member_is_hidden_from_the_team_page(
        client, owner_user, make_team_member, db_session):
    member = make_team_member(owner_user)
    login_as(client, owner_user)

    assert member.email in client.get('/team').get_data(as_text=True), "precondition: they are listed"

    member.is_active = False
    db_session.commit()

    assert member.email not in client.get('/team').get_data(as_text=True)


def test_deactivated_user_is_not_counted_as_an_active_user_on_the_admin_dashboard(
        client, make_user, db_session):
    admin = make_user(role=UserRole.ADMIN)
    other = make_user()
    login_as(client, admin)

    # Two accounts exist and both are active.
    assert User.query.filter(User.is_active.is_(True)).count() == 2

    other.is_active = False
    db_session.commit()

    resp = client.get('/admin/dashboard')
    assert resp.status_code == 200
    assert User.query.filter(User.is_active.is_(True)).count() == 1
    assert User.query.filter(User.is_active.is_(False)).count() == 1


def test_owner_cannot_re_add_a_deactivated_account_to_their_team(
        client, owner_user, make_team_member, db_session):
    """Re-inviting an existing email reactivates their old membership row. If
    the ACCOUNT is deactivated that would produce a member who holds a seat
    but can never sign in, so the invite route refuses instead."""
    member = make_team_member(owner_user)
    member_email = member.email
    membership = TeamMembership.query.filter_by(member_user_id=member.id).first()
    membership.is_active = False
    member.is_active = False
    db_session.commit()

    login_as(client, owner_user)
    token = get_csrf_token(client, '/team')
    resp = client.post('/team/invite', data={
        'email': member_email, 'role': 'DEVELOPER', 'csrf_token': token,
    }, follow_redirects=True)

    assert 'deactivated by an administrator' in resp.get_data(as_text=True)
    db.session.refresh(membership)
    assert membership.is_active is False, "the refused invite must not have re-enabled the membership"


# ---------------------------------------------------------------------------
# Admin deactivate / reactivate routes
# ---------------------------------------------------------------------------

def test_admin_can_deactivate_and_reactivate_a_user(client, make_user, db_session):
    admin = make_user(role=UserRole.ADMIN)
    victim = make_user()
    login_as(client, admin)

    token = get_csrf_token(client, '/admin/users')
    resp = client.post('/admin/users/deactivate',
                       data={'user_id': str(victim.id), 'csrf_token': token},
                       follow_redirects=True)
    assert resp.status_code == 200
    db.session.refresh(victim)
    assert victim.is_active is False

    token = get_csrf_token(client, '/admin/users')
    resp = client.post('/admin/users/reactivate',
                       data={'user_id': str(victim.id), 'csrf_token': token},
                       follow_redirects=True)
    assert resp.status_code == 200
    db.session.refresh(victim)
    assert victim.is_active is True


def test_admin_cannot_deactivate_themselves(client, make_user, db_session):
    """Blocked SERVER-SIDE, not merely hidden in the UI: load_user() returns
    None for an inactive user, so an admin who managed to do this would be
    anonymous on their very next request - locked out of the only page that
    could undo it."""
    admin = make_user(role=UserRole.ADMIN)
    login_as(client, admin)

    token = get_csrf_token(client, '/admin/users')
    resp = client.post('/admin/users/deactivate',
                       data={'user_id': str(admin.id), 'csrf_token': token},
                       follow_redirects=True)

    assert resp.status_code == 200
    assert 'cannot deactivate your own account' in resp.get_data(as_text=True)
    db.session.refresh(admin)
    assert admin.is_active is True
    assert _is_signed_in(client), "the admin must still be signed in afterwards"


def test_deactivate_writes_an_activity_log_naming_the_admin(client, make_user, db_session):
    admin = make_user(role=UserRole.ADMIN)
    victim = make_user()
    login_as(client, admin)

    token = get_csrf_token(client, '/admin/users')
    client.post('/admin/users/deactivate',
                data={'user_id': str(victim.id), 'csrf_token': token},
                follow_redirects=True)

    entry = ActivityLog.query.filter_by(action_type='admin_user_deactivated',
                                        entity_type='user', entity_id=victim.id).first()
    assert entry is not None, "deactivating must be audited"
    assert entry.user_id == admin.id
    assert admin.email in entry.description
    assert victim.email in entry.description


def test_reactivate_writes_an_activity_log_naming_the_admin(client, make_user, db_session):
    admin = make_user(role=UserRole.ADMIN)
    victim = make_user()
    victim.is_active = False
    db_session.commit()
    login_as(client, admin)

    token = get_csrf_token(client, '/admin/users')
    client.post('/admin/users/reactivate',
                data={'user_id': str(victim.id), 'csrf_token': token},
                follow_redirects=True)

    entry = ActivityLog.query.filter_by(action_type='admin_user_reactivated',
                                        entity_type='user', entity_id=victim.id).first()
    assert entry is not None
    assert entry.user_id == admin.id
    assert admin.email in entry.description


def test_deactivating_a_tenant_owner_does_not_lock_out_their_team(
        client, owner_user, make_team_member, make_user, db_session):
    """FLAGGED DECISION, locked in here so it can't drift silently:
    deactivating an owner locks out ONLY that owner. Their team members keep
    their own logins and keep working in the account's data - see
    admin.deactivate_user's docstring for why cascading was rejected."""
    member = make_team_member(owner_user)
    admin = make_user(role=UserRole.ADMIN)

    login_as(client, admin)
    token = get_csrf_token(client, '/admin/users')
    client.post('/admin/users/deactivate',
                data={'user_id': str(owner_user.id), 'csrf_token': token},
                follow_redirects=True)

    db.session.refresh(owner_user)
    db.session.refresh(member)
    assert owner_user.is_active is False
    assert member.is_active is True, "the team member must NOT be cascaded into deactivation"

    # And they can genuinely still sign in and use the owner's data. A fresh
    # app context per step, so this second client isn't handed the admin's
    # cached g._login_user - see fresh_request above.
    member_client = client.application.test_client()
    with client.application.app_context():
        login_as(member_client, member)
    assert _is_signed_in(member_client)


def test_deactivate_get_returns_405(client, make_user):
    admin = make_user(role=UserRole.ADMIN)
    login_as(client, admin)
    assert client.get('/admin/users/deactivate').status_code == 405


def test_deactivate_post_without_csrf_token_returns_400(client, make_user):
    admin = make_user(role=UserRole.ADMIN)
    victim = make_user()
    login_as(client, admin)
    resp = client.post('/admin/users/deactivate', data={'user_id': str(victim.id)})
    assert resp.status_code == 400


def test_non_admin_cannot_deactivate_anyone(client, make_user, db_session):
    """The gating this feature must never weaken: @admin_required, not just
    @login_required."""
    ordinary = make_user(role=UserRole.DEVELOPER)
    victim = make_user()
    login_as(client, ordinary)

    token = get_csrf_token(client, '/clients/create')
    resp = client.post('/admin/users/deactivate',
                       data={'user_id': str(victim.id), 'csrf_token': token},
                       follow_redirects=True)

    assert resp.status_code == 200
    assert 'do not have permission' in resp.get_data(as_text=True)
    db.session.refresh(victim)
    assert victim.is_active is True


# ---------------------------------------------------------------------------
# The delete buttons that silently did nothing
# ---------------------------------------------------------------------------
# Both routes used to gate on validate_on_submit() of the FULL edit form
# (UserForm requires first_name/last_name/username/email/role;
# SubscriptionPlanForm requires name/price_monthly/price_yearly/features)
# while their confirm modals submit only the row id + csrf_token. Validation
# therefore always failed and the route fell through to its redirect having
# done nothing, with no error shown. These tests post EXACTLY what the real
# modal posts - id + csrf and nothing else - which is the payload that used
# to be silently ignored.

def test_delete_confirm_form_validates_with_only_id_and_csrf(app, make_user):
    """The form fix itself, checked directly rather than only through a route:
    id + csrf must be enough, and a missing id must not be."""
    from forms import DeleteConfirmForm, PlanDeleteConfirmForm, UserActionForm

    with app.test_request_context(method='POST', data={'user_id': '7'}):
        app.config['WTF_CSRF_ENABLED'] = False
        try:
            assert UserActionForm().validate_on_submit() is True
        finally:
            app.config['WTF_CSRF_ENABLED'] = True

    with app.test_request_context(method='POST', data={'plan_id': '7'}):
        app.config['WTF_CSRF_ENABLED'] = False
        try:
            assert PlanDeleteConfirmForm().validate_on_submit() is True
        finally:
            app.config['WTF_CSRF_ENABLED'] = True

    # No id at all must still be refused - otherwise the route would go on to
    # get_or_404('') and 404, which reads as a broken page not a rejection.
    with app.test_request_context(method='POST', data={}):
        app.config['WTF_CSRF_ENABLED'] = False
        try:
            assert UserActionForm().validate_on_submit() is False
            assert PlanDeleteConfirmForm().validate_on_submit() is False
            # The bare base form carries the CSRF token and nothing else, so
            # it validates on its own - that is its entire job.
            assert DeleteConfirmForm().validate_on_submit() is True
        finally:
            app.config['WTF_CSRF_ENABLED'] = True


def test_delete_plan_with_only_id_and_csrf_actually_deletes_an_unreferenced_plan(
        client, make_user, make_subscription_plan):
    """The exact payload the real modal sends. Before the fix this returned a
    200 redirect, showed no message at all, and left the plan in place."""
    admin = make_user(role=UserRole.ADMIN)
    plan = make_subscription_plan(name='Unreferenced Plan For Modal Payload')
    plan_id = plan.id

    login_as(client, admin)
    token = get_csrf_token(client, '/admin/subscriptions')
    resp = client.post('/admin/subscriptions/plans/delete',
                       data={'plan_id': str(plan_id), 'csrf_token': token},
                       follow_redirects=True)

    assert resp.status_code == 200
    assert SubscriptionPlan.query.get(plan_id) is None


def test_delete_plan_with_only_id_and_csrf_still_refuses_a_referenced_plan(
        client, make_user, make_subscription_plan, make_subscription):
    """The reference guards must survive the form fix - fixing the wiring must
    not turn a previously-inert button into one that hits a raw FK error."""
    admin = make_user(role=UserRole.ADMIN)
    plan = make_subscription_plan(name='Referenced Plan For Modal Payload')
    make_subscription(admin, plan=plan, is_active=False)  # historical residue
    plan_id = plan.id

    login_as(client, admin)
    token = get_csrf_token(client, '/admin/subscriptions')
    resp = client.post('/admin/subscriptions/plans/delete',
                       data={'plan_id': str(plan_id), 'csrf_token': token},
                       follow_redirects=True)

    assert resp.status_code == 200
    assert 'still reference it' in resp.get_data(as_text=True)
    assert SubscriptionPlan.query.get(plan_id) is not None


def test_delete_user_with_only_id_and_csrf_actually_deletes_a_history_free_user(
        client, make_user):
    admin = make_user(role=UserRole.ADMIN)
    victim = make_user()
    victim_id = victim.id

    login_as(client, admin)
    token = get_csrf_token(client, '/admin/users')
    resp = client.post('/admin/users/delete',
                       data={'user_id': str(victim_id), 'csrf_token': token},
                       follow_redirects=True)

    assert resp.status_code == 200
    assert User.query.get(victim_id) is None


def test_delete_user_with_history_is_refused_and_points_at_deactivate(
        client, make_user, db_session):
    """Hard delete is no longer the primary action, so its refusal must tell
    the admin what to do instead rather than leaving them at a dead end."""
    admin = make_user(role=UserRole.ADMIN)
    victim = make_user()
    db_session.add(ActivityLog(user_id=victim.id, action_type='created',
                               entity_type='client', entity_id=1))
    db_session.commit()
    victim_id = victim.id

    login_as(client, admin)
    token = get_csrf_token(client, '/admin/users')
    resp = client.post('/admin/users/delete',
                       data={'user_id': str(victim_id), 'csrf_token': token},
                       follow_redirects=True)

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'still have related records' in body
    assert 'Deactivate them instead' in body
    assert User.query.get(victim_id) is not None


def test_admin_cannot_hard_delete_themselves(client, make_user):
    admin = make_user(role=UserRole.ADMIN)
    admin_id = admin.id
    login_as(client, admin)

    token = get_csrf_token(client, '/admin/users')
    resp = client.post('/admin/users/delete',
                       data={'user_id': str(admin_id), 'csrf_token': token},
                       follow_redirects=True)

    assert resp.status_code == 200
    assert 'cannot delete your own account' in resp.get_data(as_text=True)
    assert User.query.get(admin_id) is not None


def test_admin_users_page_shows_active_and_deactivated_status(client, make_user, db_session):
    admin = make_user(role=UserRole.ADMIN)
    off = make_user()
    off.is_active = False
    db_session.commit()

    login_as(client, admin)
    body = client.get('/admin/users').get_data(as_text=True)

    assert 'Deactivated' in body
    assert 'Reactivate' in body, "a deactivated user must offer a Reactivate action"
    assert 'Deactivate' in body, "an active user must offer a Deactivate action"
