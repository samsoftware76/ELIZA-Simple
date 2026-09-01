from flask import Flask, render_template, jsonify, request, redirect, url_for, flash, session, abort
import os
import logging
import sys
import re
import uuid
from datetime import datetime, timedelta
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from flask_mail import Mail, Message
from flask_wtf.csrf import CSRFProtect

# For reporting features
import pandas as pd
from io import BytesIO
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
from models.models import User, Client, Project, Task, Comment, TimeEntry, ProjectMember, ActivityLog, TeamMembership, TeamInvite, ClientInvite
from models.subscription import Subscription, SubscriptionPlan
from models.billing import Quote, QuoteItem, Invoice, InvoiceItem

# Import database utilities for improved connection handling
from utils.db_utils import configure_db_pool, retry_operation, safe_commit, safe_query
from models import ProjectStatus, TaskStatus, UserRole
from models.subscription import Payment
from api.payment import PesaPalPayment
from api.sms import send_sms

# Import forms
from forms import LoginForm, RegistrationForm, PasswordResetRequestForm, PasswordResetForm, ClientForm, ProjectForm, TaskForm, TimeEntryForm, ProjectAssignmentForm, BusinessProfileForm, AcceptInviteForm, ClientAcceptInviteForm

# Import template filters
from filters import register_filters

# Import email utilities
from utils.email_utils import mail, send_task_assignment_notification, send_task_update_notification, send_task_comment_notification, send_bulk_email, send_password_reset_email, send_client_invite_email

# Import analytics - log_event() is unconditionally safe to call, never raises (see utils/analytics.py)
from utils.analytics import log_event

# Import the deliverables report builder - see utils/deliverables_report.py for why the
# heavier per-project/per-client aggregation lives there instead of inline in this route.
from utils.deliverables_report import build_deliverables_report

app = Flask(__name__, template_folder='../templates', static_folder='../static')

# Configure the SQLAlchemy part of the app
DATABASE_URL = os.getenv('DATABASE_URL')
print(f"DEBUG - Database URL from env: {'set' if DATABASE_URL else 'not set'}")
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

# Initialize rate limiting (currently just guards the login POST below against
# brute-force password guessing) - see security.py for the in-memory-storage
# caveat on Vercel.
from security import limiter, configure_limiter
configure_limiter(app)

# Import and register blueprints
from api.subscription import subscription_bp
from api.email_routes import email_bp
from api.admin import admin_bp
from api.billing import billing_bp
from api.portal import portal_bp
from api.team import team_bp
from api.client_portal import client_portal_bp

app.register_blueprint(subscription_bp)
app.register_blueprint(email_bp)
app.register_blueprint(admin_bp, url_prefix='/admin')
app.register_blueprint(billing_bp)
app.register_blueprint(portal_bp)
app.register_blueprint(team_bp)
app.register_blueprint(client_portal_bp)

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

@app.before_request
def restrict_client_role_to_portal():
    """The single unconditional gate keeping a CLIENT-role login (Client.portal_user,
    see models/models.py) out of the staff app - deliberately here, not left to each
    staff route to opt into individually, since a route that forgets to check would
    otherwise leak every tenant's data to a client login.

    request.endpoint is None on a path that doesn't match any route (e.g. an
    unknown URL) - let that through so the normal 404 handling still applies
    instead of us redirecting on top of it.
    """
    if not current_user.is_authenticated or current_user.role != UserRole.CLIENT:
        return
    if request.endpoint is None:
        return
    if request.endpoint == 'static' or request.endpoint == 'logout':
        return
    if request.endpoint.startswith('client_portal.'):
        return
    return redirect(url_for('client_portal.dashboard'))

@app.route('/')
def home():
    """Home page"""
    # If user is logged in, show their active subscription
    if current_user.is_authenticated:
        # Use our retry logic for more robust database connections
        def get_subscription():
            # tenant isolation: an invited team member must see the owner
            # account's subscription/plan, not "no plan" for their own row.
            return Subscription.query.filter_by(user_id=current_user.get_owner_id(), is_active=True).first()
        
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
# methods=['POST'] scopes the limit to the credential-check submission only -
# GET (the login page itself) is never throttled, so the page always loads
# even mid-attack.
@limiter.limit("5 per minute", methods=['POST'])
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
            
            if user and user.check_password(form.password.data):
                login_user(user, remember=form.remember_me.data)
                log_event('user_login', user_id=user.id)
                flash('Login successful!', 'success')
                if user.role == UserRole.CLIENT:
                    # A CLIENT login has no destination among the staff routes,
                    # so skip next_page (unlike every other role below) and send
                    # them straight to their own portal dashboard.
                    return redirect(url_for('client_portal.dashboard'))
                next_page = request.args.get('next')
                return redirect(next_page or url_for('home'))
            else:
                print(f"DEBUG: Login failed for {form.email.data}. User exists: {user is not None}")
                flash('Login failed. Please check your email and password.', 'danger')
        except Exception as e:
            print(f"ERROR during login: {str(e)}")
            flash('An error occurred during login. Please try again.', 'danger')
    
    return render_template('auth/login.html', title='Login', form=form)

@app.route('/register', methods=['GET', 'POST'])
# Looser than login (5/min) - registration abuse isn't the priority here,
# this is just cheap insurance against a signup-spam script.
@limiter.limit("20 per hour", methods=['POST'])
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
        log_event('user_registered', user_id=user.id, role=user.role.name)

        flash('Your account has been created! You can now log in.', 'success')
        return redirect(url_for('login'))
    
    return render_template('auth/register.html', title='Register', form=form)


@app.errorhandler(429)
def rate_limit_exceeded(e):
    """Flask-Limiter raises a plain 429 when a limited route (login/register
    POST) is hit too often. Re-render the form that was being submitted with
    a clear flash instead of a raw 429 error page - a user who mistyped their
    password a few times shouldn't hit a scary/unexplained error screen."""
    if request.endpoint == 'login':
        flash('Too many login attempts - please wait a minute and try again.', 'danger')
        return render_template('auth/login.html', title='Login', form=LoginForm()), 429
    if request.endpoint == 'register':
        flash('Too many attempts - please wait a while and try again.', 'danger')
        return render_template('auth/register.html', title='Register', form=RegistrationForm()), 429
    flash('Too many requests - please wait a moment and try again.', 'danger')
    return redirect(url_for('home'))

@app.route('/logout')
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('home'))

