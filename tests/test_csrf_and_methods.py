"""
Locks in the CSRF + HTTP-method fix shipped this session for the
mutating-but-previously-loosely-guarded routes: client_delete, project_delete,
project_remove_user, task_delete, task_delete_comment, comment_delete,
task_time_start, task_time_stop, quote_delete, invoice_delete.

For each route: a plain GET must be rejected (405 - the route is POST-only),
a POST with no CSRF token must be rejected (400 - Flask-WTF's CSRFProtect),
and a POST with a valid token must actually perform the action (the resource
is genuinely gone/changed afterwards, not just "some page rendered").

MUTATION SANITY CHECK for this file specifically: temporarily change one of
these routes' methods=['POST'] back to methods=['GET', 'POST'] (or drop the
methods kwarg to the Flask default, which includes GET) and re-run - the
corresponding *_get_returns_405 test must fail. See the task report for the
before/after output.
"""
from conftest import get_csrf_token


def _get_token(client):
    """A valid CSRF token for the current session (see
    test_tenant_isolation._post's docstring for why any authenticated
    form page works as the source)."""
    return get_csrf_token(client, '/clients/create')


# ---------------------------------------------------------------------------
# client_delete
# ---------------------------------------------------------------------------

def test_client_delete_get_returns_405(client, owner_user, make_client):
    from conftest import login_as
    login_as(client, owner_user)
    target = make_client(owner_user)
    assert client.get(f'/clients/{target.id}/delete').status_code == 405


def test_client_delete_post_without_csrf_token_returns_400(client, owner_user, make_client):
    from conftest import login_as
    login_as(client, owner_user)
    target = make_client(owner_user)
    resp = client.post(f'/clients/{target.id}/delete')
    assert resp.status_code == 400


def test_client_delete_post_with_valid_token_deletes_the_client(client, owner_user, make_client):
    from conftest import login_as
    from models.models import Client
    login_as(client, owner_user)
    target = make_client(owner_user)
    target_id = target.id
    token = _get_token(client)
    resp = client.post(f'/clients/{target_id}/delete', data={'csrf_token': token})
    assert resp.status_code in (200, 302)
    assert Client.query.get(target_id) is None


# ---------------------------------------------------------------------------
# project_delete
# ---------------------------------------------------------------------------

def test_project_delete_get_returns_405(client, owner_user, make_client, make_project):
    from conftest import login_as
    login_as(client, owner_user)
    c = make_client(owner_user)
    project = make_project(c)
    assert client.get(f'/projects/{project.id}/delete').status_code == 405


def test_project_delete_post_without_csrf_token_returns_400(client, owner_user, make_client, make_project):
    from conftest import login_as
    login_as(client, owner_user)
    c = make_client(owner_user)
    project = make_project(c)
    resp = client.post(f'/projects/{project.id}/delete')
    assert resp.status_code == 400


def test_project_delete_post_with_valid_token_deletes_the_project(client, owner_user, make_client, make_project):
    from conftest import login_as
    from models.models import Project
    login_as(client, owner_user)
    c = make_client(owner_user)
    project = make_project(c)
    project_id = project.id
    token = _get_token(client)
    resp = client.post(f'/projects/{project_id}/delete', data={'csrf_token': token})
    assert resp.status_code in (200, 302)
    assert Project.query.get(project_id) is None


# ---------------------------------------------------------------------------
# project_remove_user
# ---------------------------------------------------------------------------

def _make_project_member(db_session, project, user, role='developer'):
    from models.models import ProjectMember
    member = ProjectMember(project_id=project.id, user_id=user.id, role=role)
    db_session.add(member)
    db_session.commit()
    return member


def test_project_remove_user_get_returns_405(client, owner_user, make_client, make_project, db_session):
    from conftest import login_as
    login_as(client, owner_user)
    c = make_client(owner_user)
    project = make_project(c)
    _make_project_member(db_session, project, owner_user)
    assert client.get(f'/projects/{project.id}/team/remove/{owner_user.id}').status_code == 405


