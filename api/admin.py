from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, abort
from flask_login import login_required, current_user
from sqlalchemy import func
from models.models import User, Client, Project, Task, UserRole, ProjectStatus, TaskStatus, AnalyticsEvent, ActivityLog
from models.subscription import Subscription, SubscriptionPlan
from models.billing import AccountStatement
from forms import UserForm, SubscriptionPlanForm, SubscriptionForm, EmailSettingsForm, PesapalSettingsForm, AppSettingsForm, StatementForm
from utils.account_statement import build_account_statement
from models import db
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash
import json
import os

admin_bp = Blueprint('admin', __name__)

# Admin access decorator
def admin_required(f):
    def decorated_function(*args, **kwargs):
        # current_user.role is a UserRole enum member (see models/models.py),
        # never the string 'admin' - comparing it to a string is always
        # True, so every user (including real admins) used to get bounced.
        if not current_user.is_authenticated or current_user.role != UserRole.ADMIN:
            flash('You do not have permission to access this page.', 'danger')
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function

@admin_bp.route('/dashboard')
@login_required
@admin_required
def dashboard():
    # Count statistics
    user_count = User.query.count()
    client_count = Client.query.count()
    active_project_count = Project.query.filter_by(status=ProjectStatus.IN_PROGRESS).count()

    # Get task counts by status
    todo_count = Task.query.filter_by(status=TaskStatus.TODO).count()
    in_progress_count = Task.query.filter_by(status=TaskStatus.IN_PROGRESS).count()
    review_count = Task.query.filter_by(status=TaskStatus.REVIEW).count()
    completed_count = Task.query.filter_by(status=TaskStatus.COMPLETED).count()
    
    # Get subscription counts by plan.
    # Subscription has no `status` column (only is_active/is_trial booleans -
    # see models/subscription.py); the old `Subscription.status == 'active'`
    # filters referenced a column that doesn't exist and crashed every call.
    #
    # Subscription now carries TWO FKs to subscription_plans (plan_id and,
    # since the plan-upgrade feature, pending_plan_id for a scheduled
    # downgrade) - a bare .join(SubscriptionPlan) can no longer infer which
    # one to use and raises AmbiguousForeignKeysError on every call. These
    # counts are about the plan a subscription is CURRENTLY on, so the join
    # is explicit on plan_id, never pending_plan_id.
    basic_count = Subscription.query.join(
        SubscriptionPlan, Subscription.plan_id == SubscriptionPlan.id
    ).filter(
        SubscriptionPlan.name == 'Basic',
        Subscription.is_active == True
    ).count()

    professional_count = Subscription.query.join(
        SubscriptionPlan, Subscription.plan_id == SubscriptionPlan.id
    ).filter(
        SubscriptionPlan.name == 'Professional',
        Subscription.is_active == True
    ).count()

    enterprise_count = Subscription.query.join(
        SubscriptionPlan, Subscription.plan_id == SubscriptionPlan.id
    ).filter(
        SubscriptionPlan.name == 'Enterprise',
        Subscription.is_active == True
    ).count()

    trial_count = Subscription.query.filter_by(is_trial=True, is_active=True).count()
    
    # Get recent activities (placeholder - would need an Activity model)
    recent_activities = []
    
    return render_template('admin/dashboard.html',
                          user_count=user_count,
                          client_count=client_count,
                          active_project_count=active_project_count,
                          todo_count=todo_count,
                          in_progress_count=in_progress_count,
                          review_count=review_count,
                          completed_count=completed_count,
                          basic_count=basic_count,
                          professional_count=professional_count,
                          enterprise_count=enterprise_count,
                          trial_count=trial_count,
                          recent_activities=recent_activities)