# Logo uploads land here - see the read-only-filesystem note in profile() below
# before assuming this survives in production on Vercel.
LOGO_UPLOAD_DIR = os.path.join(app.static_folder, 'uploads', 'logos')
LOGO_ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
LOGO_MAX_BYTES = 2 * 1024 * 1024  # 2MB


def _save_business_logo(file_storage, user_id):
    """Validates and saves an uploaded logo image, returning the path to store
    in User.business_logo_url (relative to static/), or None if the upload
    was rejected (a flash message is set explaining why).

    Two checks, not one: the extension allowlist alone only catches a file
    named badly, so we also make Pillow actually decode the image data -
    that's what catches a renamed .exe or similar being passed off as a .png.
    """
    filename = file_storage.filename or ''
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext not in LOGO_ALLOWED_EXTENSIONS:
        flash('Logo must be a PNG, JPG, GIF, or WEBP image.', 'danger')
        return None

    # Check size before touching disk - seek to end to get length, then
    # rewind so save()/Image.open() below still read from the start.
    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > LOGO_MAX_BYTES:
        flash('Logo image is too large - please use a file under 2MB.', 'danger')
        return None

    try:
        from PIL import Image
        Image.open(file_storage.stream).verify()
        file_storage.stream.seek(0)
    except Exception:
        flash('That file is not a valid image.', 'danger')
        return None

    try:
        os.makedirs(LOGO_UPLOAD_DIR, exist_ok=True)
    except OSError as e:
        # See the read-only-filesystem note in profile() - on Vercel this
        # directory can't be created at request time, so degrade instead of
        # 500ing on a user just trying to save their business name.
        print(f"ERROR creating logo upload dir: {str(e)}")
        flash('Logo upload is not available on this deployment right now - your business name was still saved.', 'warning')
        return None

    safe_name = secure_filename(filename)
    unique_name = f"user{user_id}-{uuid.uuid4().hex}-{safe_name}"
    file_storage.save(os.path.join(LOGO_UPLOAD_DIR, unique_name))
    return f"uploads/logos/{unique_name}"


@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    """Let a user set the business name/logo shown to their own clients on
    the portal and in quote/invoice emails, instead of the ELIZA brand.

    business_logo_url stores a path relative to static/ (rendered via
    url_for('static', ...) in templates), not a full URL, despite the column
    name - see models/models.py.

    IMPORTANT (Vercel): the platform's filesystem is read-only/ephemeral at
    request time except /tmp - a logo saved to static/uploads/logos/ here
    will NOT persist between requests or survive a redeploy in production on
    Vercel, only in local dev or on a host with persistent disk. Same class
    of caveat as the PesaPal/email settings note in api/admin.py. Making this
    survive on Vercel needs an external storage bucket (S3 etc.), which is
    out of scope for this change.
    """
    # Business name/logo are shared tenant branding shown to clients, so an
    # invited team member reads and writes through the owner's User row
    # rather than their own - a no-op when current_user IS the owner.
    owner = User.query.get(current_user.get_owner_id())
    form = BusinessProfileForm(obj=owner)
    if form.validate_on_submit():
        owner.business_name = form.business_name.data
        logo_file = form.business_logo.data
        if logo_file and logo_file.filename:
            saved_path = _save_business_logo(logo_file, owner.id)
            if saved_path:
                owner.business_logo_url = saved_path
        # else: no new file chosen - leave the existing logo alone.
        # Bank transfer details - same owner-row pattern as business_name
        # above, not current_user's own row.
        owner.bank_name = form.bank_name.data
        owner.bank_account_name = form.bank_account_name.data
        owner.bank_account_number = form.bank_account_number.data
        owner.bank_swift_code = form.bank_swift_code.data
        owner.bank_transfer_fee = form.bank_transfer_fee.data
        db.session.add(owner)
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
            # Build from app.config['BASE_URL'] rather than url_for(_external=True):
            # the latter's scheme-guessing produced an https:// link even on the
            # plain-http local dev server (Talisman sets PREFERRED_URL_SCHEME to
            # https), which the local server can't actually serve - ERR_SSL_PROTOCOL_ERROR.
            # BASE_URL is explicit per-environment (see line ~73) and is the same
            # pattern every other notification email in this app already uses.
            reset_url = app.config['BASE_URL'].rstrip('/') + url_for('reset_password', token=user.get_reset_token())
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

@app.route('/accept-invite/<token>', methods=['GET', 'POST'])
def accept_invite(token):
    """Public accept-invite landing page for a team invite email - mirrors
    reset_password() above (no @login_required, same "invalid or expired"
    handling for a bad token), except a successful submit creates a brand
    new User + TeamMembership instead of updating an existing one."""
    if current_user.is_authenticated:
        return redirect(url_for('home'))

    invite = TeamInvite.verify_invite_token(token)
    if not invite:
        # Covers an unsigned/tampered token, an expired one, and - thanks to
        # verify_invite_token's extra status=='pending' check - one that's
        # already been accepted or revoked, all with the same message so a
        # caller can't tell which case it was.
        flash('That invite link is invalid or has expired.', 'danger')
        return redirect(url_for('login'))

    form = AcceptInviteForm()
    if form.validate_on_submit():
        # The invitee's email is the invite's own invitee_email, not a form
        # field (see forms.AcceptInviteForm) - checked here the same way
        # RegistrationForm.validate_email checks a submitted email, just
        # against the invite's fixed address instead of user input.
        if User.query.filter_by(email=invite.invitee_email).first():
            flash('An account with this email already exists. Please log in instead.', 'danger')
            return redirect(url_for('login'))

        new_user = User(
            username=form.username.data,
            email=invite.invitee_email,
            first_name=form.first_name.data,
            last_name=form.last_name.data,
            phone=form.phone.data,
            role=invite.role  # TeamInvite.role is already a UserRole enum column, not a string.
        )
        new_user.set_password(form.password.data)
        db.session.add(new_user)
        db.session.flush()  # assigns new_user.id without a separate round trip, needed below

        membership = TeamMembership(
            account_owner_id=invite.inviter_id,
            member_user_id=new_user.id,
            role=invite.role,
            is_active=True
        )
        db.session.add(membership)

        invite.status = 'accepted'
        invite.accepted_at = datetime.utcnow()

        db.session.commit()
        log_event('team_invite_accepted', user_id=new_user.id, inviter_id=invite.inviter_id)

        login_user(new_user)
        flash('Your account has been created and you have joined the team!', 'success')
        return redirect(url_for('home'))

    return render_template('auth/accept_invite.html', title='Accept Invite', form=form, invite=invite)