def test_project_remove_user_post_without_csrf_token_returns_400(client, owner_user, make_client, make_project, db_session):
    from conftest import login_as
    login_as(client, owner_user)
    c = make_client(owner_user)
    project = make_project(c)
    _make_project_member(db_session, project, owner_user)
    resp = client.post(f'/projects/{project.id}/team/remove/{owner_user.id}')
    assert resp.status_code == 400


def test_project_remove_user_post_with_valid_token_removes_the_member(client, owner_user, make_client, make_project, db_session):
    from conftest import login_as
    from models.models import ProjectMember
    login_as(client, owner_user)
    c = make_client(owner_user)
    project = make_project(c)
    _make_project_member(db_session, project, owner_user)
    token = _get_token(client)
    resp = client.post(f'/projects/{project.id}/team/remove/{owner_user.id}', data={'csrf_token': token})
    assert resp.status_code in (200, 302)
    assert ProjectMember.query.filter_by(project_id=project.id, user_id=owner_user.id).first() is None


# ---------------------------------------------------------------------------
# task_delete
# ---------------------------------------------------------------------------

def test_task_delete_get_returns_405(client, owner_user, make_client, make_project, make_task):
    from conftest import login_as
    login_as(client, owner_user)
    c = make_client(owner_user)
    project = make_project(c)
    task = make_task(project)
    assert client.get(f'/tasks/{task.id}/delete').status_code == 405


def test_task_delete_post_without_csrf_token_returns_400(client, owner_user, make_client, make_project, make_task):
    from conftest import login_as
    login_as(client, owner_user)
    c = make_client(owner_user)
    project = make_project(c)
    task = make_task(project)
    resp = client.post(f'/tasks/{task.id}/delete')
    assert resp.status_code == 400


def test_task_delete_post_with_valid_token_deletes_the_task(client, owner_user, make_client, make_project, make_task):
    from conftest import login_as
    from models.models import Task
    login_as(client, owner_user)
    c = make_client(owner_user)
    project = make_project(c)
    task = make_task(project)
    task_id = task.id
    token = _get_token(client)
    resp = client.post(f'/tasks/{task_id}/delete', data={'csrf_token': token})
    assert resp.status_code in (200, 302)
    assert Task.query.get(task_id) is None


# ---------------------------------------------------------------------------
# task_delete_comment
# ---------------------------------------------------------------------------

def _make_comment(db_session, task, user, is_internal=True):
    from models.models import Comment
    comment = Comment(task_id=task.id, user_id=user.id, content='a comment', is_internal=is_internal)
    db_session.add(comment)
    db_session.commit()
    return comment


def test_task_delete_comment_get_returns_405(client, owner_user, make_client, make_project, make_task, db_session):
    from conftest import login_as
    login_as(client, owner_user)
    c = make_client(owner_user)
    project = make_project(c)
    task = make_task(project)
    comment = _make_comment(db_session, task, owner_user)
    assert client.get(f'/tasks/{task.id}/comment/delete/{comment.id}').status_code == 405


def test_task_delete_comment_post_without_csrf_token_returns_400(client, owner_user, make_client, make_project, make_task, db_session):
    from conftest import login_as
    login_as(client, owner_user)
    c = make_client(owner_user)
    project = make_project(c)
    task = make_task(project)
    comment = _make_comment(db_session, task, owner_user)
    resp = client.post(f'/tasks/{task.id}/comment/delete/{comment.id}')
    assert resp.status_code == 400


def test_task_delete_comment_post_with_valid_token_deletes_the_comment(client, owner_user, make_client, make_project, make_task, db_session):
    from conftest import login_as
    from models.models import Comment
    login_as(client, owner_user)
    c = make_client(owner_user)
    project = make_project(c)
    task = make_task(project)
    comment = _make_comment(db_session, task, owner_user)
    comment_id = comment.id
    token = _get_token(client)
    resp = client.post(f'/tasks/{task.id}/comment/delete/{comment_id}', data={'csrf_token': token})
    assert resp.status_code in (200, 302)
    assert Comment.query.get(comment_id) is None


