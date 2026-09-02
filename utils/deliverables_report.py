"""
Deliverables report builder for the ELIZA Project Management App.

Compiles an executive-ready view of what has been delivered, what is
overdue, and who was responsible, for either a single project or every
project under a client (see api/index.py's report_deliverables() route).

Every query in here is scoped by the owner_id the caller passes in - this
module trusts that project_id/client_id already belong to that owner (the
route is responsible for that ownership check, exactly like every other
lookup-by-id route in api/index.py), but every child query (tasks,
comments, time entries, activity log) is still scoped by owner_id/parent id
rather than fetched by bare foreign key, so a bug in the caller can't turn
into a cross-tenant leak here too.
"""
from datetime import datetime

from sqlalchemy import or_, and_

from models.models import Project, Client, TaskStatus, ProjectStatus, ActivityLog


def _condense_text(text, limit=240):
    """Collapse a comment's raw newlines/whitespace into one line and cap its
    length - this report is a stakeholder-facing summary, not the task
    detail page, so a comment thread here reads as a condensed line per
    comment rather than the multi-paragraph nl2br|safe rendering used there.
    """
    if not text:
        return ''
    collapsed = ' '.join(text.split())
    if len(collapsed) > limit:
        return collapsed[:limit].rstrip() + '...'
    return collapsed


def _late_status(task, today):
    """Returns (is_late, days).

    days is how many days late a completed task was, or how many days
    overdue an incomplete task currently is; None when the task isn't late.
    A task with no due_date can never be late - there's nothing to measure
    lateness against.
    """
    if not task.due_date:
        return False, None
    if task.completed_at:
        days = (task.completed_at.date() - task.due_date).days
        return (True, days) if days > 0 else (False, None)
    if task.due_date < today:
        return True, (today - task.due_date).days
    return False, None


def _accountability_sentence(task, assignee_name, days_late):
    """A short plain-language sentence naming who was responsible - always
    names someone or explicitly says "unassigned", never silent about it.
    """
    day_word = 'day' if days_late == 1 else 'days'
    if task.completed_at:
        if assignee_name:
            return f'{assignee_name} completed "{task.title}" {days_late} {day_word} late.'
        return f'"{task.title}" was unassigned and was completed {days_late} {day_word} late.'
    if assignee_name:
        return f'{assignee_name} has not completed "{task.title}", now {days_late} {day_word} overdue.'
    return f'"{task.title}" is unassigned and is now {days_late} {day_word} overdue.'


def _build_task_report(task, today):
    assignee_name = task.assigned_to.get_full_name() if task.assignee_id else None
    is_late, days_late = _late_status(task, today)
    accountability_sentence = _accountability_sentence(task, assignee_name, days_late) if is_late else None

    # task.comments is already the loaded relationship (Comment backref on
    # Task) - sorting here in Python avoids a second query for something
    # already in memory.
    comments = sorted(task.comments, key=lambda c: c.created_at)

    return {
        'task': task,
        'title': task.title,
        'assignee_id': task.assignee_id,
        'assignee_name': assignee_name or 'Unassigned',
        'due_date': task.due_date,
        'completed_at': task.completed_at,
        'status': task.status,
        'client_approved': task.client_approved,
        'client_approved_at': task.client_approved_at,
        'is_late': is_late,
        'accountability_sentence': accountability_sentence,
        'comments': [
            {
                'author_name': c.author.get_full_name(),
                'content': _condense_text(c.content),
                'created_at': c.created_at,
            }
            for c in comments
        ],
    }


def _build_activity_feed(project, task_ids, owner_id):
    """ActivityLog has no clean join back to a project - entity_type/
    entity_id is polymorphic (see models/models.py). Matching entity_type
    'project' against this project's id, or entity_type 'task' against this
    project's own task ids, is the only way to scope it that doesn't risk
    pulling in another entity's log rows - plus the owner_id check below as
    a second, independent layer of tenant isolation.
    """
    conditions = [and_(ActivityLog.entity_type == 'project', ActivityLog.entity_id == project.id)]
    if task_ids:
        conditions.append(and_(ActivityLog.entity_type == 'task', ActivityLog.entity_id.in_(task_ids)))

    logs = ActivityLog.query.filter(
        ActivityLog.owner_id == owner_id,
        or_(*conditions)
    ).order_by(ActivityLog.created_at.asc()).all()

    return [
        {
            'actor_name': log.user.get_full_name(),
            'description': log.description,
            'action_type': log.action_type,
            'created_at': log.created_at,
        }
        for log in logs
    ]