@app.route('/accept-client-invite/<token>', methods=['GET', 'POST'])
def accept_client_invite(token):
    """Public accept-invite landing page for a client-portal invite email -
    mirrors accept_invite() above almost exactly (same "invalid or expired"
    handling for a bad token, same race-condition re-check), except a
    successful submit links the new User to the invite's Client row
    (Client.user_id) instead of creating a TeamMembership, and lands the new
    login on the client portal rather than home() - a CLIENT-role user
    hitting home() immediately bounces via restrict_client_role_to_portal
    above."""
    if current_user.is_authenticated:
        return redirect(url_for('home'))

    invite = ClientInvite.verify_invite_token(token)
    if not invite:
        # Same generic message as accept_invite() - covers a tampered/expired
        # token and one already accepted/revoked, without telling a caller
        # which case it was.
        flash('That invite link is invalid or has expired.', 'danger')
        return redirect(url_for('login'))

    form = ClientAcceptInviteForm()
    if form.validate_on_submit():
        # The invitee's email is the invite's own invitee_email, not a form
        # field (see forms.ClientAcceptInviteForm) - checked here the same
        # way accept_invite() checks AcceptInviteForm's submission.
        if User.query.filter_by(email=invite.invitee_email).first():
            flash('An account with this email already exists. Please log in instead.', 'danger')
            return redirect(url_for('login'))

        new_user = User(
            username=form.username.data,
            email=invite.invitee_email,
            first_name=form.first_name.data,
            last_name=form.last_name.data,
            phone=form.phone.data,
            role=UserRole.CLIENT
        )
        new_user.set_password(form.password.data)
        db.session.add(new_user)
        db.session.flush()  # assigns new_user.id without a separate round trip, needed below

        # invite.client uses the relationship added in batch 1 - links this
        # new login to the one Client record the invite was scoped to.
        client = invite.client
        client.user_id = new_user.id

        invite.status = 'accepted'
        invite.accepted_at = datetime.utcnow()

        db.session.commit()
        log_event('client_invite_accepted', user_id=new_user.id, inviter_id=invite.inviter_id)

        login_user(new_user)
        flash('Your account has been created!', 'success')
        return redirect(url_for('client_portal.dashboard'))

    return render_template('auth/accept_client_invite.html', title='Accept Invite', form=form, invite=invite)

# Client Management Routes
@app.route('/clients')
@login_required
def clients():
    """List all clients"""
    # Paginated so the page doesn't have to load every client row on every
    # visit - client lists grow unbounded and .all() was pulling the whole table.
    page = request.args.get('page', 1, type=int)
    q = request.args.get('q', '').strip()
    created_after = request.args.get('created_after', '').strip()
    created_before = request.args.get('created_before', '').strip()
    # Tenant isolation: only ever list this user's own clients - every filter
    # below is ANDed onto this base query, never replacing it.
    query = Client.query.filter_by(owner_id=current_user.get_owner_id())

    if q:
        search = f"%{q}%"
        query = query.filter(db.or_(
            Client.name.ilike(search),
            Client.contact_person.ilike(search),
            Client.email.ilike(search)
        ))

    # created_after/created_before come from plain HTML date inputs
    # (YYYY-MM-DD) - invalid/unparseable input is ignored rather than
    # raising, so a malformed or hand-edited querystring just falls back to
    # the unfiltered list instead of a 500.
    if created_after:
        try:
            query = query.filter(Client.created_at >= datetime.strptime(created_after, '%Y-%m-%d'))
        except ValueError:
            pass
    if created_before:
        try:
            # +1 day so the filter includes the entire created_before day,
            # not just rows created at exactly midnight.
            query = query.filter(Client.created_at < datetime.strptime(created_before, '%Y-%m-%d') + timedelta(days=1))
        except ValueError:
            pass

    pagination = query.order_by(Client.name).paginate(page=page, per_page=20, error_out=False)
    return render_template('clients/index.html', clients=pagination.items, pagination=pagination, now=datetime.now())

