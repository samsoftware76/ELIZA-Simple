from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import StringField, PasswordField, SubmitField, BooleanField, SelectField, TextAreaField, DateField, FloatField, IntegerField, HiddenField, DateTimeField
from wtforms.validators import DataRequired, Email, EqualTo, Length, ValidationError, Optional, NumberRange, URL
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
    # Administrator is deliberately excluded from public self-registration -
    # this form has no invite/approval gate, so offering ADMIN here let any
    # anonymous visitor grant themselves full platform-admin access (every
    # user/client/project across every tenant) in one submission. Admins are
    # now only created by an existing admin via the admin panel's add_user
    # flow (api/admin.py), which is correctly gated behind @admin_required.
    role = SelectField('Role', choices=[
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

class TeamInviteForm(FlaskForm):
    """Form for an account owner to invite a team member.

    Two separate ideas here, deliberately not merged:
    - `role` is the ACCESS LEVEL - a UserRole enum value, a fixed set,
      security-relevant (see below).
    - `role_title` is a LABEL - free text, purely what the person is called
      ("Accountant", "Marketer", "Designer"). Nothing reads it to decide
      access. This is what lets an owner describe any job on their team
      without the enum having to grow a value per job title.
    """
    email = StringField('Email', validators=[DataRequired(), Email()])
    # Administrator is deliberately excluded here for the same reason it's
    # excluded from RegistrationForm.role above: UserRole.ADMIN is the exact
    # enum value api/admin.py's admin_required decorator checks for
    # platform-wide super-admin access across every tenant, not "admin of my
    # own team". Letting an account owner grant that to an invitee would let
    # them (or anyone whose email they typed) see every other customer's
    # data, not just their own.
    #
    # SelectField validates the submitted value against `choices` on its own,
    # so a hand-crafted POST of role=ADMIN is rejected as an invalid choice -
    # that check is the security control, do not relax it. Labelled "Access
    # level" in the UI to keep it distinct from the free-text title below.
    role = SelectField('Access level', choices=[
        (UserRole.PROJECT_MANAGER.name, 'Project Manager'),
        (UserRole.DEVELOPER.name, 'Developer'),
        (UserRole.SECRETARY.name, 'Secretary')
    ], validators=[DataRequired()])
    # Optional free text, max 60 to match the VARCHAR(60) columns added by
    # migrations/add_role_title.py. The template renders a <datalist> of
    # models.models.ROLE_TITLE_SUGGESTIONS beside it, but that is a typing
    # convenience only - anything the owner types is accepted, which is the
    # whole point of the field.
    role_title = StringField('Role title (optional)', validators=[Optional(), Length(max=60)])
    submit = SubmitField('Send Invite')


class RoleTitleForm(FlaskForm):
    """Owner-only inline edit of an existing member's display title.

    Same free-text/max-60 contract as TeamInviteForm.role_title above, split
    into its own tiny form so editing a title never touches the machine role:
    there is no role field here at all, so no crafted POST to this route can
    change anyone's access level. Used by team.update_role_title (api/team.py),
    which is _require_owner()-guarded.
    """
    role_title = StringField('Role title', validators=[Optional(), Length(max=60)])
    submit = SubmitField('Save')


class AcceptInviteForm(FlaskForm):
    """Form for a new team member to set up their account from an invite.

    Mirrors RegistrationForm's fields/validators, with two fields deliberately
    missing rather than just restricted:
    - No role field at all - not even a restricted dropdown like
      TeamInviteForm's above. The role for an accepted invite always comes
      from the TeamInvite row (set by the owner when they sent it), never
      from what the invitee submits, so there's nothing here for a crafted
      POST to override.
    - No email field either - the invitee's email is the invite's own
      invitee_email (that's who was invited), not something to retype. The
      route checks that email for a pre-existing account the same way
      RegistrationForm.validate_email does below, it's just done against
      invite.invitee_email in api/index.py rather than a form field here.
    """
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=50)])
    first_name = StringField('First Name', validators=[DataRequired(), Length(max=50)])
    last_name = StringField('Last Name', validators=[DataRequired(), Length(max=50)])
    phone = StringField('Phone Number', validators=[Optional(), Length(max=20)])
    password = PasswordField('Password', validators=[
        DataRequired(),
        Length(min=8, message='Password must be at least 8 characters long')
    ])
    confirm_password = PasswordField('Confirm Password', validators=[
        DataRequired(),
        EqualTo('password', message='Passwords must match')
    ])
    submit = SubmitField('Create Account')

    def validate_username(self, username):
        """Check if username already exists"""
        user = User.query.filter_by(username=username.data).first()
        if user:
            raise ValidationError('Username already exists. Please choose a different one.')


