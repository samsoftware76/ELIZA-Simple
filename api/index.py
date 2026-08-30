from flask import Flask, render_template, jsonify, request, redirect, url_for, flash, session
import os
import logging
import sys
import re
from datetime import datetime, timedelta
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from flask_mail import Mail, Message
from flask_wtf.csrf import CSRFProtect

# For reporting features
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend for server-side plotting

# Add the parent directory to sys.path to allow importing from sibling packages
parent_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

# Load environment variables from .env file in the ELIZA_App directory
dotenv_path = os.path.join(parent_dir, '.env')
load_dotenv(dotenv_path)

# Import database models
from models import db
from models.models import User, Client, Project, Task, Comment, TimeEntry, ProjectMember, ActivityLog
from models.subscription import Subscription, SubscriptionPlan
from models.billing import Quote, QuoteItem, Invoice, InvoiceItem

# Import database utilities for improved connection handling
from utils.db_utils import configure_db_pool, retry_operation, safe_commit, safe_query
from models import ProjectStatus, TaskStatus, UserRole
from models.subscription import Payment
from api.payment import PesaPalPayment

# Import forms
from forms import LoginForm, RegistrationForm, PasswordResetRequestForm, PasswordResetForm, ClientForm, ProjectForm, TaskForm, TimeEntryForm, ProjectAssignmentForm, BusinessProfileForm

# Import template filters
from filters import register_filters

# Import email utilities
from utils.email_utils import mail, send_task_assignment_notification, send_task_update_notification, send_task_comment_notification, send_bulk_email, send_password_reset_email

app = Flask(__name__, template_folder='../templates', static_folder='../static')

# Configure the SQLAlchemy part of the app
DATABASE_URL = os.getenv('DATABASE_URL')
print(f"DEBUG - Database URL from env: {DATABASE_URL}")
print(f"DEBUG - .env file path: {dotenv_path}")
print(f"DEBUG - .env file exists: {os.path.exists(dotenv_path)}")

# If DATABASE_URL is None, provide a default for development
if DATABASE_URL is None:
    print("WARNING: DATABASE_URL not found in environment variables. Using a default SQLite database for development.")
    DATABASE_URL = 'sqlite:///eliza_dev.db'

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default-secret-key-for-dev')

# Email configuration
app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS', 'True').lower() in ('true', 'yes', '1')
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER', 'noreply@elizaapp.com')
app.config['BASE_URL'] = os.environ.get('BASE_URL', 'http://localhost:5000')

# Configure database connection pool for better handling of cloud database connections
configure_db_pool(app)

# Initialize the database
db.init_app(app)

# Initialize Flask-Mail
mail.init_app(app)

# Initialize CSRF protection
csrf = CSRFProtect(app)

# Initialize Talisman security headers (HTTPS/HSTS/secure cookies in production only)
from security import configure_security
configure_security(app)

# Import and register blueprints
from api.subscription import subscription_bp
from api.email_routes import email_bp
from api.admin import admin_bp
from api.billing import billing_bp
from api.portal import portal_bp

app.register_blueprint(subscription_bp)
app.register_blueprint(email_bp)
app.register_blueprint(admin_bp, url_prefix='/admin')
app.register_blueprint(billing_bp)
app.register_blueprint(portal_bp)

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'info'

# Register template filters
register_filters(app)

@login_manager.user_loader
def load_user(user_id):
    # Use our retry logic for more robust database connections
    def get_user():
        return User.query.get(int(user_id))
    return retry_operation(get_user)

@app.route('/')
def home():
    """Home page"""
    # If user is logged in, show their active subscription
    if current_user.is_authenticated:
        # Use our retry logic for more robust database connections
        def get_subscription():
            return Subscription.query.filter_by(user_id=current_user.id, is_active=True).first()
        
        subscription = retry_operation(get_subscription)
        return render_template('index.html', title='Dashboard', subscription=subscription)
    
    return render_template('index.html', title='Home')

@app.route('/test-email')
@login_required
def test_email():
    """Test email configuration by sending a test email to the current user"""
    from utils.email_utils import test_email_configuration
    
    success = test_email_configuration(current_user.email)
    
    if success:
        flash('Test email sent successfully! Please check your inbox.', 'success')
    else:
        flash('Failed to send test email. Please check the server logs for details.', 'danger')
    
    return redirect(url_for('home'))

# Authentication routes
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    
    form = LoginForm()
    if form.validate_on_submit():
        try:
            # Use retry logic for database query
            def get_user():
                return User.query.filter_by(email=form.email.data).first()
            
            user = retry_operation(get_user)
            
            # For debugging - check if the admin account exists
            if form.email.data == 'smugabii22@gmail.com' and not user:
                print(f"DEBUG: Admin user with email {form.email.data} not found in database")
                # Try to create the admin account if it doesn't exist
                admin_user = User(
                    username='admin',
                    email='smugabii22@gmail.com',
                    password_hash=generate_password_hash('admin123'),
                    first_name='Admin',
                    last_name='User',
                    role='admin'
                )
                db.session.add(admin_user)
                db.session.commit()
                user = admin_user
                print("DEBUG: Created admin user account")
            
            if user and user.check_password(form.password.data):
                login_user(user, remember=form.remember_me.data)
                next_page = request.args.get('next')
                flash('Login successful!', 'success')
                return redirect(next_page or url_for('home'))
            else:
                print(f"DEBUG: Login failed for {form.email.data}. User exists: {user is not None}")
                flash('Login failed. Please check your email and password.', 'danger')
        except Exception as e:
            print(f"ERROR during login: {str(e)}")
            flash('An error occurred during login. Please try again.', 'danger')
    
    return render_template('auth/login.html', title='Login', form=form)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    
    form = RegistrationForm()
    if form.validate_on_submit():
        user = User(
            username=form.username.data,
            email=form.email.data,
            first_name=form.first_name.data,
            last_name=form.last_name.data,
            phone=form.phone.data,
            role=UserRole[form.role.data]  # Convert string to enum
        )
        user.set_password(form.password.data)
        
        db.session.add(user)
        db.session.commit()
        
        flash('Your account has been created! You can now log in.', 'success')
        return redirect(url_for('login'))
    
    return render_template('auth/register.html', title='Register', form=form)

