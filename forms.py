from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, BooleanField, SelectField, TextAreaField, DateField, FloatField, IntegerField, HiddenField, DateTimeField
from wtforms.validators import DataRequired, Email, EqualTo, Length, ValidationError, Optional, NumberRange
from models.models import User, Client, Project, Task
from models.subscription import Subscription, SubscriptionPlan
from models import UserRole, ProjectStatus, TaskStatus
from datetime import datetime

class LoginForm(FlaskForm):
    """Form for user login"""
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember_me = BooleanField('Remember Me')
    submit = SubmitField('Sign In')

class RegistrationForm(FlaskForm):
    """Form for user registration"""
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=50)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    first_name = StringField('First Name', validators=[DataRequired(), Length(max=50)])
    last_name = StringField('Last Name', validators=[DataRequired(), Length(max=50)])
    phone = StringField('Phone Number', validators=[Length(max=20)])
    password = PasswordField('Password', validators=[
        DataRequired(),
        Length(min=8, message='Password must be at least 8 characters long')
    ])
    confirm_password = PasswordField('Confirm Password', validators=[
        DataRequired(),
        EqualTo('password', message='Passwords must match')
    ])
    role = SelectField('Role', choices=[
        (UserRole.ADMIN.name, 'Administrator'),
        (UserRole.PROJECT_MANAGER.name, 'Project Manager'),
        (UserRole.DEVELOPER.name, 'Developer'),
        (UserRole.SECRETARY.name, 'Secretary')
    ], validators=[DataRequired()])
    submit = SubmitField('Register')

    def validate_username(self, username):
        """Check if username already exists"""
        user = User.query.filter_by(username=username.data).first()
        if user:
            raise ValidationError('Username already exists. Please choose a different one.')

    def validate_email(self, email):
        """Check if email already exists"""
        user = User.query.filter_by(email=email.data).first()
        if user:
            raise ValidationError('Email already registered. Please use a different one or reset your password.')

class PasswordResetRequestForm(FlaskForm):
    """Form for requesting password reset"""
    email = StringField('Email', validators=[DataRequired(), Email()])
    submit = SubmitField('Request Password Reset')

class PasswordResetForm(FlaskForm):
    """Form for resetting password"""
    password = PasswordField('New Password', validators=[
        DataRequired(),
        Length(min=8, message='Password must be at least 8 characters long')
    ])
    confirm_password = PasswordField('Confirm New Password', validators=[
        DataRequired(),
        EqualTo('password', message='Passwords must match')
    ])
    submit = SubmitField('Reset Password')

class ClientForm(FlaskForm):
    """Form for creating and editing clients"""
    name = StringField('Company/Client Name', validators=[DataRequired(), Length(max=100)])
    contact_person = StringField('Contact Person', validators=[DataRequired(), Length(max=100)])
    email = StringField('Email', validators=[Optional(), Email(), Length(max=120)])
    phone = StringField('Phone Number', validators=[DataRequired(), Length(max=20)])
    address = TextAreaField('Address', validators=[Optional(), Length(max=500)])
    notes = TextAreaField('Notes', validators=[Optional(), Length(max=1000)])
    submit = SubmitField('Save')
    
    def __init__(self, *args, **kwargs):
        self.client_id = kwargs.pop('client_id', None)
        super(ClientForm, self).__init__(*args, **kwargs)
    
    def validate_email(self, email):
        """Check if email already exists (only if provided)"""
        if email.data:  # Only validate if email is provided
            client = Client.query.filter_by(email=email.data).first()
            if client and (self.client_id is None or client.id != self.client_id):
                raise ValidationError('A client with this email already exists.')

class ProjectForm(FlaskForm):
    """Form for creating and editing projects"""
    title = StringField('Project Title', validators=[DataRequired(), Length(max=100)])
    description = TextAreaField('Description', validators=[Optional(), Length(max=1000)])
    client_id = SelectField('Client', coerce=int, validators=[DataRequired()])
    start_date = DateField('Start Date', validators=[DataRequired()], format='%Y-%m-%d')
    end_date = DateField('End Date', validators=[Optional()], format='%Y-%m-%d')
    status = SelectField('Status', validators=[DataRequired()])
    priority = SelectField('Priority', choices=[
        (1, 'Low'),
        (2, 'Medium'),
        (3, 'High')
    ], coerce=int, validators=[DataRequired()])
    budget = FloatField('Budget', validators=[Optional(), NumberRange(min=0)])
    submit = SubmitField('Save')
    
    def __init__(self, *args, **kwargs):
        super(ProjectForm, self).__init__(*args, **kwargs)
        # Populate the client dropdown
        self.client_id.choices = [(c.id, c.name) for c in Client.query.order_by(Client.name).all()]
        # Populate the status dropdown
        self.status.choices = [(status.name, status.value) for status in ProjectStatus]
        
    def validate_end_date(self, end_date):
        """Validate that end date is after start date if provided"""
        if end_date.data and self.start_date.data and end_date.data < self.start_date.data:
            raise ValidationError('End date must be after start date.')