class ClientAcceptInviteForm(FlaskForm):
    """Form for a newly-invited client to set up their portal login from a
    ClientInvite. Mirrors AcceptInviteForm above field-for-field, for the
    same reasoning:
    - No role field - an accepted ClientInvite always creates a
      UserRole.CLIENT login, never something a submitted field could override.
    - No email field - the invitee's email is the invite's own
      invitee_email (that's who was invited, and it's fixed to
      Client.email at send time), not something to retype. The route
      checks that email for a pre-existing account the same way
      accept_invite() does for AcceptInviteForm, just against
      invite.invitee_email here too.
    """
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=50)])
    first_name = StringField('First Name', validators=[DataRequired(), Length(max=50)])
    last_name = StringField('Last Name', validators=[DataRequired(), Length(max=50)])
    phone = StringField('Phone Number', validators=[Optional(), Length(max=20)])
    password = PasswordField('Password', validators=[
        DataRequired(),
        Length(min=8, message='Password must be at least 8 characters long')
    ])
    confirm_password = PasswordField('Confirm Password', validators=[
        DataRequired(),
        EqualTo('password', message='Passwords must match')
    ])
    submit = SubmitField('Create Account')

    def validate_username(self, username):
        """Check if username already exists"""
        user = User.query.filter_by(username=username.data).first()
        if user:
            raise ValidationError('Username already exists. Please choose a different one.')


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

# Shared by QuoteForm/InvoiceForm below and InvoiceDefaultsForm - defined
# before the /profile card forms so InvoiceDefaultsForm can reference it at
# class-definition time.
CURRENCY_CHOICES = [('USD', 'USD'), ('UGX', 'UGX'), ('EUR', 'EUR'), ('GBP', 'GBP'), ('KES', 'KES')]

# Display-currency choices for the personal viewing preference on the
# /profile ACCOUNT card (User.display_currency) - a deliberately SEPARATE,
# wider list than CURRENCY_CHOICES above. CURRENCY_CHOICES is what a quote/
# invoice can actually be DENOMINATED in (and what PesaPal is asked to
# charge), and must not be silently widened by a display feature; this list
# only controls which approximate equivalent a user likes to see next to
# real amounts, so it can safely cover more of the world.
DISPLAY_CURRENCY_CHOICES = CURRENCY_CHOICES + [
    ('CNY', 'CNY'), ('INR', 'INR'), ('AED', 'AED'), ('ZAR', 'ZAR'),
    ('NGN', 'NGN'), ('TZS', 'TZS'), ('RWF', 'RWF'), ('CAD', 'CAD'),
    ('AUD', 'AUD'), ('JPY', 'JPY'),
]


# ---------------------------------------------------------------------------
# /profile card forms. Deliberately ONE FlaskForm class PER CARD, each POSTed
# to its own route: WTForms treats an Optional() field missing from the POST
# body as blank, so a single combined form split across multiple <form> tags
# would silently wipe whichever card's fields weren't submitted on every
# save. Separate form classes make that bug impossible - each handler only
# ever sees (and writes) its own card's fields.
# ---------------------------------------------------------------------------

