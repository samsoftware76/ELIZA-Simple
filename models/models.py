from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
import enum

db = SQLAlchemy()

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

class Client(db.Model):
    __tablename__ = 'clients'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    contact_person = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True)
    phone = db.Column(db.String(20), nullable=False)
    address = db.Column(db.Text)
    notes = db.Column(db.Text)
    email_opt_out = db.Column(db.Boolean, default=False)  # For bulk email unsubscribe functionality
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    # Note: Project relationship is defined in the Project model
    
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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    client = db.relationship('Client', backref=db.backref('projects', lazy=True))
    tasks = db.relationship('Task', backref='project', lazy=True, cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<Project {self.title}>'

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
    task = db.relationship('Task', backref=db.backref('time_entries', lazy=True))
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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', backref=db.backref('activity_logs', lazy=True))
    
    def __repr__(self):
        return f'<ActivityLog {self.action_type} {self.entity_type} {self.entity_id}>'

# Subscription models are defined in models/subscription.py