@admin_bp.route('/analytics')
@login_required
@admin_required
def analytics():
    """Platform-wide product-usage analytics (see models.models.AnalyticsEvent
    and utils/analytics.py's log_event()) - deliberately NOT owner_id-scoped,
    same as the rest of this admin blueprint: the point is comparing behavior
    across every tenant to see where trial users drop off and what correlates
    with converting to a paying subscriber.
    """
    # Signups per day for the last 30 days. func.date() works on both
    # Postgres and the sqlite dev fallback (see api/index.py's DATABASE_URL
    # handling), unlike a Postgres-only date_trunc.
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    signup_rows = db.session.query(
        func.date(AnalyticsEvent.created_at).label('day'),
        func.count(AnalyticsEvent.id).label('count')
    ).filter(
        AnalyticsEvent.event_type == 'user_registered',
        AnalyticsEvent.created_at >= thirty_days_ago
    ).group_by('day').order_by('day').all()
    signup_labels = [str(row.day) for row in signup_rows]
    signup_counts = [row.count for row in signup_rows]

    # Simple conversion funnel. Kept as plain Python set intersections rather
    # than a fancier SQL join - this table is small enough that it doesn't
    # need to be optimized, and sets make "also has" trivial to read.
    #
    # Events are logged under the raw acting user_id (correct for audit
    # purposes - see log_event() callers, do not change that), but a team
    # member's own signup is logged as 'team_invite_accepted', never
    # 'user_registered' (see api/index.py's accept_invite()), so their
    # user_id would never land in registered_ids - meaning a
    # client_created/invoice_sent event THEY performed (logged under their
    # own user_id) would never count as progress for the account, even
    # though the account genuinely progressed. Resolve every user_id to its
    # account owner id (User.get_owner_id() - a no-op for owners themselves)
    # once up front, and reuse that map for every stage below, rather than
    # re-resolving the same user_id redundantly per stage.
    all_event_user_ids = {
        row.user_id for row in db.session.query(AnalyticsEvent.user_id).filter(
            AnalyticsEvent.user_id.isnot(None)
        ).distinct().all()
    }
    owner_id_by_user_id = {}
    for uid in all_event_user_ids:
        user = User.query.get(uid)
        owner_id_by_user_id[uid] = user.get_owner_id() if user else uid

    def _user_ids_for(event_type):
        rows = db.session.query(AnalyticsEvent.user_id).filter(
            AnalyticsEvent.event_type == event_type,
            AnalyticsEvent.user_id.isnot(None)
        ).distinct().all()
        return {owner_id_by_user_id.get(row.user_id, row.user_id) for row in rows}

    registered_ids = _user_ids_for('user_registered')
    with_client = registered_ids & _user_ids_for('client_created')
    with_invoice_sent = with_client & _user_ids_for('invoice_sent')
    with_conversion = with_invoice_sent & _user_ids_for('subscription_converted')

    funnel = [
        {'label': 'Registered', 'count': len(registered_ids)},
        {'label': 'Created a client', 'count': len(with_client)},
        {'label': 'Sent an invoice', 'count': len(with_invoice_sent)},
        {'label': 'Converted to paid', 'count': len(with_conversion)},
    ]

    # Total counts per event_type overall.
    event_type_counts = db.session.query(
        AnalyticsEvent.event_type, func.count(AnalyticsEvent.id)
    ).group_by(AnalyticsEvent.event_type).order_by(func.count(AnalyticsEvent.id).desc()).all()

    return render_template('admin/analytics.html',
                          signup_labels=signup_labels,
                          signup_counts=signup_counts,
                          funnel=funnel,
                          event_type_counts=event_type_counts)

@admin_bp.route('/users')
@login_required
@admin_required
def users():
    users = User.query.all()
    
    # Create forms for the modals
    add_form = UserForm()
    edit_form = UserForm()
    delete_form = UserForm()
    
    return render_template('admin/users.html', 
                          users=users, 
                          add_form=add_form, 
                          edit_form=edit_form, 
                          delete_form=delete_form)