class TaskForm(FlaskForm):
    """Form for creating and editing tasks"""
    title = StringField('Task Title', validators=[DataRequired(), Length(max=100)])
    description = TextAreaField('Description', validators=[Optional(), Length(max=1000)])
    project_id = SelectField('Project', coerce=int, validators=[DataRequired()])
    status = SelectField('Status', validators=[DataRequired()])
    priority = SelectField('Priority', choices=[
        (1, 'Low'),
        (2, 'Medium'),
        (3, 'High')
    ], coerce=int, validators=[DataRequired()])
    due_date = DateField('Due Date', validators=[Optional()], format='%Y-%m-%d')
    estimated_hours = FloatField('Estimated Hours', validators=[Optional(), NumberRange(min=0)])
    assigned_to = SelectField('Assign To', coerce=int, validators=[Optional()])
    is_billable = BooleanField('Billable', default=True)
    submit = SubmitField('Save')
    
    def __init__(self, *args, **kwargs):
        self.project = kwargs.pop('project', None)
        super(TaskForm, self).__init__(*args, **kwargs)
        
        # Populate the project dropdown
        if self.project:
            self.project_id.data = self.project.id
            self.project_id.render_kw = {'readonly': True}
        self.project_id.choices = [(p.id, p.title) for p in Project.query.order_by(Project.title).all()]
        
        # Populate the status dropdown
        self.status.choices = [(status.name, status.value) for status in TaskStatus]
        
        # Populate the user dropdown for assignment
        self.assigned_to.choices = [(0, 'Unassigned')] + [(u.id, f"{u.username} ({u.email})") for u in User.query.order_by(User.username).all()]
        
    def validate_due_date(self, due_date):
        """Validate that due date is not in the past"""
        if due_date.data and due_date.data < datetime.now().date():
            raise ValidationError('Due date cannot be in the past.')

class ProjectAssignmentForm(FlaskForm):
    """Form for assigning users to a project"""
    project_id = HiddenField('Project ID', validators=[DataRequired()])
    user_id = SelectField('User', coerce=int, validators=[DataRequired()])
    role = SelectField('Role', choices=[
        ('manager', 'Project Manager'),
        ('developer', 'Developer'),
        ('designer', 'Designer'),
        ('tester', 'Tester'),
        ('stakeholder', 'Stakeholder')
    ], validators=[DataRequired()])
    submit = SubmitField('Add to Project')
    
    def __init__(self, *args, **kwargs):
        super(ProjectAssignmentForm, self).__init__(*args, **kwargs)
        # Populate the user dropdown
        self.user_id.choices = [(u.id, f"{u.username} ({u.email})") for u in User.query.order_by(User.username).all()]

class TimeEntryForm(FlaskForm):
    """Form for tracking time spent on tasks"""
    task_id = HiddenField('Task ID', validators=[DataRequired()])
    start_time = DateTimeField('Start Time', format='%Y-%m-%d %H:%M', validators=[DataRequired()])
    end_time = DateTimeField('End Time', format='%Y-%m-%d %H:%M', validators=[Optional()])
    duration = FloatField('Duration (hours)', validators=[Optional(), NumberRange(min=0)])
    description = TextAreaField('Description', validators=[Optional(), Length(max=500)])
    submit = SubmitField('Save Time Entry')
    
    def validate(self):
        if not super(TimeEntryForm, self).validate():
            return False
            
        # If both end_time and duration are provided, calculate end_time from duration
        if self.end_time.data and self.duration.data:
            return True  # Both are valid, user can choose which to use
            
        # If neither end_time nor duration is provided (for starting a timer)
        if not self.end_time.data and not self.duration.data:
            return True
            
        # If only end_time is provided, make sure it's after start_time
        if self.end_time.data and not self.duration.data:
            if self.end_time.data <= self.start_time.data:
                self.end_time.errors.append('End time must be after start time')
                return False
                
        return True