# ---------------------------------------------------------------------------
# comment_delete (the direct /comments/<id>/delete route)
# ---------------------------------------------------------------------------

def test_comment_delete_get_returns_405(client, owner_user, make_client, make_project, make_task, db_session):
    from conftest import login_as
    login_as(client, owner_user)
    c = make_client(owner_user)
    project = make_project(c)
    task = make_task(project)
    comment = _make_comment(db_session, task, owner_user)
    assert client.get(f'/comments/{comment.id}/delete').status_code == 405


def test_comment_delete_post_without_csrf_token_returns_400(client, owner_user, make_client, make_project, make_task, db_session):
    from conftest import login_as
    login_as(client, owner_user)
    c = make_client(owner_user)
    project = make_project(c)
    task = make_task(project)
    comment = _make_comment(db_session, task, owner_user)
    resp = client.post(f'/comments/{comment.id}/delete')
    assert resp.status_code == 400


def test_comment_delete_post_with_valid_token_deletes_the_comment(client, owner_user, make_client, make_project, make_task, db_session):
    from conftest import login_as
    from models.models import Comment
    login_as(client, owner_user)
    c = make_client(owner_user)
    project = make_project(c)
    task = make_task(project)
    comment = _make_comment(db_session, task, owner_user)
    comment_id = comment.id
    token = _get_token(client)
    resp = client.post(f'/comments/{comment_id}/delete', data={'csrf_token': token})
    assert resp.status_code in (200, 302)
    assert Comment.query.get(comment_id) is None


# ---------------------------------------------------------------------------
# task_time_start
# ---------------------------------------------------------------------------

def test_task_time_start_get_returns_405(client, owner_user, make_client, make_project, make_task):
    from conftest import login_as
    login_as(client, owner_user)
    c = make_client(owner_user)
    project = make_project(c)
    task = make_task(project)
    assert client.get(f'/tasks/{task.id}/time/start').status_code == 405


def test_task_time_start_post_without_csrf_token_returns_400(client, owner_user, make_client, make_project, make_task):
    from conftest import login_as
    login_as(client, owner_user)
    c = make_client(owner_user)
    project = make_project(c)
    task = make_task(project)
    resp = client.post(f'/tasks/{task.id}/time/start')
    assert resp.status_code == 400


def test_task_time_start_post_with_valid_token_creates_active_time_entry(client, owner_user, make_client, make_project, make_task):
    from conftest import login_as
    from models.models import TimeEntry
    login_as(client, owner_user)
    c = make_client(owner_user)
    project = make_project(c)
    task = make_task(project)
    assert TimeEntry.query.filter_by(task_id=task.id, user_id=owner_user.id, end_time=None).first() is None
    token = _get_token(client)
    resp = client.post(f'/tasks/{task.id}/time/start', data={'csrf_token': token})
    assert resp.status_code in (200, 302)
    active = TimeEntry.query.filter_by(task_id=task.id, user_id=owner_user.id, end_time=None).first()
    assert active is not None


# ---------------------------------------------------------------------------
# task_time_stop
# ---------------------------------------------------------------------------

def _make_active_time_entry(db_session, task, user):
    from datetime import datetime, timedelta
    from models.models import TimeEntry
    entry = TimeEntry(task_id=task.id, user_id=user.id,
                       start_time=datetime.now() - timedelta(hours=1),
                       description='in progress')
    db_session.add(entry)
    db_session.commit()
    return entry