@app.route('/clients/create', methods=['GET', 'POST'])
@login_required
def client_create():
    """Create a new client"""
    # owner_id scopes the email-uniqueness check to this user's own clients - see forms.py.
    form = ClientForm(owner_id=current_user.get_owner_id())
    if form.validate_on_submit():
        client = Client(
            name=form.name.data,
            contact_person=form.contact_person.data,
            email=form.email.data,
            phone=form.phone.data,
            address=form.address.data,
            notes=form.notes.data,
            owner_id=current_user.get_owner_id()  # tenant isolation: this client belongs to its creator
        )
        db.session.add(client)
        db.session.commit()
        log_event('client_created', user_id=current_user.id)

        # Log the activity
        activity = ActivityLog(
            user_id=current_user.id,
            owner_id=current_user.get_owner_id(),  # tenant isolation: activity log entries belong to the acting tenant
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
    # 404 (not 403) so a caller can't distinguish "not yours" from "doesn't exist"
    # and confirm another tenant's client id.
    if client.owner_id != current_user.get_owner_id():
        abort(404)
    # Lets the template show "invite sent, awaiting response" instead of a
    # second Invite to Portal button once one is already outstanding - see
    # client_invite_portal below, which is the only place a pending row for
    # this client_id can come from.
    pending_invite = ClientInvite.query.filter_by(client_id=client.id, status='pending').first()
    return render_template('clients/detail.html', client=client, pending_invite=pending_invite)

@app.route('/clients/<int:client_id>/invite-portal', methods=['POST'])
@login_required
def client_invite_portal(client_id):
    """Invite a client to the client portal - creates a ClientInvite and
    emails the accept link, replacing the direct-ORM-insert workaround prior
    batches used for testing."""
    client = Client.query.get_or_404(client_id)
    # 404 (not 403) so a caller can't distinguish "not yours" from "doesn't exist"
    # and confirm another tenant's client id.
    if client.owner_id != current_user.get_owner_id():
        abort(404)

    # Guard against duplicates rather than creating a second invite/user for
    # a client that already has one - both checks below no-op with a flash
    # instead of raising, since this is reachable any time the page is open
    # in two tabs or the button is double-submitted.
    if client.user_id is not None:
        flash(f'{client.name} already has portal access.', 'info')
        return redirect(url_for('client_detail', client_id=client.id))

    existing_invite = ClientInvite.query.filter_by(client_id=client.id, status='pending').first()
    if existing_invite:
        flash(f'An invite is already pending for {client.name}.', 'info')
        return redirect(url_for('client_detail', client_id=client.id))

    if not client.email:
        flash(f'{client.name} has no email address on file - add one before inviting them to the portal.', 'danger')
        return redirect(url_for('client_detail', client_id=client.id))

    client_invite = ClientInvite(
        inviter_id=current_user.id,
        client_id=client.id,
        invitee_email=client.email,
        status='pending'
    )
    db.session.add(client_invite)
    db.session.commit()

    # Build from app.config['BASE_URL'] rather than url_for(_external=True) -
    # same reasoning as reset_password_request() above and team.invite() in
    # api/team.py: the latter's scheme-guessing produces an https:// link
    # even on the plain-http local dev server (Talisman sets
    # PREFERRED_URL_SCHEME to https there), which the local server can't
    # actually serve.
    accept_url = app.config['BASE_URL'].rstrip('/') + url_for('accept_client_invite', token=client_invite.get_invite_token())
    send_client_invite_email(client_invite, accept_url)

    flash(f'Portal invite sent to {client.email}.', 'success')
    return redirect(url_for('client_detail', client_id=client.id))

@app.route('/clients/<int:client_id>/send-sms', methods=['POST'])
@login_required
def client_send_sms(client_id):
    """Send a one-off SMS to a client's phone via Africa's Talking.

    Sending only - no delivery-status tracking, no per-SMS charge. See
    api/sms.py for the "not configured" degradation if the AFRICASTALKING_*
    env vars aren't set.
    """
    client = Client.query.get_or_404(client_id)
    # 404 (not 403) so a caller can't distinguish "not yours" from "doesn't exist"
    # and confirm another tenant's client id.
    if client.owner_id != current_user.get_owner_id():
        abort(404)
    message = request.form.get('message', '').strip()

    if not message:
        flash('Enter a message before sending.', 'danger')
        return redirect(url_for('client_detail', client_id=client.id))

    result = send_sms(client.phone, message)
    flash(result['message'], 'success' if result['success'] else 'warning')
    return redirect(url_for('client_detail', client_id=client.id))

@app.route('/clients/<int:client_id>/edit', methods=['GET', 'POST'])
@login_required
def client_edit(client_id):
    """Edit an existing client"""
    client = Client.query.get_or_404(client_id)
    # 404 (not 403) so a caller can't distinguish "not yours" from "doesn't exist"
    # and confirm another tenant's client id.
    if client.owner_id != current_user.get_owner_id():
        abort(404)
    # owner_id scopes the email-uniqueness check to this user's own clients
    # (same fix as client_create() above) - see forms.py.
    form = ClientForm(client_id=client.id, obj=client, owner_id=current_user.get_owner_id())
    
    if form.validate_on_submit():
        form.populate_obj(client)
        db.session.commit()
        
        # Log the activity
        activity = ActivityLog(
            user_id=current_user.id,
            owner_id=current_user.get_owner_id(),  # tenant isolation: activity log entries belong to the acting tenant
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
    # 404 (not 403) so a caller can't distinguish "not yours" from "doesn't exist"
    # and confirm another tenant's client id.
    if client.owner_id != current_user.get_owner_id():
        abort(404)
    client_name = client.name
    
    # Check if client has associated projects
    if client.projects:
        flash(f'Cannot delete client "{client_name}" because it has associated projects. Please delete or reassign the projects first.', 'danger')
        return redirect(url_for('client_detail', client_id=client.id))
    
    # Log the activity before deleting the client
    activity = ActivityLog(
        user_id=current_user.id,
        owner_id=current_user.get_owner_id(),  # tenant isolation: activity log entries belong to the acting tenant
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
    # Paginated for the same reason as clients() - avoid loading the whole
    # projects table on every visit.
    page = request.args.get('page', 1, type=int)
    # Tenant isolation: only ever list this user's own projects.
    pagination = Project.query.filter_by(owner_id=current_user.get_owner_id()).order_by(Project.start_date.desc()).paginate(page=page, per_page=20, error_out=False)
    return render_template('projects/index.html', projects=pagination.items, pagination=pagination, now=datetime.now())

@app.route('/projects/create', methods=['GET', 'POST'])
@login_required
def project_create():
    """Create a new project"""
    # owner_id scopes the client dropdown to this user's own clients - see forms.py.
    form = ProjectForm(owner_id=current_user.get_owner_id())
    
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
                budget=form.budget.data,
                zoom_link=form.zoom_link.data,
                drive_link=form.drive_link.data,
                whatsapp_contact=form.whatsapp_contact.data,
                owner_id=current_user.get_owner_id()  # tenant isolation: this project belongs to its creator
            )
            db.session.add(project)
            db.session.commit()
            log_event('project_created', user_id=current_user.id)

            # Log the activity
            activity = ActivityLog(
                user_id=current_user.id,
                owner_id=current_user.get_owner_id(),  # tenant isolation: activity log entries belong to the acting tenant
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
    # 404 (not 403) so a caller can't distinguish "not yours" from "doesn't exist"
    # and confirm another tenant's project id.
    if project.owner_id != current_user.get_owner_id():
        abort(404)
    # KNOWN SIMPLIFICATION: there is no team/organization concept yet, so a
    # project can only be assigned to the user who owns it - the dropdown
    # intentionally offers just the current user instead of every registered
    # platform account (see TaskForm.assigned_to in forms.py for the same
    # simplification).
    users = [current_user]
    return render_template('projects/detail.html', project=project, users=users, now=datetime.now())

@app.route('/projects/<int:project_id>/edit', methods=['GET', 'POST'])
@login_required
def project_edit(project_id):
    """Edit an existing project"""
    project = Project.query.get_or_404(project_id)
    # 404 (not 403) so a caller can't distinguish "not yours" from "doesn't exist"
    # and confirm another tenant's project id.
    if project.owner_id != current_user.get_owner_id():
        abort(404)
    # owner_id scopes the client dropdown to this user's own clients - see
    # forms.py (same fix as project_create() above).
    form = ProjectForm(obj=project, owner_id=current_user.get_owner_id())
    
    if form.validate_on_submit():
        try:
            form.populate_obj(project)
            db.session.commit()
            
            # Log the activity
            activity = ActivityLog(
                user_id=current_user.id,
                owner_id=current_user.get_owner_id(),  # tenant isolation: activity log entries belong to the acting tenant
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
    # 404 (not 403) so a caller can't distinguish "not yours" from "doesn't exist"
    # and confirm another tenant's project id.
    if project.owner_id != current_user.get_owner_id():
        abort(404)
    project_title = project.title
    
    # Check if project has associated tasks
    if project.tasks:
        flash(f'Cannot delete project "{project_title}" because it has associated tasks. Please delete the tasks first.', 'danger')
        return redirect(url_for('project_detail', project_id=project.id))
    
    try:
        # Log the activity before deleting the project
        activity = ActivityLog(
            user_id=current_user.id,
            owner_id=current_user.get_owner_id(),  # tenant isolation: activity log entries belong to the acting tenant
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
    # Task has no owner_id of its own - tenant isolation is via its parent project.
    if task.project.owner_id != current_user.get_owner_id():
        abort(404)

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
        db.session.commit()  # Commit first so time_entry.id is populated for the activity log below

        # Log the activity
        activity = ActivityLog(
            user_id=current_user.id,
            owner_id=current_user.get_owner_id(),  # tenant isolation: activity log entries belong to the acting tenant
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
    # Task has no owner_id of its own - tenant isolation is via its parent project.
    if task.project.owner_id != current_user.get_owner_id():
        abort(404)

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
            owner_id=current_user.get_owner_id(),  # tenant isolation: activity log entries belong to the acting tenant
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
    # Task has no owner_id of its own - tenant isolation is via its parent project.
    if task.project.owner_id != current_user.get_owner_id():
        abort(404)
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
            db.session.commit()  # Commit first so time_entry.id is populated for the activity log below

            duration_text = f'{time_entry.duration:.2f} hours' if time_entry.duration else 'not specified (timer still running)'

            # Log the activity
            activity = ActivityLog(
                user_id=current_user.id,
                owner_id=current_user.get_owner_id(),  # tenant isolation: activity log entries belong to the acting tenant
                action_type='added',
                entity_type='time_entry',
                entity_id=time_entry.id,
                description=f'Added time entry for task: {task.title}. Duration: {duration_text}'
            )
            db.session.add(activity)
            db.session.commit()

            flash(f'Time entry added successfully. Duration: {duration_text}.', 'success')
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
    # Task has no owner_id of its own - tenant isolation is via its parent
    # project. This is separate from the author/admin check below: that one
    # guards *who may delete this specific comment*, this one guards
    # *whether this task belongs to the caller's tenant at all*.
    if task.project.owner_id != current_user.get_owner_id():
        abort(404)

    # Only allow the comment author or an admin to delete the comment
    if comment.user_id != current_user.id and current_user.role != UserRole.ADMIN:
        flash('You do not have permission to delete this comment.', 'danger')
        return redirect(url_for('task_detail', task_id=task_id))
    
    try:
        # Log the activity before deleting the comment
        activity = ActivityLog(
            user_id=current_user.id,
            owner_id=current_user.get_owner_id(),  # tenant isolation: activity log entries belong to the acting tenant
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
    # 404 (not 403) so a caller can't distinguish "not yours" from "doesn't exist"
    # and confirm another tenant's project id.
    if project.owner_id != current_user.get_owner_id():
        abort(404)

    if request.method == 'POST':
        user_id = request.form.get('user_id', type=int)
        role = request.form.get('role')
        
        if not user_id or not role:
            flash('User and role are required.', 'danger')
            return redirect(url_for('project_detail', project_id=project.id))
        
        # No team concept yet - the assign form only ever offers "myself" as a choice,
        # but that's just a dropdown restriction; a direct POST could name any user id,
        # so enforce it server-side too.
        if user_id != current_user.id:
            flash('You can only assign yourself to this project.', 'danger')
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
                owner_id=current_user.get_owner_id(),  # tenant isolation: activity log entries belong to the acting tenant
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
    # 404 (not 403) so a caller can't distinguish "not yours" from "doesn't exist"
    # and confirm another tenant's project id.
    if project.owner_id != current_user.get_owner_id():
        abort(404)
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
            owner_id=current_user.get_owner_id(),  # tenant isolation: activity log entries belong to the acting tenant
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
    # 404 (not 403) so a caller can't distinguish "not yours" from "doesn't exist"
    # and confirm another tenant's project id.
    if project.owner_id != current_user.get_owner_id():
        abort(404)
    # user=current_user populates the project/assignee dropdowns per
    # TaskForm.__init__ - without it both choices are empty and any
    # submitted value is rejected.
    form = TaskForm(project=project, user=current_user)
    
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
                owner_id=current_user.get_owner_id(),  # tenant isolation: activity log entries belong to the acting tenant
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
                    owner_id=current_user.get_owner_id(),  # tenant isolation: activity log entries belong to the acting tenant
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
    # Task has no owner_id of its own - tenant isolation is via its parent
    # project, same 404-not-403 pattern used for Client/Project/Quote/Invoice.
    if task.project.owner_id != current_user.get_owner_id():
        abort(404)
    return render_template('tasks/detail.html', task=task, now=datetime.now())

@app.route('/tasks/<int:task_id>/edit', methods=['GET', 'POST'])
@login_required
def task_edit(task_id):
    """Edit an existing task"""
    task = Task.query.get_or_404(task_id)
    # Task has no owner_id of its own - tenant isolation is via its parent
    # project. Separate from the assigned_to check further below, which
    # guards *who a task can be assigned to*, not who may edit it.
    if task.project.owner_id != current_user.get_owner_id():
        abort(404)
    # user=current_user populates the project/assignee dropdowns per
    # TaskForm.__init__ - without it both choices are empty and any
    # submitted value is rejected (same fix as task_create() above).
    form = TaskForm(obj=task, project=task.project, user=current_user)
    
    if form.validate_on_submit():
        try:
            # Store old values for comparison
            old_assignee_id = task.assignee_id
            old_status = task.status.name if task.status else None
            old_due_date = task.due_date
            
            # Check if assignment changed
            new_assignee_id = form.assigned_to.data if form.assigned_to.data != 0 else None
            # Defense in depth: same "no team concept yet" simplification as
            # TaskForm.assigned_to - a task can only be assigned to its owner, so a
            # crafted POST naming any other user id is rejected here too, even though
            # the dropdown itself only ever offers "Unassigned" or the current user.
            if new_assignee_id not in (None, current_user.id):
                flash('Tasks can only be assigned to yourself.', 'danger')
                return render_template('tasks/form.html', form=form, task=task, project=task.project, now=datetime.now())
            assignment_changed = old_assignee_id != new_assignee_id
            
            # Update task fields
            # Note: form.populate_obj(task) raises "'int' object has no attribute
            # '_sa_instance_state'" here, because the "assigned_to" form field has the
            # same name as the Task.assigned_to relationship (which expects a User
            # object, not the raw user id the field holds). Assign each field
            # explicitly instead, the same way task_create() already does.
            task.title = form.title.data
            task.description = form.description.data
            task.project_id = form.project_id.data
            task.status = form.status.data
            task.priority = form.priority.data
            task.due_date = form.due_date.data
            task.estimated_hours = form.estimated_hours.data
            task.is_billable = form.is_billable.data
            task.assigned_to = User.query.get(new_assignee_id) if new_assignee_id else None
            db.session.commit()
            
            # Log the activity
            activity = ActivityLog(
                user_id=current_user.id,
                owner_id=current_user.get_owner_id(),  # tenant isolation: activity log entries belong to the acting tenant
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
                        owner_id=current_user.get_owner_id(),  # tenant isolation: activity log entries belong to the acting tenant
                        action_type='assigned',
                        entity_type='task',
                        entity_id=task.id,
                        description=f'Assigned task to {task.assigned_to.username}'
                    )
                else:
                    assignment_activity = ActivityLog(
                        user_id=current_user.id,
                        owner_id=current_user.get_owner_id(),  # tenant isolation: activity log entries belong to the acting tenant
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

@app.route('/tasks/<int:task_id>/submit-review', methods=['POST'])
@login_required
def task_submit_review(task_id):
    """Shortcut for the assignee (or anyone who can already edit the task) to
    signal "I think this is done, please check" without opening the full edit
    form and hunting for the status dropdown. Moves the task to REVIEW rather
    than COMPLETED - review is the correct semantic here, the assignee isn't
    unilaterally closing the task, they're asking for it to be checked."""
    task = Task.query.get_or_404(task_id)
    # Task has no owner_id of its own - tenant isolation is via its parent
    # project, same 404-not-403 pattern used on every other task route.
    if task.project.owner_id != current_user.get_owner_id():
        abort(404)

    if task.status in (TaskStatus.REVIEW, TaskStatus.COMPLETED):
        flash(f'Task "{task.title}" is already {task.status.value}.', 'info')
        return redirect(url_for('task_detail', task_id=task.id))

    task.status = TaskStatus.REVIEW
    db.session.commit()

    # Log the activity - same paired user_id/owner_id pattern as every other
    # status-affecting ActivityLog call in this file.
    activity = ActivityLog(
        user_id=current_user.id,
        owner_id=current_user.get_owner_id(),  # tenant isolation: activity log entries belong to the acting tenant
        action_type='status_changed',
        entity_type='task',
        entity_id=task.id,
        description=f'Submitted task for review: {task.title}'
    )
    db.session.add(activity)
    db.session.commit()

    flash(f'Task "{task.title}" has been submitted for review.', 'success')
    return redirect(url_for('task_detail', task_id=task.id))

@app.route('/tasks/<int:task_id>/mark-complete', methods=['POST'])
@login_required
def task_mark_complete(task_id):
    """Close the loop that task_submit_review opens: a REVIEW task otherwise
    has no quick way to actually finish - only the full edit form's status
    dropdown, which isn't obvious - so it can sit in REVIEW indefinitely even
    after a client approves it via the client portal (client_approved just
    flags approval, it never touches Task.status). This is that missing
    action."""
    task = Task.query.get_or_404(task_id)
    # Task has no owner_id of its own - tenant isolation is via its parent
    # project, same 404-not-403 pattern used on every other task route.
    if task.project.owner_id != current_user.get_owner_id():
        abort(404)

    if task.status != TaskStatus.REVIEW:
        flash(f'Task "{task.title}" must be in Review before it can be marked complete.', 'info')
        return redirect(url_for('task_detail', task_id=task.id))

    task.status = TaskStatus.COMPLETED
    task.completed_at = datetime.utcnow()
    db.session.commit()

    # Log the activity - same paired user_id/owner_id pattern as every other
    # status-affecting ActivityLog call in this file.
    activity = ActivityLog(
        user_id=current_user.id,
        owner_id=current_user.get_owner_id(),  # tenant isolation: activity log entries belong to the acting tenant
        action_type='status_changed',
        entity_type='task',
        entity_id=task.id,
        description=f'Marked task as complete: {task.title}'
    )
    db.session.add(activity)
    db.session.commit()

    flash(f'Task "{task.title}" has been marked complete.', 'success')
    return redirect(url_for('task_detail', task_id=task.id))

@app.route('/tasks/<int:task_id>/delete')
@login_required
def task_delete(task_id):
    """Delete a task"""
    task = Task.query.get_or_404(task_id)
    # Task has no owner_id of its own - tenant isolation is via its parent project.
    if task.project.owner_id != current_user.get_owner_id():
        abort(404)
    project_id = task.project_id
    task_title = task.title
    
    try:
        # Log the activity before deleting the task
        activity = ActivityLog(
            user_id=current_user.id,
            owner_id=current_user.get_owner_id(),  # tenant isolation: activity log entries belong to the acting tenant
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
    # Task has no owner_id of its own - tenant isolation is via its parent project.
    if task.project.owner_id != current_user.get_owner_id():
        abort(404)
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
            owner_id=current_user.get_owner_id(),  # tenant isolation: activity log entries belong to the acting tenant
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
    # Tenant isolation: only report on this user's own projects.
    projects = Project.query.filter_by(owner_id=current_user.get_owner_id()).all()
    
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
    
    # Query tasks with optional filter. Task has no owner_id of its own, so
    # tenant isolation is via a join through Project (same pattern as
    # all_tasks()).
    base_query = Task.query.join(Project).filter(Project.owner_id == current_user.get_owner_id())
    if status_filter:
        tasks = base_query.filter(Task.status == status_filter).all()
    else:
        tasks = base_query.all()

    # Prepare data for charts
    status_counts = {}
    priority_counts = [0, 0, 0]  # Low, Medium, High
    assignee_counts = {}

    # Project-specific task counts by status
    projects = Project.query.filter_by(owner_id=current_user.get_owner_id()).all()
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
    # Tenant isolation: TimeEntry has no owner_id of its own, so scope via a
    # join through Task -> Project (same pattern as all_tasks()).
    projects = Project.query.filter_by(owner_id=current_user.get_owner_id()).all()
    time_entries = TimeEntry.query.join(Task).join(Project).filter(
        Project.owner_id == current_user.get_owner_id()
    ).order_by(TimeEntry.start_time.desc()).limit(50).all()
    
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
    for entry in TimeEntry.query.join(Task).join(Project).filter(Project.owner_id == current_user.get_owner_id()).all():
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
    # The template's "Filter" dropdown links here with ?project_id=<id> (or
    # no param for "All Projects"), but this previously always queried every
    # project regardless - the filter link changed the URL yet the page came
    # back identical either way. all_projects stays unfiltered so the
    # dropdown itself still lists every project to switch between; projects
    # (used for the Gantt chart and both summary tables) is narrowed to the
    # selected one when project_id is given.
    # Tenant isolation: only offer/report on this user's own projects.
    all_projects = Project.query.filter_by(owner_id=current_user.get_owner_id()).order_by(Project.title).all()
    project_id = request.args.get('project_id', type=int)
    if project_id:
        projects = [p for p in all_projects if p.id == project_id]
    else:
        projects = all_projects

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
                           all_projects=all_projects,
                           selected_project_id=project_id,
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
    
    # Start with base query. Task has no owner_id of its own, so tenant
    # isolation is via a join through Project.
    query = Task.query.join(Project).filter(Project.owner_id == current_user.get_owner_id())

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
    
    # Get all tasks with applied filters, paginated - .all() was loading the
    # entire (filtered) task table on every visit.
    page = request.args.get('page', 1, type=int)
    pagination = query.order_by(Task.due_date.asc()).paginate(page=page, per_page=20, error_out=False)
    tasks = pagination.items

    # Get all projects and users for filter dropdowns, scoped to this user -
    # otherwise the filter dropdowns would leak other tenants' project/user
    # names even though the task list itself is now scoped above.
    projects = Project.query.filter_by(owner_id=current_user.get_owner_id()).order_by(Project.title).all()
    # KNOWN SIMPLIFICATION: no team/organization concept yet - see
    # project_detail().
    users = [current_user]

    return render_template('tasks/all_tasks.html',
                          tasks=tasks,
                          pagination=pagination,
                          projects=projects,
                          users=users,
                          now=datetime.now())

@app.route('/tasks/create', methods=['GET', 'POST'])
@login_required
def create_task():
    """Create a new task without requiring a project_id"""
    # user=current_user populates the project/assignee dropdowns per
    # TaskForm.__init__ - without it both choices are empty and any
    # submitted value is rejected (same fix as task_create() above).
    form = TaskForm(user=current_user)

    # Get all projects for the dropdown, scoped to this user - otherwise a
    # task could be filed against another tenant's project.
    projects = Project.query.filter_by(owner_id=current_user.get_owner_id()).order_by(Project.title).all()

    # KNOWN SIMPLIFICATION: no team/organization concept yet - see
    # project_detail().
    users = [current_user]
    
    if form.validate_on_submit():
        # Defense in depth: TaskForm.project_id's choices are scoped to this
        # user's own projects (see TaskForm.__init__), so a normal submit
        # can't reach here with someone else's project - but same as the
        # assigned_to check in task_edit(), a crafted POST bypassing the
        # dropdown shouldn't be trusted to have gone through it.
        target_project = Project.query.get_or_404(form.project_id.data)
        if target_project.owner_id != current_user.get_owner_id():
            abort(404)

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
            owner_id=current_user.get_owner_id(),  # tenant isolation: activity log entries belong to the acting tenant
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
    # Tenant isolation: Task has no owner_id of its own, so scope via a join
    # through Project (same pattern as all_tasks()).
    overdue_tasks = Task.query.join(Project).filter(
        Project.owner_id == current_user.get_owner_id(),
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

@app.route('/reports/deliverables')
@login_required
def report_deliverables():
    """Deliverables report: an executive-ready compiled view of what's been
    delivered, what's overdue, and who was responsible, for one project or
    every project under one client."""
    project_id = request.args.get('project_id', type=int)
    client_id = request.args.get('client_id', type=int)

    if project_id and client_id:
        # Ambiguous scope - a report can't be about a project and a client at once.
        abort(400)

    if not project_id and not client_id:
        # No scope chosen yet - render the picker, dropdowns scoped to this
        # user's own data same as every other filter dropdown in this file.
        projects = Project.query.filter_by(owner_id=current_user.get_owner_id()).order_by(Project.title).all()
        clients = Client.query.filter_by(owner_id=current_user.get_owner_id()).order_by(Client.name).all()
        return render_template('reports/deliverables.html', report=None, projects=projects, clients=clients)

    if project_id:
        project = Project.query.get_or_404(project_id)
        # 404 (not 403) so a caller can't distinguish "not yours" from "doesn't exist"
        # and confirm another tenant's project id.
        if project.owner_id != current_user.get_owner_id():
            abort(404)
        report = build_deliverables_report(current_user.get_owner_id(), project_id=project_id)
    else:
        client = Client.query.get_or_404(client_id)
        # 404 (not 403) so a caller can't distinguish "not yours" from "doesn't exist"
        # and confirm another tenant's client id.
        if client.owner_id != current_user.get_owner_id():
            abort(404)
        report = build_deliverables_report(current_user.get_owner_id(), client_id=client_id)

    return render_template('reports/deliverables.html', report=report, now=datetime.now())

@app.route('/reports/deliverables/print')
@login_required
def report_deliverables_print():
    """Standalone printable/PDF view of the deliverables report - same
    project_id/client_id query convention and ownership checks as
    report_deliverables() above, following the same browser-print pattern
    used for quotes/invoices (see api/billing.py's quote_print/invoice_print
    and templates/print/document.html)."""
    project_id = request.args.get('project_id', type=int)
    client_id = request.args.get('client_id', type=int)

    if project_id and client_id:
        abort(400)
    if not project_id and not client_id:
        # Nothing to print without a scope - unlike the main route this has
        # no picker to fall back to.
        abort(400)

    if project_id:
        project = Project.query.get_or_404(project_id)
        # 404 (not 403), same reasoning as report_deliverables().
        if project.owner_id != current_user.get_owner_id():
            abort(404)
        report = build_deliverables_report(current_user.get_owner_id(), project_id=project_id)
    else:
        client = Client.query.get_or_404(client_id)
        if client.owner_id != current_user.get_owner_id():
            abort(404)
        report = build_deliverables_report(current_user.get_owner_id(), client_id=client_id)

    return render_template('reports/deliverables_print.html', report=report, now=datetime.now())

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
    # Tenant isolation: this CSV must only ever contain this user's own data.
    projects = Project.query.filter_by(owner_id=current_user.get_owner_id()).all()
    
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
    # Tenant isolation: Task has no owner_id of its own, so scope via a join
    # through Project - this CSV must only ever contain this user's own data.
    tasks = Task.query.join(Project).filter(Project.owner_id == current_user.get_owner_id()).all()
    
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
    # Tenant isolation: TimeEntry has no owner_id of its own, so scope via a
    # join through Task -> Project - this CSV must only ever contain this
    # user's own data.
    time_entries = TimeEntry.query.join(Task).join(Project).filter(Project.owner_id == current_user.get_owner_id()).all()
    
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
    # Tenant isolation: Task has no owner_id of its own, so scope via a join
    # through Project - this CSV must only ever contain this user's own data.
    overdue_tasks = Task.query.join(Project).filter(
        Project.owner_id == current_user.get_owner_id(),
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

def _excel_sheet_name(title, used_names):
    """Excel sheet names are capped at 31 chars and can't contain []:*?/\\ -
    project titles are free text and can hit either limit, so sanitize and
    de-dupe before handing a name to ExcelWriter (which raises on a name
    that's too long, invalid, or already used in the workbook)."""
    invalid_chars = set('[]:*?/\\')
    cleaned = ''.join(c for c in title if c not in invalid_chars).strip() or 'Project'
    base = cleaned[:31]
    name = base
    suffix_n = 2
    while name in used_names:
        suffix = f' ({suffix_n})'
        name = base[:31 - len(suffix)] + suffix
        suffix_n += 1
    return name

@app.route('/export/deliverables')
@login_required
def export_deliverables_report():
    """Export the deliverables report as an .xlsx workbook - same
    project_id/client_id query convention and ownership-check pattern as
    report_deliverables(). One sheet per project with its task table, plus a
    Summary sheet rolling up the whole client when the report is
    client-scoped."""
    project_id = request.args.get('project_id', type=int)
    client_id = request.args.get('client_id', type=int)

    if project_id and client_id:
        abort(400)
    if not project_id and not client_id:
        abort(400)

    if project_id:
        project = Project.query.get_or_404(project_id)
        # 404 (not 403), same reasoning as report_deliverables().
        if project.owner_id != current_user.get_owner_id():
            abort(404)
        report = build_deliverables_report(current_user.get_owner_id(), project_id=project_id)
        filename = f'deliverables_{secure_filename(project.title)}.xlsx'
    else:
        client = Client.query.get_or_404(client_id)
        if client.owner_id != current_user.get_owner_id():
            abort(404)
        report = build_deliverables_report(current_user.get_owner_id(), client_id=client_id)
        filename = f'deliverables_{secure_filename(client.name)}.xlsx'

    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        if report['summary']:
            summary = report['summary']
            summary_df = pd.DataFrame([{
                'Client': report['client'].name,
                'Projects': summary['total_projects'],
                'Overdue Projects': summary['overdue_projects'],
                'Late Tasks': summary['total_late_tasks'],
                'Total Hours Logged': round(sum(p['total_hours'] for p in report['projects']), 1),
            }])
            summary_df.to_excel(writer, sheet_name='Summary', index=False)

            projects_df = pd.DataFrame([{
                'Project': p['title'],
                'Status': p['status'].value,
                '% Complete': p['percent_complete'],
                'Overdue': 'Yes' if p['is_overdue'] else 'No',
                'Hours Logged': p['total_hours'],
            } for p in report['projects']])
            # startrow leaves a blank row under the block above, same sheet.
            projects_start = len(summary_df) + 2
            projects_df.to_excel(writer, sheet_name='Summary', index=False, startrow=projects_start)

            if summary['recurring_late_assignees']:
                recurring_df = pd.DataFrame([{
                    'Assignee': a['name'],
                    'Late Tasks': a['count'],
                } for a in summary['recurring_late_assignees']])
                recurring_start = projects_start + len(projects_df) + 3
                recurring_df.to_excel(writer, sheet_name='Summary', index=False, startrow=recurring_start)

        used_sheet_names = {'Summary'} if report['summary'] else set()
        for p in report['projects']:
            data = [{
                'Task': t['title'],
                'Assignee': t['assignee_name'],
                'Due Date': t['due_date'].strftime('%Y-%m-%d') if t['due_date'] else '',
                'Completed': t['completed_at'].strftime('%Y-%m-%d') if t['completed_at'] else '',
                'Status': t['status'].value,
                'Late': 'Yes' if t['is_late'] else 'No',
                'Client Approved': 'Yes' if t['client_approved'] else 'No',
                'Accountability Note': t['accountability_sentence'] or '',
            } for t in p['tasks']]
            if not data:
                # An all-empty DataFrame still needs a sheet, otherwise a
                # project with zero tasks would silently vanish from the workbook.
                data = [{'Task': 'No tasks on this project yet.'}]
            sheet_name = _excel_sheet_name(p['title'], used_sheet_names)
            used_sheet_names.add(sheet_name)
            pd.DataFrame(data).to_excel(writer, sheet_name=sheet_name, index=False)

    output.seek(0)

    response = app.response_class(
        output.getvalue(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )
    return response

@app.route('/comments/<int:comment_id>/delete')
@login_required
def comment_delete(comment_id):
    """Delete a comment directly"""
    comment = Comment.query.get_or_404(comment_id)
    # Comment has no owner_id of its own - tenant isolation is via its
    # parent task's parent project. Separate from the author/admin check
    # below: this one guards whether the comment's task even belongs to the
    # caller's tenant, regardless of who wrote the comment.
    if comment.task.project.owner_id != current_user.get_owner_id():
        abort(404)

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
# /init-db and /reset-db used to live here as plain, unauthenticated GET
# routes - anyone who found /reset-db could wipe the entire production
# database (db.drop_all() + db.create_all()) with a single request, no
# login required. Removed outright: db.create_all() only creates missing
# tables (it never alters existing ones, so it has no ongoing use once
# production tables already exist), and db.drop_all() has no legitimate
# production use case at all. If local-dev table creation is ever needed
# again, do it via a one-off script following the migrations/ convention
# (e.g. migrations/add_task_columns.py - idempotent, run manually against
# DATABASE_URL), not a live HTTP endpoint.

# NOTE: /admin/* is handled by admin_bp (api/admin.py, registered above) -
# it has the real, working implementation (correct enum comparisons, real
# subscription data, admin_required decorator). A duplicate, broken set of
# /admin/dashboard, /admin/users, /admin/subscriptions, /admin/system routes
# used to live here too (comparing current_user.role to the string 'admin',
# which is always False since role is a UserRole enum - so every admin,
# including real ones, was silently redirected away). Removed rather than
# fixed in place, since admin_bp already does this correctly.

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