class AccountDetailsForm(FlaskForm):
    """PERSONAL details card on /profile - writes to current_user's OWN row
    (a team member edits their own identity here), unlike the tenant-level
    business/payout/invoice-defaults cards which write to the owner row.
    Username and email are displayed read-only on the page, not fields here:
    changing the login email safely needs a verification flow (out of scope).
    Validators mirror RegistrationForm's for the same fields."""
    first_name = StringField('First Name', validators=[DataRequired(), Length(max=50)])
    last_name = StringField('Last Name', validators=[DataRequired(), Length(max=50)])
    phone = StringField('Phone Number', validators=[Optional(), Length(max=20)])
    # Personal display-currency preference (User.display_currency, own row -
    # correctly on THIS card, not the tenant-level invoice-defaults card).
    # Display-layer only: shows an approximate converted equivalent next to
    # real amounts on staff pages, never changes any stored amount or what
    # PesaPal charges. Choices come from DISPLAY_CURRENCY_CHOICES, the wide
    # display-only list - NOT CURRENCY_CHOICES, which stays the narrow
    # document-currency list.
    display_currency = SelectField('Display Currency', validators=[Optional()],
                                    choices=[('', 'None (off)')] + DISPLAY_CURRENCY_CHOICES,
                                    description="Show an approximate equivalent in this currency next to amounts. Display only - never changes real amounts or what clients are charged.")
    submit = SubmitField('Save Account')


class ChangePasswordForm(FlaskForm):
    """Change-password card on /profile - own row only. The current password
    is verified server-side with check_password before anything changes; min
    length 8 matches RegistrationForm."""
    current_password = PasswordField('Current Password', validators=[DataRequired()])
    new_password = PasswordField('New Password', validators=[
        DataRequired(),
        Length(min=8, message='Password must be at least 8 characters long')
    ])
    confirm_new_password = PasswordField('Confirm New Password', validators=[
        DataRequired(),
        EqualTo('new_password', message='Passwords must match')
    ])
    submit = SubmitField('Change Password')


class BusinessIdentityForm(FlaskForm):
    """Business identity card on /profile (formerly the top half of
    BusinessProfileForm) - tenant-level, written to the OWNER row via
    get_owner_id(). Branding shown to the tenant's clients on the portal and
    in quote/invoice emails instead of ELIZA's own brand (see
    User.business_name/business_logo_url).

    business_logo_url is not a form field - the column it maps to on User
    holds a path (not a full URL) written by the profile upload handler in
    api/index.py, which does its own extension/content/size checks on the
    uploaded file before touching the column."""
    business_name = StringField('Business Name', validators=[Optional(), Length(max=150)],
                                 description="Shown to your clients instead of your personal name or 'ELIZA'")
    business_logo = FileField('Logo Image', validators=[FileAllowed(['png', 'jpg', 'jpeg', 'gif', 'webp'], 'Images only (png/jpg/jpeg/gif/webp).')],
                               description="Optional: upload a logo image (max 2MB). Leave blank to keep your current logo.")
    submit = SubmitField('Save Business Identity')


class PayoutDetailsForm(FlaskForm):
    """Payout details card on /profile (formerly the bottom half of
    BusinessProfileForm) - tenant-level, written to the OWNER row via
    get_owner_id().

    Manual "pay by bank transfer" details (see User.bank_name etc. in
    models/models.py) - all optional, shown to clients on the invoice
    portal only once bank_name is set. No new payment gateway: the client
    wires the money directly and the freelancer marks the invoice paid
    themselves via the existing invoice_mark_paid route."""
    bank_name = StringField('Bank Name', validators=[Optional(), Length(max=150)],
                             description="Shown to clients who choose to pay by bank transfer")
    bank_account_name = StringField('Account Name', validators=[Optional(), Length(max=150)])
    bank_account_number = StringField('Account Number', validators=[Optional(), Length(max=50)])
    bank_swift_code = StringField('SWIFT/BIC Code', validators=[Optional(), Length(max=20)])
    bank_transfer_fee = FloatField('Bank transfer fee', validators=[Optional(), NumberRange(min=0)],
                                    description="Flat amount added to invoices paid this way, e.g. to cover your bank's incoming wire charge")
    # Mobile money payout details (see User.mobile_money_provider etc. in
    # models/models.py) - both optional, shown on the /wallet payouts card.
    # Informational only: ELIZA never holds funds, so this is where YOU
    # withdraw PesaPal collections to (from the PesaPal merchant dashboard),
    # not a payment method offered to clients.
    mobile_money_provider = SelectField('Mobile Money Provider', validators=[Optional()], choices=[
        ('', 'Not set'),
        ('MTN Mobile Money', 'MTN Mobile Money'),
        ('Airtel Money', 'Airtel Money'),
        ('M-Pesa', 'M-Pesa'),
        ('Other', 'Other'),
    ], description="Where you withdraw your PesaPal collections to")
    mobile_money_number = StringField('Mobile Money Number', validators=[Optional(), Length(max=20)])
    submit = SubmitField('Save Payout Details')