@app.route('/logout')
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('home'))

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    """Let a user set the business name/logo shown to their own clients on
    the portal and in quote/invoice emails, instead of the ELIZA brand."""
    form = BusinessProfileForm(obj=current_user)
    if form.validate_on_submit():
        current_user.business_name = form.business_name.data
        current_user.business_logo_url = form.business_logo_url.data
        db.session.commit()
        flash('Business profile updated.', 'success')
        return redirect(url_for('profile'))
    return render_template('profile.html', form=form)

@app.route('/reset-password-request', methods=['GET', 'POST'])
def reset_password_request():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    
    form = PasswordResetRequestForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user:
            reset_url = url_for('reset_password', token=user.get_reset_token(), _external=True)
            send_password_reset_email(user, reset_url)
        # Same message whether or not the account exists - don't let this
        # route be used to enumerate which emails are registered.
        flash('If that email is registered, password reset instructions have been sent to it.', 'info')
        return redirect(url_for('login'))

    return render_template('auth/reset_password_request.html', title='Reset Password', form=form)

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for('home'))

    user = User.verify_reset_token(token)
    if not user:
        flash('That password reset link is invalid or has expired.', 'danger')
        return redirect(url_for('reset_password_request'))

    form = PasswordResetForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        db.session.commit()
        flash('Your password has been reset successfully. You can now log in with your new password.', 'success')
        return redirect(url_for('login'))

    return render_template('auth/reset_password.html', title='Reset Password', form=form)

# Client Management Routes
@app.route('/clients')
@login_required
def clients():
    """List all clients"""
    clients_list = Client.query.order_by(Client.name).all()
    return render_template('clients/index.html', clients=clients_list, now=datetime.now())

@app.route('/clients/create', methods=['GET', 'POST'])
@login_required
def client_create():
    """Create a new client"""
    form = ClientForm()
    if form.validate_on_submit():
        client = Client(
            name=form.name.data,
            contact_person=form.contact_person.data,
            email=form.email.data,
            phone=form.phone.data,
            address=form.address.data,
            notes=form.notes.data
        )
        db.session.add(client)
        db.session.commit()
        
        # Log the activity
        activity = ActivityLog(
            user_id=current_user.id,
            action_type='created',
            entity_type='client',
            entity_id=client.id,
            description=f'Created client: {client.name}'
        )
        db.session.add(activity)
        db.session.commit()
        
        flash(f'Client "{client.name}" has been created successfully!', 'success')
        return redirect(url_for('clients'))
    
    return render_template('clients/form.html', form=form, client=None)

@app.route('/clients/<int:client_id>')
@login_required
def client_detail(client_id):
    """View client details"""
    client = Client.query.get_or_404(client_id)
    return render_template('clients/detail.html', client=client)

@app.route('/clients/<int:client_id>/edit', methods=['GET', 'POST'])
@login_required
def client_edit(client_id):
    """Edit an existing client"""
    client = Client.query.get_or_404(client_id)
    form = ClientForm(client_id=client.id, obj=client)
    
    if form.validate_on_submit():
        form.populate_obj(client)
        db.session.commit()
        
        # Log the activity
        activity = ActivityLog(
            user_id=current_user.id,
            action_type='updated',
            entity_type='client',
            entity_id=client.id,
            description=f'Updated client: {client.name}'
        )
        db.session.add(activity)
        db.session.commit()
        
        flash(f'Client "{client.name}" has been updated successfully!', 'success')
        return redirect(url_for('client_detail', client_id=client.id))
    
    return render_template('clients/form.html', form=form, client=client)

@app.route('/clients/<int:client_id>/delete')
@login_required
def client_delete(client_id):
    """Delete a client"""
    client = Client.query.get_or_404(client_id)
    client_name = client.name
    
    # Check if client has associated projects
    if client.projects:
        flash(f'Cannot delete client "{client_name}" because it has associated projects. Please delete or reassign the projects first.', 'danger')
        return redirect(url_for('client_detail', client_id=client.id))
    
    # Log the activity before deleting the client
    activity = ActivityLog(
        user_id=current_user.id,
        action_type='deleted',
        entity_type='client',
        entity_id=client.id,
        description=f'Deleted client: {client.name}'
    )
    db.session.add(activity)
    
    # Delete the client
    db.session.delete(client)
    db.session.commit()
    
    flash(f'Client "{client_name}" has been deleted successfully!', 'success')
    return redirect(url_for('clients'))

# Project Management Routes
@app.route('/projects')
@login_required
def projects():
    """List all projects"""
    projects_list = Project.query.order_by(Project.start_date.desc()).all()
    return render_template('projects/index.html', projects=projects_list, now=datetime.now())

