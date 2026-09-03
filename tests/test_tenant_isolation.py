"""
Cross-tenant isolation sweep - the automated, permanent version of the manual
cross-tenant sweep run by hand this session.

For every id-bearing resource type (client, project, task, quote, invoice,
contract): create it under owner A, log in as owner B (a second, completely
independent tenant), and assert every view/edit/delete/action route on A's
resource returns 404 for B - never a 403 (see the "404 not 403" comments
throughout api/index.py, api/billing.py and api/contracts.py: a 403 would
let B distinguish "not mine" from "doesn't exist" and confirm A's ids exist).

Each test creates its own A/B pair via owner_user/other_owner_user rather than
sharing state across tests - db_session resets the whole schema between tests
anyway (see conftest.py), so this is just being explicit about the two-tenant
setup at each call site.

Every POST route below is CSRF-protected (WTF_CSRF_ENABLED stays True for the
whole session - see conftest.py's `app` fixture), so each POST here goes
through _post(), which attaches a real token for the CURRENT session rather
than disabling CSRF outright: a token is bound to the session, not to the
specific route it was rendered on (conftest.login_as already relies on this
same fact - it fetches its token from GET /login and uses it to POST to
/login). That keeps this file testing exactly one thing - does the ownership
check 404 - without a token-less POST being rejected by CSRFProtect's
before_request hook before the view (and its 404 check) ever runs. CSRF
enforcement itself is covered exhaustively in test_csrf_and_methods.py.
"""
from conftest import login_as, get_csrf_token


def _login_as_b(client, other_owner_user):
    resp = login_as(client, other_owner_user)
    assert resp.status_code == 200


def _post(client, url, data=None):
    """POST url with a valid CSRF token for the logged-in session."""
    token = get_csrf_token(client, '/clients/create')
    payload = dict(data or {})
    payload['csrf_token'] = token
    return client.post(url, data=payload)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def test_client_routes_404_for_other_tenant(client, owner_user, other_owner_user, make_client):
    a_client = make_client(owner_user)
    _login_as_b(client, other_owner_user)

    assert client.get(f'/clients/{a_client.id}').status_code == 404
    assert client.get(f'/clients/{a_client.id}/edit').status_code == 404
    assert _post(client, f'/clients/{a_client.id}/edit', {}).status_code == 404
    assert _post(client, f'/clients/{a_client.id}/delete').status_code == 404
    assert _post(client, f'/clients/{a_client.id}/invite-portal').status_code == 404
    assert _post(client, f'/clients/{a_client.id}/revoke-portal-access').status_code == 404
    assert _post(client, f'/clients/{a_client.id}/send-sms', {'message': 'hi'}).status_code == 404


def test_client_list_never_shows_other_tenants_client(client, owner_user, other_owner_user, make_client):
    a_client = make_client(owner_user, name='Only Owner A Should See This Co')
    _login_as_b(client, other_owner_user)
    resp = client.get('/clients')
    assert a_client.name not in resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

def test_project_routes_404_for_other_tenant(client, owner_user, other_owner_user, make_client, make_project):
    a_client = make_client(owner_user)
    a_project = make_project(a_client)
    _login_as_b(client, other_owner_user)

    assert client.get(f'/projects/{a_project.id}').status_code == 404
    assert client.get(f'/projects/{a_project.id}/edit').status_code == 404
    assert _post(client, f'/projects/{a_project.id}/edit', {}).status_code == 404
    assert _post(client, f'/projects/{a_project.id}/complete').status_code == 404
    assert _post(client, f'/projects/{a_project.id}/reopen').status_code == 404
    assert _post(client, f'/projects/{a_project.id}/delete').status_code == 404
    assert _post(client, f'/projects/{a_project.id}/team/assign',
                 {'user_id': owner_user.id, 'role': 'developer'}).status_code == 404
    assert _post(client, f'/projects/{a_project.id}/team/remove/{owner_user.id}').status_code == 404


def test_project_list_never_shows_other_tenants_project(client, owner_user, other_owner_user, make_client, make_project):
    a_client = make_client(owner_user)
    a_project = make_project(a_client, title='Only Owner A Should See This Project')
    _login_as_b(client, other_owner_user)
    resp = client.get('/projects')
    assert a_project.title not in resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------