@admin_bp.route('/users/add', methods=['POST'])
@login_required
@admin_required
def add_user():
    form = UserForm()
    
    if form.validate_on_submit():
        # Check if username or email already exists
        existing_user = User.query.filter(
            (User.username == form.username.data) | 
            (User.email == form.email.data)
        ).first()
        
        if existing_user:
            flash('Username or email already exists.', 'danger')
            return redirect(url_for('admin.users'))
        
        if not form.password.data:
            flash('Password is required when creating a user.', 'danger')
            return redirect(url_for('admin.users'))

        # Create new user
        new_user = User(
            first_name=form.first_name.data,
            last_name=form.last_name.data,
            username=form.username.data,
            email=form.email.data,
            phone=form.phone.data,
            password_hash=generate_password_hash(form.password.data),
            role=UserRole[form.role.data],
        )
        
        db.session.add(new_user)
        db.session.commit()
        
        flash(f'User {new_user.username} created.', 'success')
    else:
        for field, errors in form.errors.items():
            for error in errors:
                flash(f'{field}: {error}', 'danger')
    
    return redirect(url_for('admin.users'))

@admin_bp.route('/users/edit', methods=['POST'])
@login_required
@admin_required
def edit_user():
    form = UserForm()
    
    if form.validate_on_submit():
        user = User.query.get_or_404(form.user_id.data)
        
        # Check if username or email already exists for another user
        existing_user = User.query.filter(
            ((User.username == form.username.data) | 
            (User.email == form.email.data)) &
            (User.id != user.id)
        ).first()
        
        if existing_user:
            flash('Username or email already exists for another user.', 'danger')
            return redirect(url_for('admin.users'))
        
        # Update user details
        user.first_name = form.first_name.data
        user.last_name = form.last_name.data
        user.username = form.username.data
        user.email = form.email.data
        user.phone = form.phone.data
        user.role = UserRole[form.role.data]

        # Update password if provided
        if form.new_password.data:
            user.password_hash = generate_password_hash(form.new_password.data)
        
        db.session.commit()
        
        flash(f'User {user.username} saved.', 'success')
    else:
        for field, errors in form.errors.items():
            for error in errors:
                flash(f'{field}: {error}', 'danger')
    
    return redirect(url_for('admin.users'))

@admin_bp.route('/users/delete', methods=['POST'])
@login_required
@admin_required
def delete_user():
    form = UserForm()
    
    if form.validate_on_submit():
        user = User.query.get_or_404(form.user_id.data)
        
        # Prevent deleting the current user
        if user.id == current_user.id:
            flash('You cannot delete your own account.', 'danger')
            return redirect(url_for('admin.users'))

        try:
            # Delete the user
            db.session.delete(user)
            db.session.commit()
            flash(f'User {user.username} deleted.', 'success')
        except Exception:
            # ActivityLog.user_id, Comment.user_id, TimeEntry.user_id,
            # TeamMembership.account_owner_id/member_user_id,
            # Subscription.user_id, Quote/Invoice/Contract.created_by_id and
            # TeamInvite/ClientInvite.inviter_id are all NOT NULL FKs to
            # users.id with no cascade (ORM or DB) - deleting any user who
            # has ever done anything in the app raised an unhandled
            # IntegrityError here (a raw 500). Deciding what SHOULD happen
            # to that history (reassign, block, soft-delete...) is a real
            # product decision, not something to guess at in a bug-hunt
            # pass - so this at minimum degrades to a clean rollback and a
            # friendly message instead of crashing.
            db.session.rollback()
            flash(f'Could not delete {user.username}: they still have related records '
                  f'(activity, comments, time entries, invoices, or a team membership).', 'danger')
    
    return redirect(url_for('admin.users'))

@admin_bp.route('/subscriptions')
@login_required
@admin_required
def subscriptions():
    plans = SubscriptionPlan.query.all()
    subscriptions = Subscription.query.all()
    
    # Create forms for the modals
    add_plan_form = SubscriptionPlanForm()
    edit_plan_form = SubscriptionPlanForm()
    delete_plan_form = SubscriptionPlanForm()
    
    edit_subscription_form = SubscriptionForm()
    cancel_subscription_form = SubscriptionForm()

    # A subscription belongs to a User (an ELIZA account holder), not a
    # Client (that user's own customer) - populate accordingly.
    users = User.query.all()
    edit_subscription_form.user_id.choices = [(u.id, f"{u.get_full_name()} ({u.email})") for u in users]
    edit_subscription_form.plan_id.choices = [(p.id, p.name) for p in plans]
    
    return render_template('admin/subscriptions.html',
                          plans=plans,
                          subscriptions=subscriptions,
                          add_plan_form=add_plan_form,
                          edit_plan_form=edit_plan_form,
                          delete_plan_form=delete_plan_form,
                          edit_subscription_form=edit_subscription_form,
                          cancel_subscription_form=cancel_subscription_form)