class InvoiceDefaultsForm(FlaskForm):
    """Invoice defaults card on /profile - tenant-level on the OWNER row (see
    User.default_currency etc. and migrations/add_invoice_defaults.py), but
    unlike the business/payout cards only the owner THEMSELVES may edit it
    (team members see a muted note instead). Defaults only prefill the empty
    quote/invoice forms (and the auto-invoice on quote acceptance) - explicit
    user input always wins."""
    default_currency = SelectField('Default Currency', validators=[Optional()],
                                    choices=[('', 'Not set')] + CURRENCY_CHOICES,
                                    description="Pre-selected on new quotes and invoices")
    default_tax_rate = FloatField('Default Tax Rate (%)', validators=[Optional(), NumberRange(min=0, max=100)],
                                   description="Pre-filled on new quotes and invoices; leave blank for none")
    default_payment_terms_days = IntegerField('Default Payment Terms (days)', validators=[Optional(), NumberRange(min=0, max=730)],
                                               description="New invoices get a due date this many days out; also applied when a client accepts a quote")
    submit = SubmitField('Save Invoice Defaults')


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
        # Tenant isolation: see ProjectForm.owner_id above - the uniqueness
        # check below must only look at this user's own clients, matching
        # the composite UNIQUE(owner_id, email) constraint added by
        # migrations/add_client_unsubscribe_token.py. Passed in explicitly
        # by the route rather than read from a global current_user, same as
        # every other owner_id kwarg in this file.
        self.owner_id = kwargs.pop('owner_id', None)
        super(ClientForm, self).__init__(*args, **kwargs)

    def validate_email(self, email):
        """Check if email already exists for this tenant (only if provided)"""
        if email.data:  # Only validate if email is provided
            # No owner_id passed in => no tenant context => skip the check
            # rather than querying globally, matching the "empty dropdown,
            # not every record on the platform" behavior of the other forms.
            if self.owner_id is None:
                return
            client = Client.query.filter_by(email=email.data, owner_id=self.owner_id).first()
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
    # Third-party tool links (see migrations/add_project_tool_links.py) - all
    # optional. zoom_link/drive_link get URL() since they're meant to be
    # clickable links; whatsapp_contact deliberately skips URL() since it may
    # hold a plain phone number instead of a wa.me link.
    zoom_link = StringField('Zoom Link', validators=[Optional(), URL(), Length(max=255)])
    drive_link = StringField('Drive Link', validators=[Optional(), URL(), Length(max=255)])
    whatsapp_contact = StringField('WhatsApp Contact', validators=[Optional(), Length(max=255)])
    submit = SubmitField('Save')

    def __init__(self, *args, **kwargs):
        # Tenant isolation: the client dropdown must only offer clients the
        # current user owns, or one tenant could assign their project to
        # another tenant's client record. owner_id is passed in explicitly
        # by the route (same kwargs.pop pattern as ClientForm.client_id
        # above) rather than read from a global current_user here, so the
        # scoping decision stays visible at the call site.
        self.owner_id = kwargs.pop('owner_id', None)
        super(ProjectForm, self).__init__(*args, **kwargs)
        # Populate the client dropdown, scoped to this user's own clients.
        # No owner_id passed in is treated as "no tenant context" and gets
        # an empty dropdown rather than silently falling back to every
        # client on the platform.
        if self.owner_id is not None:
            self.client_id.choices = [(c.id, c.name) for c in Client.query.filter_by(owner_id=self.owner_id).order_by(Client.name).all()]
        else:
            self.client_id.choices = []
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
        # The logged-in user, passed in explicitly by the route (see
        # ProjectForm.owner_id above for why this isn't read from a global
        # current_user here). Used both to scope the project dropdown to
        # this user's own projects and to build the assignee dropdown.
        self.user = kwargs.pop('user', None)
        super(TaskForm, self).__init__(*args, **kwargs)

        # Populate the project dropdown
        if self.project:
            self.project_id.data = self.project.id
            self.project_id.render_kw = {'readonly': True}
        # Tenant isolation: only offer this user's own account's projects,
        # otherwise a task could be filed against another tenant's project.
        # Uses get_owner_id(), not .id directly - for an invited team member
        # these differ (the member's own id vs the account they were
        # invited into), and using .id here left every team member unable
        # to create or edit a task on any shared project at all (the
        # dropdown was empty while the submitted project id was still
        # forced in, so WTForms rejected it as "Not a valid choice"). No
        # user passed in => no tenant context => empty dropdown, not "every
        # project on the platform".
        if self.user is not None:
            self.project_id.choices = [(p.id, p.title) for p in Project.query.filter_by(owner_id=self.user.get_owner_id()).order_by(Project.title).all()]
        else:
            self.project_id.choices = []

        # Populate the status dropdown
        self.status.choices = [(status.name, status.value) for status in TaskStatus]

        # KNOWN SIMPLIFICATION: there is no team/organization concept yet
        # (see the migration plan's note on this), so a task can only be
        # assigned to the user who owns it - the dropdown intentionally
        # offers just the current user instead of every registered
        # platform account, which would otherwise let one tenant assign
        # work to a user who has nothing to do with their projects.
        self.assigned_to.choices = [(0, 'Unassigned')] + ([(self.user.id, f"{self.user.username} ({self.user.email})")] if self.user is not None else [])
        
    def validate_due_date(self, due_date):
        """Validate that due date is not in the past"""
        if due_date.data and due_date.data < datetime.now().date():
            raise ValidationError('Due date cannot be in the past.')