def test_task_routes_404_for_other_tenant(client, owner_user, other_owner_user, make_client, make_project, make_task):
    a_client = make_client(owner_user)
    a_project = make_project(a_client)
    a_task = make_task(a_project)
    _login_as_b(client, other_owner_user)

    assert client.get(f'/tasks/{a_task.id}').status_code == 404
    assert client.get(f'/tasks/{a_task.id}/edit').status_code == 404
    assert _post(client, f'/tasks/{a_task.id}/edit', {}).status_code == 404
    assert _post(client, f'/tasks/{a_task.id}/submit-review').status_code == 404
    assert _post(client, f'/tasks/{a_task.id}/mark-complete').status_code == 404
    assert _post(client, f'/tasks/{a_task.id}/delete').status_code == 404
    assert _post(client, f'/tasks/{a_task.id}/comment', {'content': 'hi'}).status_code == 404
    assert _post(client, f'/tasks/{a_task.id}/time/start').status_code == 404
    assert _post(client, f'/tasks/{a_task.id}/time/stop').status_code == 404
    assert client.get(f'/tasks/{a_task.id}/time/add').status_code == 404


def test_task_comment_delete_routes_404_for_other_tenant(client, owner_user, other_owner_user,
                                                          make_client, make_project, make_task, db_session):
    from models.models import Comment
    a_client = make_client(owner_user)
    a_project = make_project(a_client)
    a_task = make_task(a_project)
    comment = Comment(task_id=a_task.id, user_id=owner_user.id, content='internal note', is_internal=True)
    db_session.add(comment)
    db_session.commit()

    _login_as_b(client, other_owner_user)
    assert _post(client, f'/tasks/{a_task.id}/comment/delete/{comment.id}').status_code == 404
    assert _post(client, f'/comments/{comment.id}/delete').status_code == 404


def test_task_delete_comment_rejects_comment_from_a_different_tenants_task_even_for_an_admin(
        client, owner_user, other_owner_user, make_user, make_client, make_project, make_task, db_session):
    """Bug-hunt finding: task_delete_comment() anchored its tenant check to
    the URL's task_id, never to the comment's OWN task - so an ADMIN-role
    user who legitimately owns some task in THEIR OWN tenant (B) could pair
    it with a comment_id from a completely different tenant's (A) task and
    delete that comment, because the ADMIN branch skipped ownership
    entirely. This is deliberately NOT the same scenario as the test above
    (which uses a task B has no access to at all, so the earlier ownership
    check already 404s regardless of this bug) - task_id here genuinely
    belongs to B, only comment_id crosses tenants."""
    from models.models import Comment, UserRole

    # Tenant A: a task with a comment on it.
    a_client = make_client(owner_user)
    a_project = make_project(a_client)
    a_task = make_task(a_project)
    a_comment = Comment(task_id=a_task.id, user_id=owner_user.id, content='tenant A internal note', is_internal=True)
    db_session.add(a_comment)
    db_session.commit()
    a_comment_id = a_comment.id

    # Tenant B: an ADMIN-role user who owns their own, unrelated task.
    b_admin = make_user(role=UserRole.ADMIN)
    b_client = make_client(b_admin)
    b_project = make_project(b_client)
    b_task = make_task(b_project)

    _login_as_b(client, b_admin)
    resp = _post(client, f'/tasks/{b_task.id}/comment/delete/{a_comment_id}')
    assert resp.status_code == 404

    # The comment must still exist, untouched, in tenant A.
    assert Comment.query.get(a_comment_id) is not None


# ---------------------------------------------------------------------------
# Quote
# ---------------------------------------------------------------------------

def test_quote_routes_404_for_other_tenant(client, owner_user, other_owner_user, make_client, make_quote):
    a_client = make_client(owner_user)
    a_quote = make_quote(a_client, owner_user)
    _login_as_b(client, other_owner_user)

    assert client.get(f'/quotes/{a_quote.id}').status_code == 404
    assert client.get(f'/quotes/{a_quote.id}/print').status_code == 404
    assert client.get(f'/quotes/{a_quote.id}/edit').status_code == 404
    assert _post(client, f'/quotes/{a_quote.id}/edit', {}).status_code == 404
    assert _post(client, f'/quotes/{a_quote.id}/send').status_code == 404
    assert _post(client, f'/quotes/{a_quote.id}/convert').status_code == 404
    assert _post(client, f'/quotes/{a_quote.id}/delete').status_code == 404