@admin_bp.route('/subscriptions/plans/add', methods=['POST'])
@login_required
@admin_required
def add_plan():
    form = SubscriptionPlanForm()
    
    if form.validate_on_submit():
        # Create new subscription plan
        new_plan = SubscriptionPlan(
            name=form.name.data,
            description=form.description.data,
            price_monthly=form.price_monthly.data,
            price_yearly=form.price_yearly.data,
            max_projects=form.max_projects.data or 0,
            max_users=form.max_users.data or 0,
            max_clients=form.max_clients.data or 0,
            features=form.features.data,
            is_active=form.is_active.data,
        )

        db.session.add(new_plan)
        db.session.commit()
        
        flash(f'Plan {new_plan.name} created.', 'success')
    else:
        for field, errors in form.errors.items():
            for error in errors:
                flash(f'{field}: {error}', 'danger')
    
    return redirect(url_for('admin.subscriptions'))

@admin_bp.route('/subscriptions/plans/edit', methods=['POST'])
@login_required
@admin_required
def edit_plan():
    form = SubscriptionPlanForm()
    
    if form.validate_on_submit():
        plan = SubscriptionPlan.query.get_or_404(form.plan_id.data)

        # Update plan details
        plan.name = form.name.data
        plan.description = form.description.data
        plan.price_monthly = form.price_monthly.data
        plan.price_yearly = form.price_yearly.data
        plan.max_projects = form.max_projects.data or 0
        plan.max_users = form.max_users.data or 0
        plan.max_clients = form.max_clients.data or 0
        plan.features = form.features.data
        plan.is_active = form.is_active.data

        db.session.commit()
        
        flash(f'Plan {plan.name} saved.', 'success')
    else:
        for field, errors in form.errors.items():
            for error in errors:
                flash(f'{field}: {error}', 'danger')
    
    return redirect(url_for('admin.subscriptions'))

@admin_bp.route('/subscriptions/plans/delete', methods=['POST'])
@login_required
@admin_required
def delete_plan():
    form = SubscriptionPlanForm()
    
    if form.validate_on_submit():
        plan = SubscriptionPlan.query.get_or_404(form.plan_id.data)

        # subscriptions.plan_id AND subscriptions.pending_plan_id are both
        # NOT-NULL-safe FKs to this table with no cascade (ORM or DB) - a
        # plan that has ever had a customer who later upgraded, downgraded,
        # or churned still has an INACTIVE Subscription row pointing at it
        # (subscribe() sets is_active=False rather than deleting on a plan
        # switch), and a pending_plan_id reference can exist independent of
        # is_active entirely. Counting only is_active=True subscriptions
        # missed both, so this guard would pass and the delete below would
        # still hit Postgres's own FK constraint - an unhandled 500. Count
        # every subscription that references this plan at all, active or
        # historical, via either column.
        referencing_subscriptions = Subscription.query.filter(
            db.or_(Subscription.plan_id == plan.id, Subscription.pending_plan_id == plan.id)
        ).count()

        if referencing_subscriptions > 0:
            flash(f'Cannot delete plan {plan.name}: {referencing_subscriptions} subscription(s) '
                  f'(active or historical) still reference it.', 'danger')
            return redirect(url_for('admin.subscriptions'))

        try:
            # Delete the plan
            db.session.delete(plan)
            db.session.commit()
            flash(f'Plan {plan.name} deleted.', 'success')
        except Exception:
            db.session.rollback()
            flash(f'Could not delete plan {plan.name} because it still has related records.', 'danger')
    
    return redirect(url_for('admin.subscriptions'))