def test_task_time_stop_get_returns_405(client, owner_user, make_client, make_project, make_task, db_session):
    from conftest import login_as
    login_as(client, owner_user)
    c = make_client(owner_user)
    project = make_project(c)
    task = make_task(project)
    _make_active_time_entry(db_session, task, owner_user)
    assert client.get(f'/tasks/{task.id}/time/stop').status_code == 405


def test_task_time_stop_post_without_csrf_token_returns_400(client, owner_user, make_client, make_project, make_task, db_session):
    from conftest import login_as
    login_as(client, owner_user)
    c = make_client(owner_user)
    project = make_project(c)
    task = make_task(project)
    _make_active_time_entry(db_session, task, owner_user)
    resp = client.post(f'/tasks/{task.id}/time/stop')
    assert resp.status_code == 400


def test_task_time_stop_post_with_valid_token_stops_the_timer(client, owner_user, make_client, make_project, make_task, db_session):
    from conftest import login_as
    from models.models import TimeEntry
    login_as(client, owner_user)
    c = make_client(owner_user)
    project = make_project(c)
    task = make_task(project)
    entry = _make_active_time_entry(db_session, task, owner_user)
    entry_id = entry.id
    token = _get_token(client)
    resp = client.post(f'/tasks/{task.id}/time/stop', data={'csrf_token': token})
    assert resp.status_code in (200, 302)
    refreshed = TimeEntry.query.get(entry_id)
    assert refreshed.end_time is not None
    assert refreshed.duration is not None and refreshed.duration > 0


# ---------------------------------------------------------------------------
# quote_delete
# ---------------------------------------------------------------------------

def test_quote_delete_get_returns_405(client, owner_user, make_client, make_quote):
    from conftest import login_as
    login_as(client, owner_user)
    c = make_client(owner_user)
    quote = make_quote(c, owner_user)
    assert client.get(f'/quotes/{quote.id}/delete').status_code == 405


def test_quote_delete_post_without_csrf_token_returns_400(client, owner_user, make_client, make_quote):
    from conftest import login_as
    login_as(client, owner_user)
    c = make_client(owner_user)
    quote = make_quote(c, owner_user)
    resp = client.post(f'/quotes/{quote.id}/delete')
    assert resp.status_code == 400


def test_quote_delete_post_with_valid_token_deletes_the_quote(client, owner_user, make_client, make_quote):
    from conftest import login_as
    from models.billing import Quote
    login_as(client, owner_user)
    c = make_client(owner_user)
    quote = make_quote(c, owner_user)
    quote_id = quote.id
    token = _get_token(client)
    resp = client.post(f'/quotes/{quote_id}/delete', data={'csrf_token': token})
    assert resp.status_code in (200, 302)
    assert Quote.query.get(quote_id) is None


# ---------------------------------------------------------------------------
# invoice_delete
# ---------------------------------------------------------------------------

def test_invoice_delete_get_returns_405(client, owner_user, make_client, make_invoice):
    from conftest import login_as
    login_as(client, owner_user)
    c = make_client(owner_user)
    invoice = make_invoice(c, owner_user)
    assert client.get(f'/invoices/{invoice.id}/delete').status_code == 405


def test_invoice_delete_post_without_csrf_token_returns_400(client, owner_user, make_client, make_invoice):
    from conftest import login_as
    login_as(client, owner_user)
    c = make_client(owner_user)
    invoice = make_invoice(c, owner_user)
    resp = client.post(f'/invoices/{invoice.id}/delete')
    assert resp.status_code == 400


def test_invoice_delete_post_with_valid_token_deletes_the_invoice(client, owner_user, make_client, make_invoice):
    from conftest import login_as
    from models.billing import Invoice
    login_as(client, owner_user)
    c = make_client(owner_user)
    invoice = make_invoice(c, owner_user)
    invoice_id = invoice.id
    token = _get_token(client)
    resp = client.post(f'/invoices/{invoice_id}/delete', data={'csrf_token': token})
    assert resp.status_code in (200, 302)
    assert Invoice.query.get(invoice_id) is None