@app.route('/projects/create', methods=['GET', 'POST'])
@login_required
def project_create():
    """Create a new project"""
    form = ProjectForm()
    
    # Pre-select client if provided in query params
    client_id = request.args.get('client_id', type=int)
    if client_id:
        form.client_id.data = client_id
    
    if form.validate_on_submit():
        try:
            project = Project(
                title=form.title.data,
                description=form.description.data,
                client_id=form.client_id.data,
                start_date=form.start_date.data,
                end_date=form.end_date.data,
                status=form.status.data,
                priority=form.priority.data,
                budget=form.budget.data
            )
            db.session.add(project)
            db.session.commit()
            
            # Log the activity
            activity = ActivityLog(
                user_id=current_user.id,
                action_type='created',
                entity_type='project',
                entity_id=project.id,
                description=f'Created project: {project.title}'
            )
            db.session.add(activity)
            db.session.commit()
            
            flash(f'Project "{project.title}" has been created successfully!', 'success')
            return redirect(url_for('project_detail', project_id=project.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating project: {str(e)}', 'danger')
    
    return render_template('projects/form.html', form=form, project=None, now=datetime.now())

@app.route('/projects/<int:project_id>')
@login_required
def project_detail(project_id):
    """View project details"""
    project = Project.query.get_or_404(project_id)
    users = User.query.order_by(User.username).all()
    return render_template('projects/detail.html', project=project, users=users, now=datetime.now())

@app.route('/projects/<int:project_id>/edit', methods=['GET', 'POST'])
@login_required
def project_edit(project_id):
    """Edit an existing project"""
    project = Project.query.get_or_404(project_id)
    form = ProjectForm(obj=project)
    
    if form.validate_on_submit():
        try:
            form.populate_obj(project)
            db.session.commit()
            
            # Log the activity
            activity = ActivityLog(
                user_id=current_user.id,
                action_type='updated',
                entity_type='project',
                entity_id=project.id,
                description=f'Updated project: {project.title}'
            )
            db.session.add(activity)
            db.session.commit()
            
            flash(f'Project "{project.title}" has been updated successfully!', 'success')
            return redirect(url_for('project_detail', project_id=project.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating project: {str(e)}', 'danger')
    
    return render_template('projects/form.html', form=form, project=project, now=datetime.now())

@app.route('/projects/<int:project_id>/delete')
@login_required
def project_delete(project_id):
    """Delete a project"""
    project = Project.query.get_or_404(project_id)
    project_title = project.title
    
    # Check if project has associated tasks
    if project.tasks:
        flash(f'Cannot delete project "{project_title}" because it has associated tasks. Please delete the tasks first.', 'danger')
        return redirect(url_for('project_detail', project_id=project.id))
    
    try:
        # Log the activity before deleting the project
        activity = ActivityLog(
            user_id=current_user.id,
            action_type='deleted',
            entity_type='project',
            entity_id=project.id,
            description=f'Deleted project: {project.title}'
        )
        db.session.add(activity)
        
        # Delete the project
        db.session.delete(project)
        db.session.commit()
        
        flash(f'Project "{project_title}" has been deleted successfully!', 'success')
        return redirect(url_for('projects'))
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting project: {str(e)}', 'danger')
        return redirect(url_for('project_detail', project_id=project.id))

# Time Tracking Routes
@app.route('/tasks/<int:task_id>/time/start', methods=['GET', 'POST'])
@login_required
def task_time_start(task_id):
    """Start time tracking for a task"""
    task = Task.query.get_or_404(task_id)
    
    # Check if there's already an active time entry for this task and user
    active_entry = TimeEntry.query.filter_by(
        task_id=task.id,
        user_id=current_user.id,
        end_time=None
    ).first()
    
    if active_entry:
        flash('You already have an active time entry for this task.', 'warning')
        return redirect(url_for('task_detail', task_id=task.id))
    
    # Create a new time entry with start time only
    time_entry = TimeEntry(
        task_id=task.id,
        user_id=current_user.id,
        start_time=datetime.now(),
        description=f'Started working on {task.title}'
    )
    
    # If the task hasn't been started yet, update the started_at field
    if not task.started_at:
        task.started_at = datetime.now()
    
    try:
        db.session.add(time_entry)
        
        # Log the activity
        activity = ActivityLog(
            user_id=current_user.id,
            action_type='started',
            entity_type='time_entry',
            entity_id=time_entry.id,
            description=f'Started time tracking for task: {task.title}'
        )
        db.session.add(activity)
        db.session.commit()
        
        flash('Time tracking started successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error starting time tracking: {str(e)}', 'danger')
    
    return redirect(url_for('task_detail', task_id=task.id))

@app.route('/tasks/<int:task_id>/time/stop', methods=['GET', 'POST'])
@login_required
def task_time_stop(task_id):
    """Stop time tracking for a task"""
    task = Task.query.get_or_404(task_id)
    
    # Find the active time entry for this task and user
    active_entry = TimeEntry.query.filter_by(
        task_id=task.id,
        user_id=current_user.id,
        end_time=None
    ).first()
    
    if not active_entry:
        flash('You do not have an active time entry for this task.', 'warning')
        return redirect(url_for('task_detail', task_id=task.id))
    
    # Update the time entry with end time and calculate duration
    end_time = datetime.now()
    active_entry.end_time = end_time
    
    # Calculate duration in hours
    duration = (end_time - active_entry.start_time).total_seconds() / 3600
    active_entry.duration = round(duration, 2)  # Round to 2 decimal places
    
    # Update the task's actual hours
    if task.actual_hours:
        task.actual_hours += active_entry.duration
    else:
        task.actual_hours = active_entry.duration
    
    try:
        # Log the activity
        activity = ActivityLog(
            user_id=current_user.id,
            action_type='stopped',
            entity_type='time_entry',
            entity_id=active_entry.id,
            description=f'Stopped time tracking for task: {task.title}. Duration: {active_entry.duration:.2f} hours'
        )
        db.session.add(activity)
        db.session.commit()
        
        flash(f'Time tracking stopped. You worked for {active_entry.duration:.2f} hours.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error stopping time tracking: {str(e)}', 'danger')
    
    return redirect(url_for('task_detail', task_id=task.id))

@app.route('/tasks/<int:task_id>/time/add', methods=['GET', 'POST'])
@login_required
def task_time_add(task_id):
    """Manually add a time entry for a task"""
    task = Task.query.get_or_404(task_id)
    form = TimeEntryForm()
    form.task_id.data = task.id
    
    if form.validate_on_submit():
        # Create a new time entry
        time_entry = TimeEntry(
            task_id=task.id,
            user_id=current_user.id,
            start_time=form.start_time.data,
            end_time=form.end_time.data,
            description=form.description.data
        )
        
        # If duration is provided, use it; otherwise calculate from start/end times
        if form.duration.data:
            time_entry.duration = form.duration.data
        elif form.end_time.data:
            duration = (form.end_time.data - form.start_time.data).total_seconds() / 3600
            time_entry.duration = round(duration, 2)
        
        # Update the task's actual hours
        if time_entry.duration:
            if task.actual_hours:
                task.actual_hours += time_entry.duration
            else:
                task.actual_hours = time_entry.duration
        
        # If the task hasn't been started yet, update the started_at field
        if not task.started_at:
            task.started_at = form.start_time.data
        
        try:
            db.session.add(time_entry)
            
            # Log the activity
            activity = ActivityLog(
                user_id=current_user.id,
                action_type='added',
                entity_type='time_entry',
                entity_id=time_entry.id,
                description=f'Added time entry for task: {task.title}. Duration: {time_entry.duration:.2f} hours'
            )
            db.session.add(activity)
            db.session.commit()
            
            flash(f'Time entry added successfully. Duration: {time_entry.duration:.2f} hours.', 'success')
            return redirect(url_for('task_detail', task_id=task.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding time entry: {str(e)}', 'danger')
    
    return render_template('tasks/time_form.html', form=form, task=task)

@app.route('/tasks/<int:task_id>/comment/delete/<int:comment_id>')
@login_required
def task_delete_comment(task_id, comment_id):
    """Delete a comment from a task"""
    comment = Comment.query.get_or_404(comment_id)
    task = Task.query.get_or_404(task_id)
    
    # Only allow the comment author or an admin to delete the comment
    if comment.user_id != current_user.id and current_user.role != UserRole.ADMIN:
        flash('You do not have permission to delete this comment.', 'danger')
        return redirect(url_for('task_detail', task_id=task_id))
    
    try:
        # Log the activity before deleting the comment
        activity = ActivityLog(
            user_id=current_user.id,
            action_type='deleted',
            entity_type='comment',
            entity_id=comment.id,
            description=f'Deleted comment on task: {task.title}'
        )
        db.session.add(activity)
        
        # Delete the comment
        db.session.delete(comment)
        db.session.commit()
        
        flash('Comment has been deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting comment: {str(e)}', 'danger')
    
    return redirect(url_for('task_detail', task_id=task_id))

# Project Team Management Routes
@app.route('/projects/<int:project_id>/team/assign', methods=['POST'])
@login_required
def project_assign_user(project_id):
    """Assign a user to a project team"""
    project = Project.query.get_or_404(project_id)
    
    if request.method == 'POST':
        user_id = request.form.get('user_id', type=int)
        role = request.form.get('role')
        
        if not user_id or not role:
            flash('User and role are required.', 'danger')
            return redirect(url_for('project_detail', project_id=project.id))
        
        # Check if user exists
        user = User.query.get(user_id)
        if not user:
            flash('Selected user does not exist.', 'danger')
            return redirect(url_for('project_detail', project_id=project.id))
        
        # Check if user is already assigned to this project
        existing_assignment = ProjectMember.query.filter_by(project_id=project.id, user_id=user.id).first()
        if existing_assignment:
            flash(f'User {user.username} is already assigned to this project.', 'warning')
            return redirect(url_for('project_detail', project_id=project.id))
        
        try:
            # Create the team member assignment
            team_member = ProjectMember(
                project_id=project.id,
                user_id=user.id,
                role=role
            )
            db.session.add(team_member)
            
            # Log the activity
            activity = ActivityLog(
                user_id=current_user.id,
                action_type='assigned',
                entity_type='project',
                entity_id=project.id,
                description=f'Added {user.username} to project as {role}'
            )
            db.session.add(activity)
            db.session.commit()
            
            flash(f'{user.username} has been added to the project as {role}.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error assigning user to project: {str(e)}', 'danger')
        
    return redirect(url_for('project_detail', project_id=project.id))

@app.route('/projects/<int:project_id>/team/remove/<int:user_id>')
@login_required
def project_remove_user(project_id, user_id):
    """Remove a user from a project team"""
    project = Project.query.get_or_404(project_id)
    user = User.query.get_or_404(user_id)
    
    # Find the team member assignment
    team_member = ProjectMember.query.filter_by(project_id=project.id, user_id=user.id).first()
    if not team_member:
        flash(f'User {user.username} is not assigned to this project.', 'warning')
        return redirect(url_for('project_detail', project_id=project.id))
    
    try:
        # Check if user has assigned tasks in this project
        assigned_tasks = Task.query.filter_by(project_id=project.id, assignee_id=user.id).count()
        if assigned_tasks > 0:
            flash(f'Cannot remove {user.username} from the project because they have {assigned_tasks} assigned tasks. Please reassign the tasks first.', 'danger')
            return redirect(url_for('project_detail', project_id=project.id))
        
        # Log the activity before removing the team member
        activity = ActivityLog(
            user_id=current_user.id,
            action_type='removed',
            entity_type='project',
            entity_id=project.id,
            description=f'Removed {user.username} from project (was {team_member.role})'
        )
        db.session.add(activity)
        
        # Remove the team member
        db.session.delete(team_member)
        db.session.commit()
        
        flash(f'{user.username} has been removed from the project.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error removing user from project: {str(e)}', 'danger')
    
    return redirect(url_for('project_detail', project_id=project.id))

# Task Management Routes
@app.route('/tasks/create/<int:project_id>', methods=['GET', 'POST'])
@login_required
def task_create(project_id):
    """Create a new task for a project"""
    project = Project.query.get_or_404(project_id)
    form = TaskForm(project=project)
    
    if form.validate_on_submit():
        try:
            task = Task(
                title=form.title.data,
                description=form.description.data,
                project_id=project.id,
                assignee_id=form.assigned_to.data if form.assigned_to.data != 0 else None,
                creator_id=current_user.id,
                status=form.status.data,
                priority=form.priority.data,
                due_date=form.due_date.data,
                estimated_hours=form.estimated_hours.data
            )
            db.session.add(task)
            db.session.commit()
            
            # Log the activity
            activity = ActivityLog(
                user_id=current_user.id,
                action_type='created',
                entity_type='task',
                entity_id=task.id,
                description=f'Created task: {task.title}'
            )
            db.session.add(activity)
            
            # If task is assigned, log that too
            if task.assignee_id:
                assignment_activity = ActivityLog(
                    user_id=current_user.id,
                    action_type='assigned',
                    entity_type='task',
                    entity_id=task.id,
                    description=f'Assigned task to {task.assigned_to.username}'
                )
                db.session.add(assignment_activity)
            
            db.session.commit()
            
            # Send email notification if task is assigned to someone
            if task.assignee_id and task.assignee_id != current_user.id:
                try:
                    print(f"DEBUG - Sending task assignment notification to {task.assigned_to.email}")
                    app.logger.info(f"Sending task assignment notification to {task.assigned_to.email}")
                    send_task_assignment_notification(task, task.assigned_to)
                    print(f"DEBUG - Task notification sent successfully to {task.assigned_to.email}")
                    app.logger.info(f"Task notification sent successfully to {task.assigned_to.email}")
                except Exception as e:
                    # Log the error but don't prevent task creation
                    print(f"DEBUG - Failed to send email notification: {str(e)}")
                    app.logger.error(f"Failed to send email notification: {str(e)}")
                    import traceback
                    print(traceback.format_exc())
                    app.logger.error(traceback.format_exc())
            
            flash(f'Task "{task.title}" has been created successfully!', 'success')
            return redirect(url_for('project_detail', project_id=project.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating task: {str(e)}', 'danger')
    
    return render_template('tasks/form.html', form=form, task=None, project=project, now=datetime.now())

@app.route('/tasks/<int:task_id>')
@login_required
def task_detail(task_id):
    """View task details"""
    task = Task.query.get_or_404(task_id)
    return render_template('tasks/detail.html', task=task, now=datetime.now())

@app.route('/tasks/<int:task_id>/edit', methods=['GET', 'POST'])
@login_required
def task_edit(task_id):
    """Edit an existing task"""
    task = Task.query.get_or_404(task_id)
    form = TaskForm(obj=task, project=task.project)
    
    if form.validate_on_submit():
        try:
            # Store old values for comparison
            old_assignee_id = task.assignee_id
            old_status = task.status.name if task.status else None
            old_due_date = task.due_date
            
            # Check if assignment changed
            new_assignee_id = form.assigned_to.data if form.assigned_to.data != 0 else None
            assignment_changed = old_assignee_id != new_assignee_id
            
            # Update task fields
            form.populate_obj(task)
            task.assignee_id = new_assignee_id
            db.session.commit()
            
            # Log the activity
            activity = ActivityLog(
                user_id=current_user.id,
                action_type='updated',
                entity_type='task',
                entity_id=task.id,
                description=f'Updated task: {task.title}'
            )
            db.session.add(activity)
            
            # If assignment changed, log that too
            if assignment_changed:
                if new_assignee_id:
                    assignment_activity = ActivityLog(
                        user_id=current_user.id,
                        action_type='assigned',
                        entity_type='task',
                        entity_id=task.id,
                        description=f'Assigned task to {task.assigned_to.username}'
                    )
                else:
                    assignment_activity = ActivityLog(
                        user_id=current_user.id,
                        action_type='unassigned',
                        entity_type='task',
                        entity_id=task.id,
                        description='Unassigned task'
                    )
                db.session.add(assignment_activity)
            
            db.session.commit()
            
            # Send email notifications for task updates
            try:
                # If assignment changed and task is assigned to someone
                if assignment_changed and new_assignee_id and new_assignee_id != current_user.id:
                    print(f"DEBUG - Sending task assignment notification to {task.assigned_to.email} from task edit")
                    app.logger.info(f"Sending task assignment notification to {task.assigned_to.email} from task edit")
                    send_task_assignment_notification(task, task.assigned_to)
                    print(f"DEBUG - Task assignment notification sent successfully to {task.assigned_to.email}")
                    app.logger.info(f"Task assignment notification sent successfully to {task.assigned_to.email}")
                
                # Notify team about status change
                if 'status' in form.data and form.data['status'] != old_status:
                    print(f"DEBUG - Sending task status update notification for task {task.title}")
                    app.logger.info(f"Sending task status update notification for task {task.title}")
                    send_task_update_notification(task, 'status_change', current_user)
                    print(f"DEBUG - Task status update notification sent successfully")
                    app.logger.info(f"Task status update notification sent successfully")
                
                # Notify team about due date change
                if form.due_date.data != old_due_date:
                    send_task_update_notification(task, 'due_date_change', current_user)
            except Exception as e:
                # Log the error but don't prevent task update
                print(f"DEBUG - Failed to send email notification from task edit: {str(e)}")
                app.logger.error(f"Failed to send email notification from task edit: {str(e)}")
                import traceback
                print(traceback.format_exc())
                app.logger.error(traceback.format_exc())
            
            flash(f'Task "{task.title}" has been updated successfully!', 'success')
            return redirect(url_for('task_detail', task_id=task.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating task: {str(e)}', 'danger')
    
    return render_template('tasks/form.html', form=form, task=task, project=task.project, now=datetime.now())

@app.route('/tasks/<int:task_id>/delete')
@login_required
def task_delete(task_id):
    """Delete a task"""
    task = Task.query.get_or_404(task_id)
    project_id = task.project_id
    task_title = task.title
    
    try:
        # Log the activity before deleting the task
        activity = ActivityLog(
            user_id=current_user.id,
            action_type='deleted',
            entity_type='task',
            entity_id=task.id,
            description=f'Deleted task: {task.title}'
        )
        db.session.add(activity)
        
        # Delete the task
        db.session.delete(task)
        db.session.commit()
        
        flash(f'Task "{task_title}" has been deleted successfully!', 'success')
        return redirect(url_for('project_detail', project_id=project_id))
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting task: {str(e)}', 'danger')
        return redirect(url_for('task_detail', task_id=task_id))

@app.route('/tasks/<int:task_id>/comment', methods=['POST'])
@login_required
def task_add_comment(task_id):
    """Add a comment to a task"""
    task = Task.query.get_or_404(task_id)
    content = request.form.get('content')
    
    if not content:
        flash('Comment content is required.', 'danger')
        return redirect(url_for('task_detail', task_id=task_id))
    
    comment = Comment(
        task_id=task_id,
        user_id=current_user.id,
        content=content
    )
    
    try:
        db.session.add(comment)
        
        # Log the activity
        activity = ActivityLog(
            user_id=current_user.id,
            action_type='commented',
            entity_type='task',
            entity_id=task.id,
            description=f'Added comment to task: {task.title}'
        )
        db.session.add(activity)
        db.session.commit()
        
        # Send email notification about the new comment
        try:
            print(f"DEBUG - Sending task comment notification for task {task.title}")
            app.logger.info(f"Sending task comment notification for task {task.title}")
            send_task_comment_notification(task, comment, current_user)
            print(f"DEBUG - Task comment notification sent successfully")
            app.logger.info(f"Task comment notification sent successfully")
        except Exception as e:
            # Log the error but don't prevent comment creation
            print(f"DEBUG - Failed to send comment notification: {str(e)}")
            app.logger.error(f"Failed to send comment notification: {str(e)}")
            import traceback
            print(traceback.format_exc())
            app.logger.error(traceback.format_exc())
        
        flash('Comment added successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error adding comment: {str(e)}', 'danger')
    
    return redirect(url_for('task_detail', task_id=task_id))

# Reporting Routes
@app.route('/reports')
@login_required
def reports():
    """Reports dashboard"""
    return render_template('reports/index.html')

@app.route('/reports/project-status')
@login_required
def report_project_status():
    """Project status report"""
    projects = Project.query.all()
    
    # Prepare data for charts
    status_counts = {}
    priority_counts = [0, 0, 0]  # Low, Medium, High
    
    for project in projects:
        # Count by status
        status_name = project.status.name
        if status_name in status_counts:
            status_counts[status_name] += 1
        else:
            status_counts[status_name] = 1
        
        # Count by priority (1=Low, 2=Medium, 3=High)
        if project.priority and 1 <= project.priority <= 3:
            priority_counts[project.priority - 1] += 1
    
    # Convert status counts to lists for the chart
    status_labels = list(status_counts.keys())
    status_values = list(status_counts.values())
    
    return render_template('reports/project_status.html', 
                           projects=projects,
                           status_labels=status_labels,
                           status_counts=status_values,
                           priority_counts=priority_counts,
                           now=datetime.now())

@app.route('/reports/task-distribution')
@login_required
def report_task_distribution():
    """Task distribution report"""
    # Get filter parameters
    status_filter = request.args.get('status')
    
    # Query tasks with optional filter
    if status_filter:
        tasks = Task.query.filter_by(status=status_filter).all()
    else:
        tasks = Task.query.all()
    
    # Prepare data for charts
    status_counts = {}
    priority_counts = [0, 0, 0]  # Low, Medium, High
    assignee_counts = {}
    
    # Project-specific task counts by status
    projects = Project.query.all()
    project_labels = [project.title for project in projects]
    todo_counts = [0] * len(projects)
    in_progress_counts = [0] * len(projects)
    review_counts = [0] * len(projects)
    completed_counts = [0] * len(projects)
    
    for task in tasks:
        # Count by status
        status_name = task.status.name
        if status_name in status_counts:
            status_counts[status_name] += 1
        else:
            status_counts[status_name] = 1
        
        # Count by priority (1=Low, 2=Medium, 3=High)
        if task.priority and 1 <= task.priority <= 3:
            priority_counts[task.priority - 1] += 1
        
        # Count by assignee
        assignee_name = task.assigned_to.username if task.assignee_id else 'Unassigned'
        if assignee_name in assignee_counts:
            assignee_counts[assignee_name] += 1
        else:
            assignee_counts[assignee_name] = 1
        
        # Count by project and status
        if task.project:
            try:
                project_index = project_labels.index(task.project.title)
                if task.status.name == 'TODO':
                    todo_counts[project_index] += 1
                elif task.status.name == 'IN_PROGRESS':
                    in_progress_counts[project_index] += 1
                elif task.status.name == 'REVIEW':
                    review_counts[project_index] += 1
                elif task.status.name == 'COMPLETED':
                    completed_counts[project_index] += 1
            except ValueError:
                # Project not in the list (shouldn't happen, but just in case)
                pass
    
    # Convert status counts to lists for the chart
    status_labels = list(status_counts.keys())
    status_values = list(status_counts.values())
    
    # Convert assignee counts to lists for the chart
    assignee_labels = list(assignee_counts.keys())
    assignee_values = list(assignee_counts.values())
    
    return render_template('reports/task_distribution.html', 
                           tasks=tasks,
                           status_labels=status_labels,
                           status_counts=status_values,
                           priority_counts=priority_counts,
                           assignee_labels=assignee_labels,
                           assignee_counts=assignee_values,
                           project_labels=project_labels,
                           todo_counts=todo_counts,
                           in_progress_counts=in_progress_counts,
                           review_counts=review_counts,
                           completed_counts=completed_counts,
                           now=datetime.now())

@app.route('/reports/time-tracking')
@login_required
def report_time_tracking():
    """Time tracking report"""
    projects = Project.query.all()
    time_entries = TimeEntry.query.order_by(TimeEntry.start_time.desc()).limit(50).all()
    
    # Prepare data for charts
    project_hours = {}
    user_hours = {}
    
    # Calculate hours per project
    for project in projects:
        total_hours = 0
        for task in project.tasks:
            if task.actual_hours:
                total_hours += task.actual_hours
        project_hours[project.title] = round(total_hours, 1)
    
    # Calculate hours per user from time entries
    for entry in TimeEntry.query.all():
        if entry.duration:  # Only count entries with a duration
            username = entry.user.username
            if username in user_hours:
                user_hours[username] += entry.duration
            else:
                user_hours[username] = entry.duration
    
    # Round user hours to 1 decimal place
    for username in user_hours:
        user_hours[username] = round(user_hours[username], 1)
    
    # Convert to lists for the charts
    project_labels = list(project_hours.keys())
    project_hours_values = list(project_hours.values())
    
    user_labels = list(user_hours.keys())
    user_hours_values = list(user_hours.values())
    
    return render_template('reports/time_tracking.html', 
                           projects=projects,
                           time_entries=time_entries,
                           project_labels=project_labels,
                           project_hours=project_hours_values,
                           user_labels=user_labels,
                           user_hours=user_hours_values,
                           now=datetime.now())

@app.route('/reports/project-timeline')
@login_required
def report_project_timeline():
    """Project timeline report (Gantt chart)"""
    projects = Project.query.all()
    
    # Generate timeline data for projects and tasks
    timeline_data = []
    
    for project in projects:
        # Add project to timeline
        timeline_data.append({
            'id': f'p{project.id}',
            'name': project.title,
            'start': project.start_date.strftime('%Y-%m-%d') if project.start_date else None,
            'end': project.end_date.strftime('%Y-%m-%d') if project.end_date else None,
            'type': 'project',
            'progress': calculate_project_progress(project)
        })
        
        # Add tasks to timeline
        for task in project.tasks:
            if task.started_at or task.due_date:  # Only include tasks with dates
                timeline_data.append({
                    'id': f't{task.id}',
                    'name': task.title,
                    'parent': f'p{project.id}',
                    'start': task.started_at.strftime('%Y-%m-%d') if task.started_at else None,
                    'end': task.due_date.strftime('%Y-%m-%d') if task.due_date else None,
                    'type': 'task',
                    'progress': 100 if task.status.name == 'COMPLETED' else 
                               75 if task.status.name == 'REVIEW' else 
                               50 if task.status.name == 'IN_PROGRESS' else 0,
                    'assignee': task.assigned_to.username if task.assignee_id else 'Unassigned'
                })
    
    return render_template('reports/project_timeline.html', 
                           projects=projects,
                           timeline_data=timeline_data,
                           now=datetime.now())

@app.route('/tasks')
@login_required
def all_tasks():
    """View all tasks with filtering options"""
    # Get filter parameters from request
    status = request.args.get('status')
    priority = request.args.get('priority')
    assignee = request.args.get('assignee')
    project_id = request.args.get('project')
    
    # Start with base query
    query = Task.query
    
    # Apply filters
    if status:
        query = query.filter(Task.status == status)
    
    if priority:
        query = query.filter(Task.priority == priority)
    
    if assignee:
        if assignee == 'me':
            query = query.filter(Task.assignee_id == current_user.id)
        elif assignee == 'unassigned':
            query = query.filter(Task.assignee_id == None)
        else:
            query = query.filter(Task.assignee_id == assignee)
    
    if project_id:
        query = query.filter(Task.project_id == project_id)
    
    # Get all tasks with applied filters
    tasks = query.order_by(Task.due_date.asc()).all()
    
    # Get all projects and users for filter dropdowns
    projects = Project.query.all()
    users = User.query.all()
    
    return render_template('tasks/all_tasks.html', 
                          tasks=tasks, 
                          projects=projects, 
                          users=users, 
                          now=datetime.now())

@app.route('/tasks/create', methods=['GET', 'POST'])
@login_required
def create_task():
    """Create a new task without requiring a project_id"""
    form = TaskForm()
    
    # Get all projects for the dropdown
    projects = Project.query.all()
    
    # Get all users for the assignee dropdown
    users = User.query.all()
    
    if form.validate_on_submit():
        # Create new task
        task = Task(
            title=form.title.data,
            description=form.description.data,
            project_id=form.project_id.data,
            assignee_id=form.assigned_to.data if form.assigned_to.data != 0 else None,
            creator_id=current_user.id,
            status=form.status.data,
            priority=form.priority.data,
            due_date=form.due_date.data,
            estimated_hours=form.estimated_hours.data,
            is_billable=form.is_billable.data
        )
        
        db.session.add(task)
        db.session.commit()
        
        # Create activity log entry
        activity = ActivityLog(
            user_id=current_user.id,
            action_type='task_created',
            entity_type='task',
            entity_id=task.id,
            description=f'Task "{task.title}" created'
        )
        db.session.add(activity)
        db.session.commit()
        
        # Send notification if task is assigned
        if task.assignee_id and task.assignee_id != current_user.id:
            send_task_assignment_notification(task, task.assigned_to)
        
        flash(f'Task "{task.title}" created successfully.', 'success')
        return redirect(url_for('task_detail', task_id=task.id))
    
    # If GET request or form validation failed
    return render_template('tasks/create_task.html', 
                          form=form, 
                          projects=projects, 
                          users=users)

@app.route('/reports/overdue-tasks')
@login_required
def report_overdue_tasks():
    """Overdue tasks report"""
    today = datetime.now().date()
    overdue_tasks = Task.query.filter(
        Task.due_date < today,
        Task.status != TaskStatus.COMPLETED
    ).order_by(Task.due_date).all()
    
    # Group tasks by project
    projects_with_overdue = {}
    for task in overdue_tasks:
        if task.project_id in projects_with_overdue:
            projects_with_overdue[task.project_id]['tasks'].append(task)
        else:
            projects_with_overdue[task.project_id] = {
                'project': task.project,
                'tasks': [task]
            }
    
    # Prepare chart data for assignees
    assignee_data = {}
    for task in overdue_tasks:
        assignee_name = task.assigned_to.username if task.assignee_id else 'Unassigned'
        if assignee_name in assignee_data:
            assignee_data[assignee_name] += 1
        else:
            assignee_data[assignee_name] = 1
    
    # Prepare chart data for priority
    priority_data = [0, 0, 0]  # Low, Medium, High
    for task in overdue_tasks:
        if task.priority and 1 <= task.priority <= 3:
            priority_data[task.priority - 1] += 1
    
    # Format for Chart.js
    assignee_chart_data = {
        'labels': list(assignee_data.keys()),
        'values': list(assignee_data.values())
    }
    
    return render_template('reports/overdue_tasks.html', 
                           projects_with_overdue=projects_with_overdue,
                           overdue_tasks=overdue_tasks,
                           assignee_chart_data=assignee_chart_data,
                           priority_chart_data=priority_data,
                           now=datetime.now())

# Helper functions for reports
def calculate_project_progress(project):
    """Calculate the progress percentage of a project based on completed tasks"""
    task_count = len(project.tasks)
    if task_count == 0:
        return 0
    
    completed_tasks = sum(1 for task in project.tasks if task.status.name == 'COMPLETED')
    return int((completed_tasks / task_count) * 100)

@app.route('/export/project-status')
@login_required
def export_project_status_report():
    """Export project status report as CSV"""
    projects = Project.query.all()
    
    # Create a DataFrame
    data = []
    for project in projects:
        # Calculate progress
        progress = calculate_project_progress(project)
        
        data.append({
            'Project': project.title,
            'Client': project.client.name,
            'Status': project.status.value,
            'Priority': 'High' if project.priority == 3 else 'Medium' if project.priority == 2 else 'Low',
            'Start Date': project.start_date.strftime('%Y-%m-%d') if project.start_date else '',
            'End Date': project.end_date.strftime('%Y-%m-%d') if project.end_date else '',
            'Progress': f'{progress}%',
            'Tasks': len(project.tasks),
            'Completed Tasks': sum(1 for task in project.tasks if task.status.name == 'COMPLETED')
        })
    
    # Convert to DataFrame
    df = pd.DataFrame(data)
    
    # Create CSV in memory
    csv_data = df.to_csv(index=False)
    
    # Create response
    response = app.response_class(
        csv_data,
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=project_status_report.csv'}
    )
    
    return response

@app.route('/export/task-distribution')
@login_required
def export_task_distribution_report():
    """Export task distribution report as CSV"""
    tasks = Task.query.all()
    
    # Create a DataFrame
    data = []
    for task in tasks:
        data.append({
            'Task': task.title,
            'Project': task.project.title,
            'Assignee': task.assigned_to.username if task.assignee_id else 'Unassigned',
            'Status': task.status.value,
            'Priority': 'High' if task.priority == 3 else 'Medium' if task.priority == 2 else 'Low',
            'Due Date': task.due_date.strftime('%Y-%m-%d') if task.due_date else '',
            'Estimated Hours': task.estimated_hours if task.estimated_hours else 0,
            'Actual Hours': task.actual_hours if task.actual_hours else 0,
            'Created Date': task.created_at.strftime('%Y-%m-%d')
        })
    
    # Convert to DataFrame
    df = pd.DataFrame(data)
    
    # Create CSV in memory
    csv_data = df.to_csv(index=False)
    
    # Create response
    response = app.response_class(
        csv_data,
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=task_distribution_report.csv'}
    )
    
    return response

@app.route('/export/time-tracking')
@login_required
def export_time_tracking_report():
    """Export time tracking report as CSV"""
    time_entries = TimeEntry.query.all()
    
    # Create a DataFrame
    data = []
    for entry in time_entries:
        data.append({
            'User': entry.user.username,
            'Task': entry.task.title,
            'Project': entry.task.project.title,
            'Start Time': entry.start_time.strftime('%Y-%m-%d %H:%M'),
            'End Time': entry.end_time.strftime('%Y-%m-%d %H:%M') if entry.end_time else '',
            'Duration (hours)': entry.duration if entry.duration else 
                              ((entry.end_time - entry.start_time).total_seconds() / 3600) if entry.end_time else '',
            'Description': entry.description or ''
        })
    
    # Convert to DataFrame
    df = pd.DataFrame(data)
    
    # Create CSV in memory
    csv_data = df.to_csv(index=False)
    
    # Create response
    response = app.response_class(
        csv_data,
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=time_tracking_report.csv'}
    )
    
    return response

@app.route('/export/overdue-tasks')
@login_required
def export_overdue_tasks_report():
    """Export overdue tasks report as CSV"""
    today = datetime.now().date()
    overdue_tasks = Task.query.filter(
        Task.due_date < today,
        Task.status != TaskStatus.COMPLETED
    ).order_by(Task.due_date).all()
    
    # Create a DataFrame
    data = []
    for task in overdue_tasks:
        data.append({
            'Task': task.title,
            'Project': task.project.title,
            'Assignee': task.assigned_to.username if task.assignee_id else 'Unassigned',
            'Due Date': task.due_date.strftime('%Y-%m-%d'),
            'Days Overdue': (today - task.due_date).days,
            'Status': task.status.value,
            'Priority': 'High' if task.priority == 3 else 'Medium' if task.priority == 2 else 'Low',
            'Estimated Hours': task.estimated_hours if task.estimated_hours else 0,
            'Actual Hours': task.actual_hours if task.actual_hours else 0
        })
    
    # Convert to DataFrame
    df = pd.DataFrame(data)
    
    # Create CSV in memory
    csv_data = df.to_csv(index=False)
    
    # Create response
    response = app.response_class(
        csv_data,
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=overdue_tasks_report.csv'}
    )
    
    return response

@app.route('/comments/<int:comment_id>/delete')
@login_required
def comment_delete(comment_id):
    """Delete a comment directly"""
    comment = Comment.query.get_or_404(comment_id)
    
    # Only the comment author or an admin can delete a comment
    if comment.user_id != current_user.id and current_user.role != UserRole.ADMIN:
        flash('You do not have permission to delete this comment.', 'danger')
        return redirect(url_for('task_detail', task_id=comment.task_id))
    
    task_id = comment.task_id
    
    try:
        db.session.delete(comment)
        db.session.commit()
        flash('Comment deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting comment: {str(e)}', 'danger')
    
    return redirect(url_for('task_detail', task_id=task_id))

# Route to initialize the database (only for development)
@app.route('/init-db')
def init_db():
    try:
        with app.app_context():
            db.create_all()
        return jsonify({'message': 'Database initialized successfully!'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
        
# Route to reset the database (only for development - CAUTION: destroys all data)
@app.route('/reset-db')
def reset_db():
    try:
        with app.app_context():
            db.drop_all()
            db.create_all()
        return jsonify({'message': 'Database reset successfully! All tables dropped and recreated.'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Admin routes
@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    if current_user.role != 'admin':
        flash('You do not have permission to access the admin dashboard.', 'danger')
        return redirect(url_for('index'))
    
    # Count statistics
    user_count = User.query.count()
    client_count = Client.query.count()
    active_project_count = Project.query.filter_by(status='in_progress').count()
    
    # Get task counts by status
    todo_count = Task.query.filter_by(status='to_do').count()
    in_progress_count = Task.query.filter_by(status='in_progress').count()
    review_count = Task.query.filter_by(status='in_review').count()
    completed_count = Task.query.filter_by(status='completed').count()
    
    # Get subscription counts by plan (placeholder - would need actual subscription models)
    basic_count = 0
    professional_count = 0
    enterprise_count = 0
    trial_count = 0
    
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

@app.route('/admin/users')
@login_required
def admin_users():
    if current_user.role != 'admin':
        flash('You do not have permission to access the admin dashboard.', 'danger')
        return redirect(url_for('index'))
    
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

@app.route('/admin/subscriptions')
@login_required
def admin_subscriptions():
    if current_user.role != 'admin':
        flash('You do not have permission to access the admin dashboard.', 'danger')
        return redirect(url_for('index'))
    
    # Placeholder - would need actual subscription models
    plans = []
    subscriptions = []
    
    # Create forms for the modals
    add_plan_form = SubscriptionPlanForm()
    edit_plan_form = SubscriptionPlanForm()
    delete_plan_form = SubscriptionPlanForm()
    
    edit_subscription_form = SubscriptionForm()
    cancel_subscription_form = SubscriptionForm()
    
    return render_template('admin/subscriptions.html',
                          plans=plans,
                          subscriptions=subscriptions,
                          add_plan_form=add_plan_form,
                          edit_plan_form=edit_plan_form,
                          delete_plan_form=delete_plan_form,
                          edit_subscription_form=edit_subscription_form,
                          cancel_subscription_form=cancel_subscription_form)

@app.route('/admin/system')
@login_required
def admin_system():
    if current_user.role != 'admin':
        flash('You do not have permission to access the admin dashboard.', 'danger')
        return redirect(url_for('index'))
    
    # Create forms for the system settings
    email_form = EmailSettingsForm()
    pesapal_form = PesapalSettingsForm()
    app_form = AppSettingsForm()
    
    # Populate form with current settings (placeholder)
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
    app_form.date_format.data = os.environ.get('DATE_FORMAT', '%%Y-%%m-%%d')
    
    return render_template('admin/system.html',
                          email_form=email_form,
                          pesapal_form=pesapal_form,
                          app_form=app_form)

if __name__ == '__main__':
    # This part is for local development only
    # Vercel will use the 'app' variable as the WSGI entry point
    print(f"Attempting to load .env from: {dotenv_path}")
    print(f"DATABASE_URL found: {'Yes' if DATABASE_URL else 'No'}")
    
    # Create tables if they don't exist
    with app.app_context():
        db.create_all()
        print("Database tables created (if they didn't exist already)")
    
    app.run(debug=True)