@admin_bp.route('/subscriptions/edit', methods=['POST'])
@login_required
@admin_required
def edit_subscription():
    form = SubscriptionForm()

    # Populate the choices for validation
    users = User.query.all()
    plans = SubscriptionPlan.query.all()
    form.user_id.choices = [(u.id, f"{u.get_full_name()} ({u.email})") for u in users]
    form.plan_id.choices = [(p.id, p.name) for p in plans]

    if form.validate_on_submit():
        subscription = Subscription.query.get_or_404(form.subscription_id.data)

        # Update subscription details. There's no `status` column on
        # Subscription - the form's status choice maps to is_active/is_trial.
        subscription.user_id = form.user_id.data
        subscription.plan_id = form.plan_id.data
        subscription.start_date = form.start_date.data
        subscription.end_date = form.end_date.data
        subscription.billing_cycle = form.billing_cycle.data
        subscription.is_active = form.status.data in ('active', 'trial')
        subscription.is_trial = form.status.data == 'trial'

        db.session.commit()
        
        flash('Subscription saved.', 'success')
    else:
        for field, errors in form.errors.items():
            for error in errors:
                flash(f'{field}: {error}', 'danger')
    
    return redirect(url_for('admin.subscriptions'))

@admin_bp.route('/subscriptions/cancel', methods=['POST'])
@login_required
@admin_required
def cancel_subscription():
    # The cancel modal only submits subscription_id (+ a reason we have
    # nowhere to store - Subscription has no cancellation_reason column) -
    # not the full user/plan/dates SubscriptionForm requires, so reusing
    # that form here made validate_on_submit() always fail and this action
    # silently do nothing. CSRF is still enforced globally by Flask-WTF.
    subscription_id = request.form.get('subscription_id', type=int)
    if not subscription_id:
        flash('Invalid cancellation request.', 'danger')
        return redirect(url_for('admin.subscriptions'))

    subscription = Subscription.query.get_or_404(subscription_id)

    # Subscription has no status/cancellation_reason/cancelled_at columns -
    # cancelling just deactivates it.
    subscription.is_active = False
    subscription.is_trial = False

    db.session.commit()

    # A Subscription belongs to a User, not a Client - see SubscriptionForm's docstring.
    subscriber = User.query.get(subscription.user_id)
    plan = SubscriptionPlan.query.get(subscription.plan_id)

    flash(f'Subscription for {subscriber.get_full_name()} to the {plan.name} plan has been cancelled.', 'success')
    return redirect(url_for('admin.subscriptions'))

@admin_bp.route('/system')
@login_required
@admin_required
def system():
    email_form = EmailSettingsForm()
    pesapal_form = PesapalSettingsForm()
    app_form = AppSettingsForm()

    email_form.mail_server.data = os.environ.get('MAIL_SERVER', '')
    email_form.mail_port.data = int(os.environ.get('MAIL_PORT', 587))
    email_form.mail_username.data = os.environ.get('MAIL_USERNAME', '')
    email_form.mail_use_tls.data = os.environ.get('MAIL_USE_TLS', 'True') == 'True'
    email_form.mail_use_ssl.data = os.environ.get('MAIL_USE_SSL', 'False') == 'True'
    email_form.mail_default_sender.data = os.environ.get('MAIL_DEFAULT_SENDER', '')

    pesapal_form.pesapal_consumer_key.data = os.environ.get('PESAPAL_CONSUMER_KEY', '')
    pesapal_form.pesapal_consumer_secret.data = os.environ.get('PESAPAL_CONSUMER_SECRET', '')
    pesapal_form.pesapal_ipn_id.data = os.environ.get('PESAPAL_IPN_ID', '')
    pesapal_form.pesapal_live_mode.data = os.environ.get('PESAPAL_LIVE_MODE', 'False') == 'True'

    app_form.app_name.data = os.environ.get('APP_NAME', 'ELIZA Project Management')
    app_form.company_name.data = os.environ.get('COMPANY_NAME', 'Your Company')
    app_form.company_logo.data = os.environ.get('COMPANY_LOGO', '')
    app_form.timezone.data = os.environ.get('TIMEZONE', 'Africa/Nairobi')
    app_form.date_format.data = os.environ.get('DATE_FORMAT', '%Y-%m-%d')

    return render_template('admin/system.html', email_form=email_form, pesapal_form=pesapal_form, app_form=app_form)