# CURRENCY_CHOICES is defined further up (above InvoiceDefaultsForm).


class QuoteForm(FlaskForm):
    """Form for creating and editing quotes (line items are handled separately in the view)"""
    client_id = SelectField('Client', coerce=int, validators=[DataRequired()])
    project_id = SelectField('Project (optional)', coerce=int, validators=[Optional()])
    title = StringField('Quote Title', validators=[DataRequired(), Length(max=150)])
    currency = SelectField('Currency', choices=CURRENCY_CHOICES, validators=[DataRequired()])
    tax_rate = FloatField('Tax Rate (%)', validators=[Optional(), NumberRange(min=0, max=100)], default=0)
    valid_until = DateField('Valid Until', validators=[Optional()], format='%Y-%m-%d')
    notes = TextAreaField('Notes / Terms', validators=[Optional(), Length(max=2000)])
    submit = SubmitField('Save Quote')

    def __init__(self, *args, **kwargs):
        # Tenant isolation: see ProjectForm.owner_id above - a quote must
        # only be able to point at this user's own clients/projects.
        self.owner_id = kwargs.pop('owner_id', None)
        super(QuoteForm, self).__init__(*args, **kwargs)
        if self.owner_id is not None:
            self.client_id.choices = [(c.id, c.name) for c in Client.query.filter_by(owner_id=self.owner_id).order_by(Client.name).all()]
            self.project_id.choices = [(0, '— No project —')] + \
                [(p.id, p.title) for p in Project.query.filter_by(owner_id=self.owner_id).order_by(Project.title).all()]
        else:
            self.client_id.choices = []
            self.project_id.choices = [(0, '— No project —')]


