from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from models.models import User, Client, Project, Task
from models.subscription import Subscription, SubscriptionPlan
from forms import UserForm, SubscriptionPlanForm, SubscriptionForm
from models import db
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash
import json

admin_bp = Blueprint('admin', __name__)

# Admin access decorator
def admin_required(f):
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('You do not have permission to access this page.', 'danger')
            return redirect(url_for('index'))
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
    active_project_count = Project.query.filter_by(status='in_progress').count()
    
    # Get task counts by status
    todo_count = Task.query.filter_by(status='to_do').count()
    in_progress_count = Task.query.filter_by(status='in_progress').count()
    review_count = Task.query.filter_by(status='in_review').count()
    completed_count = Task.query.filter_by(status='completed').count()
    
    # Get subscription counts by plan
    basic_count = Subscription.query.join(SubscriptionPlan).filter(
        SubscriptionPlan.name == 'Basic', 
        Subscription.status == 'active'
    ).count()
    
    professional_count = Subscription.query.join(SubscriptionPlan).filter(
        SubscriptionPlan.name == 'Professional', 
        Subscription.status == 'active'
    ).count()
    
    enterprise_count = Subscription.query.join(SubscriptionPlan).filter(
        SubscriptionPlan.name == 'Enterprise', 
        Subscription.status == 'active'
    ).count()
    
    trial_count = Subscription.query.filter_by(status='trial').count()
    
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
        
        # Create new user
        new_user = User(
            username=form.username.data,
            email=form.email.data,
            password_hash=generate_password_hash(form.password.data),
            role=form.role.data,
            is_active=True
        )
        
        db.session.add(new_user)
        db.session.commit()
        
        flash(f'User {new_user.username} has been created successfully.', 'success')
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
        user.username = form.username.data
        user.email = form.email.data
        user.role = form.role.data
        user.is_active = form.is_active.data
        
        # Update password if provided
        if form.new_password.data:
            user.password_hash = generate_password_hash(form.new_password.data)
        
        db.session.commit()
        
        flash(f'User {user.username} has been updated successfully.', 'success')
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
        
        # Delete the user
        db.session.delete(user)
        db.session.commit()
        
        flash(f'User {user.username} has been deleted successfully.', 'success')
    
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
    
    # Populate the client and plan choices for the subscription form
    clients = Client.query.all()
    edit_subscription_form.client_id.choices = [(c.id, c.name) for c in clients]
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
            price=form.price.data,
            duration=form.duration.data,
            features=form.features.data
        )
        
        db.session.add(new_plan)
        db.session.commit()
        
        flash(f'Subscription plan {new_plan.name} has been created successfully.', 'success')
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
        plan.price = form.price.data
        plan.duration = form.duration.data
        plan.features = form.features.data
        
        db.session.commit()
        
        flash(f'Subscription plan {plan.name} has been updated successfully.', 'success')
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
        
        # Check if plan is being used by any active subscriptions
        active_subscriptions = Subscription.query.filter_by(plan_id=plan.id, status='active').count()
        
        if active_subscriptions > 0:
            flash(f'Cannot delete plan {plan.name} as it has {active_subscriptions} active subscriptions.', 'danger')
            return redirect(url_for('admin.subscriptions'))
        
        # Delete the plan
        db.session.delete(plan)
        db.session.commit()
        
        flash(f'Subscription plan {plan.name} has been deleted successfully.', 'success')
    
    return redirect(url_for('admin.subscriptions'))

@admin_bp.route('/subscriptions/edit', methods=['POST'])
@login_required
@admin_required
def edit_subscription():
    form = SubscriptionForm()
    
    # Populate the choices for validation
    clients = Client.query.all()
    plans = SubscriptionPlan.query.all()
    form.client_id.choices = [(c.id, c.name) for c in clients]
    form.plan_id.choices = [(p.id, p.name) for p in plans]
    
    if form.validate_on_submit():
        subscription = Subscription.query.get_or_404(form.subscription_id.data)
        
        # Update subscription details
        subscription.user_id = form.client_id.data
        subscription.plan_id = form.plan_id.data
        subscription.start_date = form.start_date.data
        subscription.end_date = form.end_date.data
        subscription.status = form.status.data
        
        db.session.commit()
        
        flash('Subscription has been updated successfully.', 'success')
    else:
        for field, errors in form.errors.items():
            for error in errors:
                flash(f'{field}: {error}', 'danger')
    
    return redirect(url_for('admin.subscriptions'))

@admin_bp.route('/subscriptions/cancel', methods=['POST'])
@login_required
@admin_required
def cancel_subscription():
    form = SubscriptionForm()
    
    if form.validate_on_submit():
        subscription = Subscription.query.get_or_404(form.subscription_id.data)
        
        # Update subscription status to cancelled
        subscription.status = 'cancelled'
        subscription.cancellation_reason = form.cancellation_reason.data
        subscription.cancelled_at = datetime.now()
        
        db.session.commit()
        
        # Get client and plan details for the flash message
        client = Client.query.get(subscription.user_id)
        plan = SubscriptionPlan.query.get(subscription.plan_id)
        
        flash(f'Subscription for {client.name} to the {plan.name} plan has been cancelled.', 'success')
    
    return redirect(url_for('admin.subscriptions'))

@admin_bp.route('/system')
@login_required
@admin_required
def system():
    # Placeholder for system settings page
    return render_template('admin/system.html')