def _build_project_report(project, owner_id, today):
    # Sorted by due_date (undated tasks last) so the deliverables list reads
    # as a timeline, not creation order.
    tasks = sorted(project.tasks, key=lambda t: (t.due_date is None, t.due_date))
    task_reports = [_build_task_report(t, today) for t in tasks]

    task_count = len(tasks)
    completed_count = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED)
    # Mirrors calculate_project_progress() in api/index.py - not imported
    # from there to avoid a circular import (api/index.py imports this
    # module at startup), same tradeoff as
    # generate_client_unsubscribe_token in models/models.py.
    percent_complete = int((completed_count / task_count) * 100) if task_count else 0

    is_overdue = bool(
        project.end_date and project.end_date < today and project.status != ProjectStatus.COMPLETED
    )

    # Sum TimeEntry.duration directly (not Task.actual_hours) per spec -
    # duration is hours logged via the time tracking flow, actual_hours is a
    # separate manually-set estimate field.
    total_hours = 0.0
    for task in tasks:
        for entry in task.time_entries:
            if entry.duration:
                total_hours += entry.duration

    task_ids = [t.id for t in tasks]

    return {
        'project': project,
        'title': project.title,
        'client_name': project.client.name if project.client else None,
        'status': project.status,
        'start_date': project.start_date,
        'end_date': project.end_date,
        'percent_complete': percent_complete,
        'is_overdue': is_overdue,
        'tasks': task_reports,
        'total_hours': round(total_hours, 1),
        'activity_feed': _build_activity_feed(project, task_ids, owner_id),
    }


def _build_summary(project_reports):
    """Top-level roll-up across every project in a client-scope report.

    recurring_late_assignees is keyed by assignee_id (not display name) so
    two different people who happen to share a full name are never merged
    into one count. Unassigned late tasks are excluded here - there's no
    "who" to flag as recurring, that's the per-task accountability
    sentence's job instead.
    """
    total_projects = len(project_reports)
    overdue_projects = sum(1 for p in project_reports if p['is_overdue'])
    total_late_tasks = sum(1 for p in project_reports for t in p['tasks'] if t['is_late'])

    by_assignee = {}
    for p in project_reports:
        for t in p['tasks']:
            if t['is_late'] and t['assignee_id']:
                entry = by_assignee.setdefault(t['assignee_id'], {'name': t['assignee_name'], 'count': 0})
                entry['count'] += 1

    recurring = sorted(
        (v for v in by_assignee.values() if v['count'] >= 2),
        key=lambda v: -v['count']
    )

    return {
        'total_projects': total_projects,
        'overdue_projects': overdue_projects,
        'total_late_tasks': total_late_tasks,
        'recurring_late_assignees': recurring,
    }


def build_deliverables_report(owner_id, project_id=None, client_id=None):
    """Build the deliverables report dict for one project or one client's
    projects. Exactly one of project_id/client_id must be given.

    Assumes project_id/client_id already belongs to owner_id - the caller
    (the route) is responsible for that ownership check, same contract as
    every other lookup-by-id route in this codebase.
    """
    if (project_id is None) == (client_id is None):
        raise ValueError('build_deliverables_report requires exactly one of project_id or client_id')

    today = datetime.now().date()

    if project_id is not None:
        scope = 'project'
        project = Project.query.get(project_id)
        client = project.client
        projects = [project]
    else:
        scope = 'client'
        project = None
        client = Client.query.get(client_id)
        # Scoped by owner_id too, not just client_id - belt and suspenders
        # with the route's own ownership check, same layered style as the
        # rest of this codebase's owner_id-scoped queries.
        projects = Project.query.filter_by(client_id=client.id, owner_id=owner_id).order_by(Project.start_date).all()

    project_reports = [_build_project_report(p, owner_id, today) for p in projects]

    return {
        'scope': scope,
        'client': client,
        'project': project,
        'projects': project_reports,
        'summary': _build_summary(project_reports) if scope == 'client' else None,
        'generated_at': datetime.now(),
    }