# These settings are environment-variable-driven (see config.py / api/index.py)
# and there's no settings table to persist edits to - these routes exist so the
# forms don't 500, but they're honest about not persisting across a restart
# rather than silently discarding the admin's input or faking success.
_SETTINGS_NOTE = 'saved for this running process only - update your .env (or hosting provider env vars) and restart to make this permanent.'


@admin_bp.route('/system/email', methods=['POST'])
@login_required
@admin_required
def update_email_settings():
    form = EmailSettingsForm()
    if form.validate_on_submit():
        # Flask-Mail reads current_app.config (set once at startup from
        # os.environ) at send time, not os.environ directly - updating only
        # the environment variable would silently do nothing for the rest
        # of this process, so both must be updated for the change to apply.
        os.environ['MAIL_SERVER'] = form.mail_server.data
        os.environ['MAIL_PORT'] = str(form.mail_port.data)
        os.environ['MAIL_USERNAME'] = form.mail_username.data
        if form.mail_password.data:
            os.environ['MAIL_PASSWORD'] = form.mail_password.data
        os.environ['MAIL_USE_TLS'] = str(form.mail_use_tls.data)
        os.environ['MAIL_USE_SSL'] = str(form.mail_use_ssl.data)
        os.environ['MAIL_DEFAULT_SENDER'] = form.mail_default_sender.data

        current_app.config['MAIL_SERVER'] = form.mail_server.data
        current_app.config['MAIL_PORT'] = form.mail_port.data
        current_app.config['MAIL_USERNAME'] = form.mail_username.data
        if form.mail_password.data:
            current_app.config['MAIL_PASSWORD'] = form.mail_password.data
        current_app.config['MAIL_USE_TLS'] = form.mail_use_tls.data
        current_app.config['MAIL_USE_SSL'] = form.mail_use_ssl.data
        current_app.config['MAIL_DEFAULT_SENDER'] = form.mail_default_sender.data

        flash(f'Email settings updated ({_SETTINGS_NOTE})', 'success')
    else:
        for field, errors in form.errors.items():
            for error in errors:
                flash(f'{field}: {error}', 'danger')
    return redirect(url_for('admin.system'))


@admin_bp.route('/system/pesapal', methods=['POST'])
@login_required
@admin_required
def update_pesapal_settings():
    form = PesapalSettingsForm()
    if form.validate_on_submit():
        # Note: api/payment.py reads these into module-level constants at
        # import time, so changing os.environ here won't affect an
        # already-running PesaPal client until the process restarts.
        os.environ['PESAPAL_CONSUMER_KEY'] = form.pesapal_consumer_key.data
        os.environ['PESAPAL_CONSUMER_SECRET'] = form.pesapal_consumer_secret.data
        os.environ['PESAPAL_IPN_ID'] = form.pesapal_ipn_id.data
        os.environ['PESAPAL_LIVE_MODE'] = str(form.pesapal_live_mode.data)
        flash(f'PesaPal settings updated ({_SETTINGS_NOTE}) A restart is required for payment requests to use the new credentials.', 'success')
    else:
        for field, errors in form.errors.items():
            for error in errors:
                flash(f'{field}: {error}', 'danger')
    return redirect(url_for('admin.system'))


@admin_bp.route('/system/app', methods=['POST'])
@login_required
@admin_required
def update_app_settings():
    form = AppSettingsForm()
    if form.validate_on_submit():
        os.environ['APP_NAME'] = form.app_name.data
        os.environ['COMPANY_NAME'] = form.company_name.data
        os.environ['COMPANY_LOGO'] = form.company_logo.data or ''
        os.environ['TIMEZONE'] = form.timezone.data
        os.environ['DATE_FORMAT'] = form.date_format.data
        flash(f'Application settings updated ({_SETTINGS_NOTE})', 'success')
    else:
        for field, errors in form.errors.items():
            for error in errors:
                flash(f'{field}: {error}', 'danger')
    return redirect(url_for('admin.system'))


