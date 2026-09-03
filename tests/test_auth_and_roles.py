"""
Authentication + role-gate coverage:

  * A CLIENT-role login is redirected away from every staff route it tries
    (restrict_client_role_to_portal in api/index.py) and can reach its own
    /my dashboard.
  * A non-admin (ordinary staff DEVELOPER) is rejected from /admin/* routes
    (admin_required in api/admin.py).
  * A team member sees the read-only /team view (no invite/remove controls
    in the response HTML) while the owner sees the full one.
  * Inviting a team member with role=ADMIN is rejected - the
    ADMIN-exclusion-from-invites security control (forms.TeamInviteForm.role
    only offers PROJECT_MANAGER/DEVELOPER/SECRETARY as valid choices).
"""
import pytest

from conftest import login_as, get_csrf_token


STAFF_ROUTES = ['/clients', '/projects', '/wallet', '/admin/dashboard']


# ---------------------------------------------------------------------------
# CLIENT-role login: redirected away from staff routes, can reach /my
# ---------------------------------------------------------------------------

def test_client_role_redirected_away_from_every_staff_route(client, owner_user, make_client, make_client_login):
    a_client = make_client(owner_user)
    portal_user = make_client_login(a_client)
    login_as(client, portal_user)

    for route in STAFF_ROUTES:
        resp = client.get(route, follow_redirects=False)
        assert resp.status_code == 302, f"{route} did not redirect a CLIENT-role login (got {resp.status_code})"
        assert '/my' in resp.headers.get('Location', ''), (
            f"{route} redirected a CLIENT-role login somewhere other than the portal: "
            f"{resp.headers.get('Location')!r}"
        )


def test_client_role_can_reach_its_own_my_dashboard(client, owner_user, make_client, make_client_login):
    a_client = make_client(owner_user)
    portal_user = make_client_login(a_client)
    login_as(client, portal_user)

    resp = client.get('/my')
    assert resp.status_code == 200
    assert f'Welcome, {a_client.name}' in resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Non-admin staff user rejected from /admin/*
# ---------------------------------------------------------------------------

def test_non_admin_staff_user_rejected_from_admin_dashboard(client, owner_user):
    login_as(client, owner_user)  # default role is DEVELOPER, not ADMIN
    resp = client.get('/admin/dashboard', follow_redirects=True)
    assert resp.status_code == 200
    # admin_required redirects to home() with a flash (see api/admin.py) -
    # a staff (non-CLIENT) user never touches restrict_client_role_to_portal,
    # so this is a distinct rejection path from the CLIENT-role redirect above.
    assert 'You do not have permission to access this page' in resp.get_data(as_text=True)


def test_admin_role_can_reach_admin_dashboard(client, make_user):
    from models.models import UserRole
    admin = make_user(role=UserRole.ADMIN)
    login_as(client, admin)
    resp = client.get('/admin/dashboard')
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Team page: read-only for a member, full for the owner
# ---------------------------------------------------------------------------

def test_team_member_sees_read_only_view_no_invite_or_remove_controls(client, owner_user, make_team_member):
    member = make_team_member(owner_user)
    login_as(client, member)
    resp = client.get('/team')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Invite a Team Member' not in html
    assert 'Pending Invites' not in html
    assert 'confirmAndDisable(this, \'Remove' not in html


def test_team_owner_sees_full_view_with_invite_and_remove_controls(client, owner_user, make_team_member):
    make_team_member(owner_user)
    login_as(client, owner_user)
    resp = client.get('/team')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Invite a Team Member' in html
    assert 'confirmAndDisable(this, \'Remove' in html


# ---------------------------------------------------------------------------
# ADMIN exclusion from team invites
# ---------------------------------------------------------------------------

def test_inviting_a_team_member_with_admin_role_is_rejected(client, owner_user):
    from models.models import TeamInvite
    login_as(client, owner_user)
    token = get_csrf_token(client, '/team')
    resp = client.post('/team/invite', data={
        'email': 'wouldbe-admin@example.com',
        'role': 'ADMIN',  # not one of TeamInviteForm.role's valid choices
        'role_title': '',
        'csrf_token': token,
    }, follow_redirects=True)
    assert resp.status_code == 200
    # SelectField rejects an out-of-choice value, so validate_on_submit()
    # returns False and no invite is ever created for this email.
    assert TeamInvite.query.filter_by(invitee_email='wouldbe-admin@example.com').first() is None


def test_inviting_a_team_member_with_a_valid_role_succeeds(client, owner_user):
    from models.models import TeamInvite, UserRole
    login_as(client, owner_user)
    token = get_csrf_token(client, '/team')
    resp = client.post('/team/invite', data={
        'email': 'new-hire@example.com',
        'role': 'DEVELOPER',
        'role_title': 'Developer',
        'csrf_token': token,
    }, follow_redirects=True)
    assert resp.status_code == 200
    invite = TeamInvite.query.filter_by(invitee_email='new-hire@example.com').first()
    assert invite is not None
    assert invite.role == UserRole.DEVELOPER