# Admin Forms
class UserForm(FlaskForm):
    """Form for managing users in the admin panel"""
    user_id = HiddenField('User ID')
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=80)])
    email = StringField('Email', validators=[DataRequired(), Email(), Length(max=120)])
    password = PasswordField('Password', validators=[Optional(), Length(min=8)])
    new_password = PasswordField('New Password', validators=[Optional(), Length(min=8)])
    role = SelectField('Role', choices=[
        ('admin', 'Administrator'),
        ('manager', 'Project Manager'),
        ('developer', 'Developer'),
        ('client', 'Client')
    ], validators=[DataRequired()])
    is_active = BooleanField('Active', default=True)
    submit = SubmitField('Save')


class SubscriptionPlanForm(FlaskForm):
    """Form for managing subscription plans"""
    plan_id = HiddenField('Plan ID')
    name = StringField('Plan Name', validators=[DataRequired(), Length(max=50)])
    price = FloatField('Price', validators=[DataRequired(), NumberRange(min=0)])
    duration = IntegerField('Duration (days)', validators=[DataRequired(), NumberRange(min=1)])
    features = TextAreaField('Features', validators=[DataRequired()])
    submit = SubmitField('Save')


class SubscriptionForm(FlaskForm):
    """Form for managing subscriptions"""
    subscription_id = HiddenField('Subscription ID')
    client_id = SelectField('Client', coerce=int, validators=[DataRequired()])
    plan_id = SelectField('Plan', coerce=int, validators=[DataRequired()])
    start_date = DateField('Start Date', validators=[DataRequired()], format='%Y-%m-%d')
    end_date = DateField('End Date', validators=[DataRequired()], format='%Y-%m-%d')
    status = SelectField('Status', choices=[
        ('active', 'Active'),
        ('pending', 'Pending'),
        ('expired', 'Expired'),
        ('cancelled', 'Cancelled'),
        ('trial', 'Free Trial')
    ], validators=[DataRequired()])
    cancellation_reason = TextAreaField('Cancellation Reason', validators=[Optional()])
    submit = SubmitField('Save')

    def validate_end_date(self, end_date):
        """Validate that end date is after start date"""
        if end_date.data and self.start_date.data and end_date.data < self.start_date.data:
            raise ValidationError('End date must be after start date.')


class EmailSettingsForm(FlaskForm):
    """Form for configuring email settings"""
    mail_server = StringField('Mail Server', validators=[DataRequired()])
    mail_port = IntegerField('Mail Port', validators=[DataRequired(), NumberRange(min=1, max=65535)])
    mail_username = StringField('Mail Username', validators=[DataRequired()])
    mail_password = PasswordField('Mail Password', validators=[DataRequired()])
    mail_use_tls = BooleanField('Use TLS')
    mail_use_ssl = BooleanField('Use SSL')
    mail_default_sender = StringField('Default Sender', validators=[DataRequired(), Email()])
    submit = SubmitField('Save')


class PesapalSettingsForm(FlaskForm):
    """Form for configuring PesaPal integration"""
    pesapal_consumer_key = StringField('Consumer Key', validators=[DataRequired()])
    pesapal_consumer_secret = StringField('Consumer Secret', validators=[DataRequired()])
    pesapal_ipn_id = StringField('IPN ID', validators=[DataRequired()])
    pesapal_live_mode = BooleanField('Live Mode')
    submit = SubmitField('Save')


class AppSettingsForm(FlaskForm):
    """Form for configuring application settings"""
    app_name = StringField('Application Name', validators=[DataRequired()])
    company_name = StringField('Company Name', validators=[DataRequired()])
    company_logo = StringField('Company Logo URL', validators=[Optional()])
    timezone = SelectField('Timezone', choices=[
        ('Africa/Nairobi', 'Africa/Nairobi (EAT)'),
        ('UTC', 'UTC'),
        ('Europe/London', 'Europe/London (GMT/BST)'),
        ('America/New_York', 'America/New_York (EST/EDT)'),
        ('America/Los_Angeles', 'America/Los_Angeles (PST/PDT)')
    ], validators=[DataRequired()])
    date_format = SelectField('Date Format', choices=[
        ('%%Y-%%m-%%d', 'YYYY-MM-DD'),
        ('%%d/%%m/%%Y', 'DD/MM/YYYY'),
        ('%%m/%%d/%%Y', 'MM/DD/YYYY'),
        ('%%d-%%b-%%Y', 'DD-Mon-YYYY')
    ], validators=[DataRequired()])
    submit = SubmitField('Save')


class BulkEmailForm(FlaskForm):
    """Form for sending bulk emails to clients"""
    subject = StringField('Subject', validators=[DataRequired()])
    message = TextAreaField('Message', validators=[DataRequired()])
    include_unsubscribed = BooleanField('Include Unsubscribed Clients', default=False)
    test_email = StringField('Send Test To', validators=[Optional(), Email()])
    submit = SubmitField('Send Email')