@admin_bp.route('/system/backup', methods=['POST'])
@login_required
@admin_required
def backup_database():
    flash('Database backup is not implemented in this build. For Neon Postgres, use the Neon console\'s branch/backup feature instead.', 'info')
    return redirect(url_for('admin.system'))


@admin_bp.route('/system/clear-cache', methods=['POST'])
@login_required
@admin_required
def clear_cache():
    flash('This app has no server-side cache layer to clear yet - nothing to do.', 'info')
    return redirect(url_for('admin.system'))


@admin_bp.route('/statement/<int:owner_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def statement_generate(owner_id):
    """ADMIN-ONLY: generate an account statement for ANY tenant, by user id -
    the platform-level compliance/legal-request escape hatch (a bank,
    auditor or police/legal inquiry about one account's activity). owner_id
    may be a TEAM MEMBER's own id, not just a literal owner's - every row in
    admin/users.html links here, owners and team members alike - so this
    always resolves through .get_owner_id() first, landing on that team's
    real tenant scope rather than an empty, meaningless team-member-id scope.

    Nothing technically stops an admin from reaching this same data by other
    means today (direct DB access, impersonation, etc.) - the point of this
    route is an HONEST, deliberate audit trail of the fact that it happened:
    every use writes a prominent ActivityLog entry naming the admin, the
    target tenant, and the range - in addition to the AccountStatement row's
    own generated_by_id, the quieter half of the same trail (see
    models/billing.py's AccountStatement docstring).

    Viewing/printing/exporting the resulting statement happens on the same
    shared routes a tenant's own owner uses (wallet_statement_view/_print,
    export_account_statement in api/index.py) - _statement_visible() there
    lets any admin through, so this route only needs to handle the
    CREATE-for-another-tenant step and its logging.
    """
    target_user = User.query.get_or_404(owner_id)
    target_owner_id = target_user.get_owner_id()
    target_owner = User.query.get(target_owner_id)

    form = StatementForm(owner_id=target_owner_id)

    if form.validate_on_submit():
        # Defense in depth: the form's client choices are already scoped to
        # the target tenant's own clients, but a crafted POST can submit any
        # id directly - re-check ownership server-side.
        selected_client = Client.query.get(form.client_id.data) if form.client_id.data else None
        if form.client_id.data and (not selected_client or selected_client.owner_id != target_owner_id):
            flash('Invalid client selected.', 'danger')
        else:
            try:
                statement = AccountStatement(
                    reference_number='PENDING',
                    owner_id=target_owner_id,
                    generated_by_id=current_user.id,
                    client_id=form.client_id.data or None,
                    period_start=form.period_start.data,
                    period_end=form.period_end.data,
                    format='pdf',
                )
                db.session.add(statement)
                db.session.flush()  # assigns statement.id for numbering, still inside the transaction
                statement.reference_number = f"STMT-{statement.id:05d}"

                # Prominent, honest audit trail: this is a real
                # privacy-sensitive capability (an admin pulling another
                # tenant's financial data), so this entry names the admin
                # explicitly rather than reading like routine tenant
                # activity - see the docstring above.
                db.session.add(ActivityLog(
                    user_id=current_user.id,
                    owner_id=target_owner_id,
                    action_type='admin_statement_generated',
                    entity_type='account_statement',
                    entity_id=statement.id,
                    description=(
                        f'ADMIN {current_user.get_full_name()} ({current_user.email}) generated account '
                        f'statement {statement.reference_number} for tenant {target_owner.get_full_name()} '
                        f'({target_owner.email}, user id {target_owner_id}), covering '
                        f'{form.period_start.data} to {form.period_end.data}.'
                    ),
                ))
                db.session.commit()
                flash(f'Statement {statement.reference_number} generated for {target_owner.get_full_name()}.', 'success')
                return redirect(url_for('wallet_statement_view', statement_id=statement.id))
            except Exception as e:
                db.session.rollback()
                current_app.logger.error(f"Error generating admin account statement: {str(e)}")
                flash(f'Error generating statement: {str(e)}', 'danger')

    return render_template('wallet_statement.html', is_owner=True, form=form, report=None,
                          owner=target_owner, admin_target=target_user)