class InvoiceForm(FlaskForm):
    """Form for creating and editing invoices (line items are handled separately in the view)"""
    client_id = SelectField('Client', coerce=int, validators=[DataRequired()])
    project_id = SelectField('Project (optional)', coerce=int, validators=[Optional()])
    title = StringField('Invoice Title', validators=[DataRequired(), Length(max=150)])
    currency = SelectField('Currency', choices=CURRENCY_CHOICES, validators=[DataRequired()])
    tax_rate = FloatField('Tax Rate (%)', validators=[Optional(), NumberRange(min=0, max=100)], default=0)
    due_date = DateField('Due Date', validators=[Optional()], format='%Y-%m-%d')
    notes = TextAreaField('Notes / Payment Terms', validators=[Optional(), Length(max=2000)])
    submit = SubmitField('Save Invoice')

    def __init__(self, *args, **kwargs):
        # Tenant isolation: see ProjectForm.owner_id above - an invoice must
        # only be able to point at this user's own clients/projects.
        self.owner_id = kwargs.pop('owner_id', None)
        super(InvoiceForm, self).__init__(*args, **kwargs)
        if self.owner_id is not None:
            self.client_id.choices = [(c.id, c.name) for c in Client.query.filter_by(owner_id=self.owner_id).order_by(Client.name).all()]
            self.project_id.choices = [(0, '— No project —')] + \
                [(p.id, p.title) for p in Project.query.filter_by(owner_id=self.owner_id).order_by(Project.title).all()]
        else:
            self.client_id.choices = []
            self.project_id.choices = [(0, '— No project —')]


class ContractForm(FlaskForm):
    """Form for creating and editing contracts (models/billing.py Contract).
    Editing is DRAFT-ONLY - after sending, body_snapshot is the frozen legal
    text and api/contracts.py refuses edits regardless of this form."""
    client_id = SelectField('Client', coerce=int, validators=[DataRequired()])
    project_id = SelectField('Project (optional)', coerce=int, validators=[Optional()])
    title = StringField('Contract Title', validators=[DataRequired(), Length(max=150)])
    body = TextAreaField('Contract Terms', validators=[DataRequired(), Length(max=50000)],
                         description="Plain text - line breaks are preserved. This is the text the client will sign.")
    submit = SubmitField('Save Contract')

    def __init__(self, *args, **kwargs):
        # Tenant isolation: see ProjectForm.owner_id above - a contract must
        # only be able to point at this user's own clients/projects.
        self.owner_id = kwargs.pop('owner_id', None)
        super(ContractForm, self).__init__(*args, **kwargs)
        if self.owner_id is not None:
            self.client_id.choices = [(c.id, c.name) for c in Client.query.filter_by(owner_id=self.owner_id).order_by(Client.name).all()]
            self.project_id.choices = [(0, '— No project —')] + \
                [(p.id, p.title) for p in Project.query.filter_by(owner_id=self.owner_id).order_by(Project.title).all()]
        else:
            self.client_id.choices = []
            self.project_id.choices = [(0, '— No project —')]