def test_quotes_list_never_shows_other_tenants_quote(client, owner_user, other_owner_user, make_client, make_quote):
    a_client = make_client(owner_user)
    a_quote = make_quote(a_client, owner_user)
    _login_as_b(client, other_owner_user)
    resp = client.get('/quotes')
    assert a_quote.quote_number not in resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Invoice
# ---------------------------------------------------------------------------

def test_invoice_routes_404_for_other_tenant(client, owner_user, other_owner_user, make_client, make_invoice):
    a_client = make_client(owner_user)
    a_invoice = make_invoice(a_client, owner_user)
    _login_as_b(client, other_owner_user)

    assert client.get(f'/invoices/{a_invoice.id}').status_code == 404
    assert client.get(f'/invoices/{a_invoice.id}/print').status_code == 404
    assert client.get(f'/invoices/{a_invoice.id}/edit').status_code == 404
    assert _post(client, f'/invoices/{a_invoice.id}/edit', {}).status_code == 404
    assert _post(client, f'/invoices/{a_invoice.id}/send').status_code == 404
    assert _post(client, f'/invoices/{a_invoice.id}/mark-paid').status_code == 404
    assert _post(client, f'/invoices/{a_invoice.id}/delete').status_code == 404


def test_invoices_list_never_shows_other_tenants_invoice(client, owner_user, other_owner_user, make_client, make_invoice):
    a_client = make_client(owner_user)
    a_invoice = make_invoice(a_client, owner_user)
    _login_as_b(client, other_owner_user)
    resp = client.get('/invoices')
    assert a_invoice.invoice_number not in resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------

def test_contract_routes_404_for_other_tenant(client, owner_user, other_owner_user, make_client, make_contract):
    a_client = make_client(owner_user)
    a_contract = make_contract(a_client, owner_user)
    _login_as_b(client, other_owner_user)

    assert client.get(f'/contracts/{a_contract.id}').status_code == 404
    assert client.get(f'/contracts/{a_contract.id}/print').status_code == 404
    assert client.get(f'/contracts/{a_contract.id}/edit').status_code == 404
    assert _post(client, f'/contracts/{a_contract.id}/edit', {}).status_code == 404
    assert _post(client, f'/contracts/{a_contract.id}/send').status_code == 404
    assert _post(client, f'/contracts/{a_contract.id}/amend').status_code == 404
    assert _post(client, f'/contracts/{a_contract.id}/void').status_code == 404
    assert _post(client, f'/contracts/{a_contract.id}/delete').status_code == 404


def test_contracts_list_never_shows_other_tenants_contract(client, owner_user, other_owner_user, make_client, make_contract):
    a_client = make_client(owner_user)
    a_contract = make_contract(a_client, owner_user)
    _login_as_b(client, other_owner_user)
    resp = client.get('/contracts')
    assert a_contract.contract_number not in resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Team invite / membership (owner-scoped, not a document, but still an
# id-bearing per-tenant resource with the same 404-not-403 contract)
# ---------------------------------------------------------------------------

def test_team_invite_revoke_404s_for_other_tenant(client, owner_user, other_owner_user):
    from models import db
    from models.models import TeamInvite, UserRole
    invite = TeamInvite(inviter_id=owner_user.id, invitee_email='someone@example.com',
                        role=UserRole.DEVELOPER, status='pending')
    db.session.add(invite)
    db.session.commit()

    _login_as_b(client, other_owner_user)
    assert _post(client, f'/team/invite/{invite.id}/revoke').status_code == 404


def test_team_member_remove_404s_for_other_tenant(client, owner_user, other_owner_user, make_team_member):
    member = make_team_member(owner_user)
    from models.models import TeamMembership
    membership = TeamMembership.query.filter_by(account_owner_id=owner_user.id, member_user_id=member.id).first()

    _login_as_b(client, other_owner_user)
    assert _post(client, f'/team/members/{membership.id}/remove').status_code == 404
    assert _post(client, f'/team/members/{membership.id}/role-title', {'role_title': 'Hacked'}).status_code == 404
