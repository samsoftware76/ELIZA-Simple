from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
import enum
import secrets

db = SQLAlchemy()


def generate_client_unsubscribe_token():
    """A URL-safe token used for the public, no-auth unsubscribe link.

    Same secrets.token_urlsafe(32) pattern as generate_public_token() in
    models/billing.py, kept as its own copy rather than imported from there:
    models/billing.py does `from models import db`, which runs this package's
    __init__.py, which imports *this* module (models.models) first - so this
    module importing back from models.billing would be circular.
    """
    return secrets.token_urlsafe(32)

# Enums for status fields
class ProjectStatus(enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ON_HOLD = "on_hold"
    CANCELLED = "cancelled"

class TaskStatus(enum.Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    COMPLETED = "completed"

class UserRole(enum.Enum):
    ADMIN = "admin"
    PROJECT_MANAGER = "project_manager"
    DEVELOPER = "developer"
    SECRETARY = "secretary"
    CLIENT = "client"

# Association table for many-to-many relationship between users and projects
project_users = db.Table('project_users',
    db.Column('user_id', db.Integer, db.ForeignKey('users.id'), primary_key=True),
    db.Column('project_id', db.Integer, db.ForeignKey('projects.id'), primary_key=True)
)

class User(db.Model, UserMixin):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    first_name = db.Column(db.String(50), nullable=False)
    last_name = db.Column(db.String(50), nullable=False)
    phone = db.Column(db.String(20))
    role = db.Column(db.Enum(UserRole), nullable=False, default=UserRole.DEVELOPER)
    # Shown to clients on the portal/emails instead of the ELIZA brand, so a
    # freelancer's own clients see their business, not ours - see the
    # HoneyBook "cannot remove logo" complaint this exists to avoid.
    business_name = db.Column(db.String(150))
    business_logo_url = db.Column(db.String(500))
    # Manual "pay by bank transfer" details, shown to clients on the invoice
    # portal as an alternative to the PesaPal button (see
    # migrations/add_bank_transfer_fields.py). Tenant-level like business_name
    # above - resolved via get_owner_id() wherever read/written, not
    # necessarily current_user's own row. All nullable: bank transfer is
    # opt-in, and the portal template only shows the section once bank_name
    # is set. bank_transfer_fee is a flat amount in the invoice's own
    # currency (e.g. 40.0 = "the receiving bank charges $40 on incoming
    # wires", billed to the client on top of invoice.total) - there is no
    # payment gateway involved, the client wires the money directly and the
    # freelancer marks the invoice paid themselves via the existing
    # invoice_mark_paid route once it lands.
    bank_name = db.Column(db.String(150))
    bank_account_name = db.Column(db.String(150))
    bank_account_number = db.Column(db.String(50))
    bank_swift_code = db.Column(db.String(20))
    bank_transfer_fee = db.Column(db.Float)
    # Mobile money payout details (see migrations/add_mobile_money_fields.py).
    # Tenant-level like the bank_* fields above - resolved via get_owner_id()
    # wherever read/written, not necessarily current_user's own row. All
    # nullable: purely informational payout details shown on the /wallet
    # payouts card. ELIZA never holds funds - PesaPal collections are
    # withdrawn to this number from the PesaPal merchant dashboard, not by
    # this app. provider is one of the SelectField choices in
    # forms.BusinessProfileForm (MTN Mobile Money / Airtel Money / M-Pesa /
    # Other).
    mobile_money_provider = db.Column(db.String(30))
    mobile_money_number = db.Column(db.String(20))
    # Invoice/quote defaults (see migrations/add_invoice_defaults.py).
    # Tenant-level like business_name/bank_* above - resolved via
    # get_owner_id() wherever read, and only the owner themselves may write
    # them (the /profile invoice-defaults card is owner-only). All nullable:
    # NULL means "no default set", and these only ever prefill the initial
    # values of an empty quote/invoice form (or the auto-invoice created when
    # a client accepts a quote) - explicit user input always wins.
    # default_currency holds one of forms.CURRENCY_CHOICES' codes.
    default_currency = db.Column(db.String(10))
    default_tax_rate = db.Column(db.Float)
    default_payment_terms_days = db.Column(db.Integer)
    # PERSONAL display-currency preference (see migrations/add_display_currency.py).
    # Explicitly NOT owner-resolved, unlike business_name/bank_*/default_* above:
    # this lives on the user's OWN row and is read directly off current_user,
    # never through get_owner_id() - a team member can view amounts in their
    # own currency without changing anything about the tenant's data. Purely
    # display-layer: stored currencies, PesaPal charges, and every real amount
    # stay exactly as they are; when set, staff pages show an approximate
    # converted equivalent NEXT TO the real amount (see filters.approx_display
    # and utils/exchange_rates.py). NULL means "off" - no conversions shown.
    # Holds one of forms.DISPLAY_CURRENCY_CHOICES' codes (a deliberately wider
    # list than the document-currency CURRENCY_CHOICES).
    display_currency = db.Column(db.String(10))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    projects = db.relationship('Project', secondary=project_users, lazy='subquery',
                               backref=db.backref('team_members', lazy=True))
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_reset_token(self, expires_sec=1800):
        """A signed, time-limited token for the forgot-password flow (default 30 min)."""
        from itsdangerous import URLSafeTimedSerializer
        from flask import current_app
        s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
        return s.dumps({'user_id': self.id}, salt='password-reset')

    @staticmethod
    def verify_reset_token(token, expires_sec=1800):
        """Returns the User for a valid, unexpired token, or None."""
        from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
        from flask import current_app
        s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
        try:
            data = s.loads(token, salt='password-reset', max_age=expires_sec)
        except (BadSignature, SignatureExpired):
            return None
        return User.query.get(data.get('user_id'))
    
    def __repr__(self):
        return f'<User {self.username}>'
    
    # Flask-Login required methods
    def get_id(self):
        return str(self.id)
        
    @property
    def is_authenticated(self):
        return True
        
    @property
    def is_active(self):
        return True
        
    @property
    def is_anonymous(self):
        return False
        
    # Helper methods
    def get_full_name(self):
        return f"{self.first_name} {self.last_name}"
        
    def has_role(self, role):
        if isinstance(role, str):
            return self.role.name == role
        return self.role == role

    def get_owner_id(self):
        """The tenant id whose data this user should see.

        Team members share the inviting account's data, so current_user.id
        alone is no longer always the right value for owner_id-scoped
        queries (Client.owner_id, Project.owner_id, ...) once a user can be
        someone else's team member. Falls back to self.id for everyone else
        (including an account owner themselves), so this is a drop-in
        replacement wherever current_user.id was used as the tenant id.
        """
        membership = TeamMembership.query.filter_by(member_user_id=self.id, is_active=True).first()
        return membership.account_owner_id if membership else self.id

class Client(db.Model):
    __tablename__ = 'clients'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    contact_person = db.Column(db.String(100), nullable=False)
    # Not unique=True here anymore - email uniqueness is scoped per-tenant now
    # (see __table_args__ below), not global. migrations/add_client_unsubscribe_token.py
    # dropped the old bare clients_email_key constraint and replaced it with
    # a composite UNIQUE(owner_id, email) so two different tenants can have a
    # client with the same email without a false "already exists" error.
    email = db.Column(db.String(120))
    phone = db.Column(db.String(20), nullable=False)
    address = db.Column(db.Text)
    notes = db.Column(db.Text)
    email_opt_out = db.Column(db.Boolean, default=False)  # For bulk email unsubscribe functionality
    # Per-client unsubscribe link secret, so the unsubscribe endpoint can look
    # a client up without auth (it's a public GET link inside an email) while
    # still requiring something unguessable - a bare email address is not
    # enough, see migrations/add_client_unsubscribe_token.py. Every existing
    # row was backfilled with a unique token by that migration, so this can
    # be NOT NULL + UNIQUE from the start (unlike owner_id below). The
    # default= ensures every client created from here on gets one too,
    # not just the rows the migration backfilled.
    unsubscribe_token = db.Column(db.String(64), unique=True, default=generate_client_unsubscribe_token)
    # Tenant isolation: which user this client belongs to. Nullable because
    # existing rows were backfilled by migrations/add_tenant_ownership.py
    # rather than made NOT NULL - see that file for how the backfill owner
    # was chosen.
    owner_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    # The client's OWN login, if they've been invited (see ClientInvite
    # below) and have accepted - a completely different relationship than
    # owner_id above. owner_id points at the STAFF account that owns/manages
    # this client record; user_id points at the CLIENT's own account, so
    # exactly one User can sign in scoped to exactly this one Client record
    # (not a whole tenant's data like owner_id, and not shared access like
    # TeamMembership). Nullable - most clients have no portal login at all,
    # only ones invited via ClientInvite. UNIQUE - one User login can be tied
    # to at most one Client record.
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Composite uniqueness matching the constraint added by
    # migrations/add_client_unsubscribe_token.py (name kept identical so this
    # matches the real constraint rather than describing a different one).
    __table_args__ = (db.UniqueConstraint('owner_id', 'email', name='clients_owner_id_email_key'),)

    # Relationships
    # Note: Project relationship is defined in the Project model
    # Explicit foreign_keys= required: clients now has two FKs to users.id
    # (owner_id and user_id) - same AmbiguousForeignKeysError class of bug as
    # TeamMembership/ActivityLog above, so leaving this implicit would break
    # every mapper in the registry on the first query, not just this one.
    portal_user = db.relationship('User', foreign_keys=[user_id],
                                   backref=db.backref('client_login', lazy=True, uselist=False))

    def __repr__(self):
        return f'<Client {self.name}>'

class ProjectMember(db.Model):
    __tablename__ = 'project_members'
    
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    role = db.Column(db.String(50), nullable=False)  # manager, developer, designer, tester, stakeholder
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    project = db.relationship('Project', backref=db.backref('project_members', lazy=True))
    user = db.relationship('User', backref=db.backref('project_roles', lazy=True))
    
    # Ensure a user can only have one role per project
    __table_args__ = (db.UniqueConstraint('project_id', 'user_id', name='unique_project_user'),)

class Project(db.Model):
    __tablename__ = 'projects'
    
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date)
    status = db.Column(db.Enum(ProjectStatus), default=ProjectStatus.PENDING)
    priority = db.Column(db.Integer, default=2)  # 1=Low, 2=Medium, 3=High
    budget = db.Column(db.Float)
    # Tenant isolation: which user this project belongs to. Nullable for the
    # same reason as Client.owner_id above - see
    # migrations/add_tenant_ownership.py for the backfill of existing rows.
    owner_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    # Third-party tool links for a project - all optional/nullable, see
    # migrations/add_project_tool_links.py. String(255) is plenty for a URL
    # or a wa.me link/phone number; no format constraint at the DB layer,
    # that's enforced by ProjectForm's validators instead.
    zoom_link = db.Column(db.String(255))
    drive_link = db.Column(db.String(255))
    whatsapp_contact = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    client = db.relationship('Client', backref=db.backref('projects', lazy=True))
    tasks = db.relationship('Task', backref='project', lazy=True, cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<Project {self.title}>'

class TeamMembership(db.Model):
    __tablename__ = 'team_memberships'

    id = db.Column(db.Integer, primary_key=True)
    account_owner_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    # unique=True: a member's data has to resolve to exactly one owner account
    # (see User.get_owner_id below) - if a user could be an active member of
    # two owners at once, owner_id-scoped queries would have no single answer
    # for which account's clients/projects to show them.
    member_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True)
    role = db.Column(db.Enum(UserRole), nullable=False, default=UserRole.DEVELOPER)
    # Soft-revoke flag: kept instead of deleting the row so a removed member's
    # history (who invited them, when) isn't lost, matching this codebase's
    # general preference for auditable soft state over hard deletes.
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Two FKs to users.id on this table - foreign_keys= required on both
    # relationships below or SQLAlchemy can't tell which column each join
    # uses and raises AmbiguousForeignKeysError on the first query touching
    # any mapper in the registry (i.e. it breaks the entire app, not just
    # this model). Same issue as ActivityLog.user_id/owner_id above.
    account_owner = db.relationship('User', foreign_keys=[account_owner_id],
                                     backref=db.backref('team_memberships_owned', lazy=True))
    # uselist=False: member_user_id is unique above, so a user has at most
    # one membership row to look back at, not a list.
    member = db.relationship('User', foreign_keys=[member_user_id],
                              backref=db.backref('team_membership', lazy=True, uselist=False))

    def __repr__(self):
        return f'<TeamMembership member={self.member_user_id} owner={self.account_owner_id}>'

class TeamInvite(db.Model):
    __tablename__ = 'team_invites'

    id = db.Column(db.Integer, primary_key=True)
    inviter_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    # String, not a FK to users.id: the invited person usually doesn't have a
    # User row yet - that's the whole point of an invite - so this has to be
    # able to hold an email with no matching account until it's accepted.
    invitee_email = db.Column(db.String(120), nullable=False)
    role = db.Column(db.Enum(UserRole), nullable=False, default=UserRole.DEVELOPER)
    status = db.Column(db.String(20), default='pending')  # pending / accepted / revoked
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    accepted_at = db.Column(db.DateTime, nullable=True)

    inviter = db.relationship('User', foreign_keys=[inviter_id],
                               backref=db.backref('team_invites_sent', lazy=True))

    def get_invite_token(self, expires_sec=604800):
        """A signed, time-limited token for the accept-invite link (default 7
        days - an invite should outlast a password reset's 30 minutes, since
        the invitee isn't sitting at their inbox waiting for it)."""
        from itsdangerous import URLSafeTimedSerializer
        from flask import current_app
        s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
        return s.dumps({'invite_id': self.id}, salt='team-invite')

    @staticmethod
    def verify_invite_token(token, expires_sec=604800):
        """Returns the TeamInvite for a valid, unexpired token whose status is
        still 'pending', or None.

        The status=='pending' check is the important extra step beyond
        User.verify_reset_token's version: a signature can still be valid
        after the invite it points at was revoked or already accepted, so
        the signature alone isn't enough to let someone (re)join through it.
        """
        from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
        from flask import current_app
        s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
        try:
            data = s.loads(token, salt='team-invite', max_age=expires_sec)
        except (BadSignature, SignatureExpired):
            return None
        invite = TeamInvite.query.get(data.get('invite_id'))
        if invite and invite.status == 'pending':
            return invite
        return None

    def __repr__(self):
        return f'<TeamInvite {self.invitee_email} status={self.status}>'

class ClientInvite(db.Model):
    __tablename__ = 'client_invites'

    id = db.Column(db.Integer, primary_key=True)
    inviter_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    # Which specific client record this invite grants a login for - unlike
    # TeamInvite above (which grants access to the inviter's whole tenant),
    # a client invite is scoped to exactly one Client row, matching
    # Client.user_id.
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    # String, not a FK to users.id: the invited person usually doesn't have a
    # User row yet - that's the whole point of an invite - so this has to be
    # able to hold an email with no matching account until it's accepted.
    invitee_email = db.Column(db.String(120), nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending / accepted / revoked
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    accepted_at = db.Column(db.DateTime, nullable=True)

    inviter = db.relationship('User', foreign_keys=[inviter_id],
                               backref=db.backref('client_invites_sent', lazy=True))
    client = db.relationship('Client', foreign_keys=[client_id],
                              backref=db.backref('invites', lazy=True))

    def get_invite_token(self, expires_sec=604800):
        """A signed, time-limited token for the accept-invite link (default 7
        days - an invite should outlast a password reset's 30 minutes, since
        the invitee isn't sitting at their inbox waiting for it)."""
        from itsdangerous import URLSafeTimedSerializer
        from flask import current_app
        s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
        return s.dumps({'invite_id': self.id}, salt='client-invite')

    @staticmethod
    def verify_invite_token(token, expires_sec=604800):
        """Returns the ClientInvite for a valid, unexpired token whose status
        is still 'pending', or None.

        The status=='pending' check is the important extra step beyond
        User.verify_reset_token's version: a signature can still be valid
        after the invite it points at was revoked or already accepted, so
        the signature alone isn't enough to let someone (re)join through it.
        """
        from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
        from flask import current_app
        s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
        try:
            data = s.loads(token, salt='client-invite', max_age=expires_sec)
        except (BadSignature, SignatureExpired):
            return None
        invite = ClientInvite.query.get(data.get('invite_id'))
        if invite and invite.status == 'pending':
            return invite
        return None

    def __repr__(self):
        return f'<ClientInvite {self.invitee_email} status={self.status}>'

class TimeEntry(db.Model):
    __tablename__ = 'time_entries'
    
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('tasks.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    start_time = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    end_time = db.Column(db.DateTime)
    duration = db.Column(db.Float)  # Duration in hours
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    # cascade="all, delete-orphan" mirrors Task.comments below - without it,
    # deleting a task with existing time entries fails with a NotNullViolation
    # (SQLAlchemy tries to null out time_entries.task_id, which is NOT NULL)
    # instead of deleting the entries along with the task.
    task = db.relationship('Task', backref=db.backref('time_entries', lazy=True, cascade="all, delete-orphan"))
    user = db.relationship('User', backref=db.backref('time_entries', lazy=True))
    
    def __repr__(self):
        return f'<TimeEntry {self.id} for Task {self.task_id}>'

class Task(db.Model):
    __tablename__ = 'tasks'
    
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    assignee_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    creator_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    status = db.Column(db.Enum(TaskStatus), default=TaskStatus.TODO)
    priority = db.Column(db.Integer, default=2)  # 1=Low, 2=Medium, 3=High
    due_date = db.Column(db.Date)
    estimated_hours = db.Column(db.Float)
    actual_hours = db.Column(db.Float)
    started_at = db.Column(db.DateTime)  # When work first started on the task
    completed_at = db.Column(db.DateTime)  # When the task was marked as completed
    is_billable = db.Column(db.Boolean, default=True)
    # The actual client-facing approval flag - a real client login (see
    # Client.user_id / ClientInvite above) can mark a task approved once it's
    # REVIEW or COMPLETED. Distinct from Task.status above, which staff
    # control: a client can sign off on a task without being able to move it
    # through the staff workflow themselves.
    client_approved = db.Column(db.Boolean, default=False, nullable=False)
    client_approved_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    assigned_to = db.relationship('User', foreign_keys=[assignee_id], backref=db.backref('assigned_tasks', lazy=True))
    creator = db.relationship('User', foreign_keys=[creator_id], backref=db.backref('created_tasks', lazy=True))
    comments = db.relationship('Comment', backref='task', lazy=True, cascade="all, delete-orphan")
    # Use viewonly relationship for activity logs since there's no direct foreign key
    activity_logs = db.relationship(
        'ActivityLog',
        primaryjoin="and_(ActivityLog.entity_type=='task', foreign(ActivityLog.entity_id)==Task.id)",
        lazy=True,
        viewonly=True
    )
    
    def __repr__(self):
        return f'<Task {self.title}>'

class Comment(db.Model):
    __tablename__ = 'comments'
    
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    task_id = db.Column(db.Integer, db.ForeignKey('tasks.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    # Staff-only vs client-visible. Defaults to True (internal) as the safe
    # default - an accidental leak of an internal note to a client is worse
    # than an extra click to mark one shareable. A client-authored comment
    # (api/client_portal.py add_comment) always sets this False instead: a
    # client wrote it themselves, so there is no "internal" concept from
    # their side. See migrations/add_comment_is_internal.py.
    is_internal = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    author = db.relationship('User', backref='comments')
    
    def __repr__(self):
        return f'<Comment {self.id}>'

class ActivityLog(db.Model):
    __tablename__ = 'activity_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    action_type = db.Column(db.String(50), nullable=False)  # e.g., 'created', 'updated', 'deleted'
    entity_type = db.Column(db.String(50), nullable=False)  # e.g., 'project', 'task', 'client'
    entity_id = db.Column(db.Integer, nullable=False)
    description = db.Column(db.Text)
    # Tenant isolation: which user's data this log entry is about (the acting
    # user, same as user_id above - there's no separate actor/subject split
    # here). Nullable and NOT backfilled on existing rows: entity_type/
    # entity_id is polymorphic (task, project, client, ...) with no single
    # join back to an owner that's safe for every entity_type, so old rows
    # are left NULL rather than guessed at. See
    # migrations/add_activity_log_owner.py.
    owner_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    # foreign_keys=[user_id] is required now that owner_id (added above) is a
    # second FK to users.id on this table - without it SQLAlchemy can't tell
    # which column this relationship should join on and raises
    # AmbiguousForeignKeysError on the *first* query touching any mapper in
    # the registry (i.e. it breaks the entire app, not just ActivityLog).
    user = db.relationship('User', foreign_keys=[user_id], backref=db.backref('activity_logs', lazy=True))

    def __repr__(self):
        return f'<ActivityLog {self.action_type} {self.entity_type} {self.entity_id}>'

class AnalyticsEvent(db.Model):
    """Platform-wide product-usage event log (signups, first-client, invoice
    sent/paid, trial conversion, ...) - NOT tenant-scoped by owner_id like
    Client/Project above. The whole point is comparing behavior across every
    tenant to see where trial users drop off, so this is queried the way
    api/admin.py's dashboard() queries User/Client/Project counts: globally,
    admin-only. See utils/analytics.py for the write side.
    """
    __tablename__ = 'analytics_events'

    id = db.Column(db.Integer, primary_key=True)
    # Nullable: some events (e.g. a failed login attempt) have no known user yet.
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    event_type = db.Column(db.String(100), nullable=False)
    # Named event_metadata, not metadata - `metadata` is reserved on
    # SQLAlchemy declarative models (it's the Base's own MetaData attribute)
    # and raises if used directly as a column name. Small JSON string for
    # extra context, e.g. {"invoice_id": 5, "amount": 100}.
    event_metadata = db.Column(db.Text, nullable=True)
    # Indexed: this table is queried by date range (e.g. "signups in the last
    # 30 days" on the admin analytics dashboard).
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def __repr__(self):
        return f'<AnalyticsEvent {self.event_type} user={self.user_id}>'

# Subscription models are defined in models/subscription.py