class TimeEntryForm(FlaskForm):
    """Form for tracking time spent on tasks"""
    task_id = HiddenField('Task ID', validators=[DataRequired()])
    start_time = DateTimeField('Start Time', format='%Y-%m-%d %H:%M', validators=[DataRequired()])
    end_time = DateTimeField('End Time', format='%Y-%m-%d %H:%M', validators=[Optional()])
    duration = FloatField('Duration (hours)', validators=[Optional(), NumberRange(min=0)])
    description = TextAreaField('Description', validators=[Optional(), Length(max=500)])
    submit = SubmitField('Save Time Entry')
    
    def validate(self, extra_validators=None):
        # Flask-WTF's validate_on_submit() calls validate(extra_validators=...)
        # (Flask-WTF 1.x / WTForms 3.x); this override previously took no
        # arguments, so every submission of this form raised
        # "TypeError: validate() got an unexpected keyword argument
        # 'extra_validators'" - a 500 on every "add time entry" request.
        if not super(TimeEntryForm, self).validate(extra_validators=extra_validators):
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
    """Form for managing users in the admin panel.

    Role choices are the real UserRole enum *names* (models/models.py) -
    the Postgres column stores names like 'ADMIN', not values like 'admin',
    so the choice values must match exactly or assigning user.role crashes.
    first_name/last_name are required here because User.first_name/last_name
    are NOT NULL columns; there's no is_active column on User (is_active is
    a read-only Flask-Login property, always True), so no toggle for it.
    """
    user_id = HiddenField('User ID')
    first_name = StringField('First Name', validators=[DataRequired(), Length(max=50)])
    last_name = StringField('Last Name', validators=[DataRequired(), Length(max=50)])
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=80)])
    email = StringField('Email', validators=[DataRequired(), Email(), Length(max=120)])
    phone = StringField('Phone', validators=[Optional(), Length(max=20)])
    password = PasswordField('Password', validators=[Optional(), Length(min=8)])
    new_password = PasswordField('New Password', validators=[Optional(), Length(min=8)])
    role = SelectField('Role', choices=[
        ('ADMIN', 'Administrator'),
        ('PROJECT_MANAGER', 'Project Manager'),
        ('DEVELOPER', 'Developer'),
        ('SECRETARY', 'Secretary'),
        ('CLIENT', 'Client'),
    ], validators=[DataRequired()])
    submit = SubmitField('Save')


class SubscriptionPlanForm(FlaskForm):
    """Form for managing subscription plans - fields match SubscriptionPlan
    (models/subscription.py) exactly: there is no flat price/duration column,
    billing is monthly/yearly, and limits are 0 = unlimited."""
    plan_id = HiddenField('Plan ID')
    name = StringField('Plan Name', validators=[DataRequired(), Length(max=50)])
    description = StringField('Description', validators=[Optional(), Length(max=255)])
    price_monthly = FloatField('Monthly Price', validators=[DataRequired(), NumberRange(min=0)])
    price_yearly = FloatField('Yearly Price', validators=[DataRequired(), NumberRange(min=0)])
    max_projects = IntegerField('Max Projects (0 = unlimited)', validators=[Optional(), NumberRange(min=0)], default=0)
    max_users = IntegerField('Max Team Members (0 = unlimited)', validators=[Optional(), NumberRange(min=0)], default=0)
    max_clients = IntegerField('Max Clients (0 = unlimited)', validators=[Optional(), NumberRange(min=0)], default=0)
    features = TextAreaField('Features', validators=[DataRequired()])
    is_active = BooleanField('Active', default=True)
    submit = SubmitField('Save')


class SubscriptionForm(FlaskForm):
    """Form for managing subscriptions - matches the real Subscription model:
    a subscription belongs to a User (an ELIZA account holder paying for the
    app), not a Client (that user's own customer) - the two were mixed up in
    the original version of this form. There's no status/cancellation_reason
    column; "status" here is a UI-only choice mapped to is_active/is_trial
    in the route handler."""
    subscription_id = HiddenField('Subscription ID')
    user_id = SelectField('User', coerce=int, validators=[DataRequired()])
    plan_id = SelectField('Plan', coerce=int, validators=[DataRequired()])
    start_date = DateField('Start Date', validators=[DataRequired()], format='%Y-%m-%d')
    end_date = DateField('End Date', validators=[DataRequired()], format='%Y-%m-%d')
    billing_cycle = SelectField('Billing Cycle', choices=[('monthly', 'Monthly'), ('yearly', 'Yearly')], validators=[DataRequired()])
    status = SelectField('Status', choices=[
        ('active', 'Active'),
        ('trial', 'Free Trial'),
        ('inactive', 'Inactive/Cancelled'),
    ], validators=[DataRequired()])
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
