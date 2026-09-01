"""
Client-facing portal for the ELIZA Project Management App.

Unlike api/portal.py (public, tokenized links, no login), this is for a
CLIENT-role User who actually logs in - see Client.user_id / User.client_login
in models/models.py. A client login is scoped to exactly one Client record
(client_id), a completely different axis from the owner_id tenant scoping
used everywhere else in this app for staff logins.

The @app.before_request gate in api/index.py is what actually keeps a
CLIENT login out of every other blueprint; the _client_or_redirect() check
repeated in each route below is a defensive second layer in case this
blueprint is ever reached some other way (e.g. a non-CLIENT staff user
typing /my directly - the gate only stops CLIENT roles from leaving here,
it doesn't stop other roles from wandering in).
"""
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user

from models import db
from models.models import Project, Task, Comment, ActivityLog, UserRole, TaskStatus
from utils.analytics import log_event

client_portal_bp = Blueprint('client_portal', __name__, url_prefix='/my')


def _client_or_redirect():
    """Returns (client, None) for a valid CLIENT login, or (None, redirect)
    if this route was reached some other way. Redirects to 'home' rather
    than back into this blueprint - a non-CLIENT/unlinked login has no
    client record to loop back to here."""
    if current_user.role != UserRole.CLIENT or current_user.client_login is None:
        flash('That page is not available for your account.', 'danger')
        return None, redirect(url_for('home'))
    return current_user.client_login, None


@client_portal_bp.route('')
@login_required
def dashboard():
    """Lists the projects belonging to the one Client record this login is
    tied to (client_id scoping - see module docstring)."""
    client, resp = _client_or_redirect()
    if resp:
        return resp
    projects = Project.query.filter_by(client_id=client.id).all()
    return render_template('client_portal/dashboard.html', client=client, projects=projects, now=datetime.now())


@client_portal_bp.route('/projects/<int:project_id>')
@login_required
def project_detail(project_id):
    client, resp = _client_or_redirect()
    if resp:
        return resp
    project = Project.query.get_or_404(project_id)
    # 404 (not 403) so a caller can't distinguish "not yours" from "doesn't
    # exist" and confirm another client's project id - same pattern used for
    # every ownership check elsewhere in this app, just keyed on client_id
    # instead of owner_id.
    if project.client_id != client.id:
        abort(404)
    return render_template('client_portal/project_detail.html', project=project, now=datetime.now())


@client_portal_bp.route('/tasks/<int:task_id>/approve', methods=['POST'])
@login_required
def approve_task(task_id):
    client, resp = _client_or_redirect()
    if resp:
        return resp
    task = Task.query.get_or_404(task_id)
    # Task has no client_id of its own - scoped via its parent project, same
    # shape as the owner_id-via-project check used for staff task routes.
    if task.project.client_id != client.id:
        abort(404)

    if task.status not in (TaskStatus.REVIEW, TaskStatus.COMPLETED):
        flash(f'"{task.title}" is not ready for approval yet.', 'danger')
        return redirect(url_for('client_portal.project_detail', project_id=task.project_id))

    task.client_approved = True
    task.client_approved_at = datetime.utcnow()
    db.session.commit()

    # Log the activity - same paired user_id/owner_id pattern used everywhere
    # else in this codebase, except owner_id here is task.project.owner_id
    # (the STAFF tenant this activity belongs to) rather than
    # current_user.get_owner_id() - a CLIENT login isn't part of a staff
    # tenant, so that method has nothing correct to resolve to here.
    activity = ActivityLog(
        user_id=current_user.id,
        owner_id=task.project.owner_id,
        action_type='client_approved',
        entity_type='task',
        entity_id=task.id,
        description=f'Client approved task: {task.title}'
    )
    db.session.add(activity)
    db.session.commit()
    log_event('task_client_approved', user_id=current_user.id, task_id=task.id)

    flash(f'"{task.title}" has been approved.', 'success')
    return redirect(url_for('client_portal.project_detail', project_id=task.project_id))


@client_portal_bp.route('/tasks/<int:task_id>/comment', methods=['POST'])
@login_required
def add_comment(task_id):
    client, resp = _client_or_redirect()
    if resp:
        return resp
    task = Task.query.get_or_404(task_id)
    if task.project.client_id != client.id:
        abort(404)

    content = request.form.get('content')
    if not content:
        flash('Comment content is required.', 'danger')
        return redirect(url_for('client_portal.project_detail', project_id=task.project_id))

    # Comment.author works with any User regardless of role - a client's
    # comment renders the same way as staff comments on the staff-side task
    # detail page.
    comment = Comment(task_id=task.id, user_id=current_user.id, content=content)
    db.session.add(comment)
    db.session.commit()

    flash('Comment added.', 'success')
    return redirect(url_for('client_portal.project_detail', project_id=task.project_id))
