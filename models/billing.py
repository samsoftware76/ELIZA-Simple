"""
Billing models (Quotes and Invoices) for the ELIZA Project Management App,
including the fields needed for the client-facing approval/payment portal
(public_token, status timestamps, PesaPal tracking fields).
"""
import secrets
from datetime import datetime
from models import db


class QuoteStatus:
    DRAFT = 'draft'
    SENT = 'sent'
    ACCEPTED = 'accepted'
    DECLINED = 'declined'

    CHOICES = [DRAFT, SENT, ACCEPTED, DECLINED]


class InvoiceStatus:
    DRAFT = 'draft'
    SENT = 'sent'
    PAID = 'paid'
    CANCELLED = 'cancelled'

    CHOICES = [DRAFT, SENT, PAID, CANCELLED]


def generate_public_token():
    """A URL-safe token used for client portal links. Not guessable, not sequential."""
    return secrets.token_urlsafe(32)


class Quote(db.Model):
    __tablename__ = 'quotes'

    id = db.Column(db.Integer, primary_key=True)
    quote_number = db.Column(db.String(20), unique=True, nullable=False)

    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    title = db.Column(db.String(150), nullable=False)
    notes = db.Column(db.Text)
    currency = db.Column(db.String(3), default='USD')
    tax_rate = db.Column(db.Float, default=0.0)  # percentage, e.g. 18 for 18%
    status = db.Column(db.String(20), default=QuoteStatus.DRAFT, nullable=False)
    valid_until = db.Column(db.Date)
    decline_reason = db.Column(db.Text)

    public_token = db.Column(db.String(64), unique=True, default=generate_public_token, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    sent_at = db.Column(db.DateTime)
    responded_at = db.Column(db.DateTime)

    client = db.relationship('Client', backref='quotes')
    project = db.relationship('Project', backref='quotes')
    created_by = db.relationship('User')
    items = db.relationship('QuoteItem', backref='quote', cascade='all, delete-orphan', order_by='QuoteItem.id')
    invoices = db.relationship('Invoice', backref='quote', lazy=True)

    @property
    def subtotal(self):
        return round(sum(item.total for item in self.items), 2)

    @property
    def tax_amount(self):
        return round(self.subtotal * (self.tax_rate or 0) / 100, 2)

    @property
    def total(self):
        return round(self.subtotal + self.tax_amount, 2)

    @property
    def is_expired(self):
        return bool(
            self.valid_until
            and self.valid_until < datetime.utcnow().date()
            and self.status == QuoteStatus.SENT
        )

    @property
    def is_respondable(self):
        """Whether the client can still accept/decline this quote via the portal."""
        return self.status == QuoteStatus.SENT and not self.is_expired

    def __repr__(self):
        return f'<Quote {self.quote_number}>'


class QuoteItem(db.Model):
    __tablename__ = 'quote_items'

    id = db.Column(db.Integer, primary_key=True)
    quote_id = db.Column(db.Integer, db.ForeignKey('quotes.id'), nullable=False)
    description = db.Column(db.String(255), nullable=False)
    quantity = db.Column(db.Float, default=1)
    unit_price = db.Column(db.Float, default=0)

    @property
    def total(self):
        return round((self.quantity or 0) * (self.unit_price or 0), 2)


class Invoice(db.Model):
    __tablename__ = 'invoices'

    id = db.Column(db.Integer, primary_key=True)
    invoice_number = db.Column(db.String(20), unique=True, nullable=False)

    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True)
    quote_id = db.Column(db.Integer, db.ForeignKey('quotes.id'), nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    title = db.Column(db.String(150), nullable=False)
    notes = db.Column(db.Text)
    currency = db.Column(db.String(3), default='USD')
    tax_rate = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(20), default=InvoiceStatus.DRAFT, nullable=False)
    due_date = db.Column(db.Date)

    public_token = db.Column(db.String(64), unique=True, default=generate_public_token, nullable=False)

    # PesaPal payment tracking - looked up by token in the callback, never by session,
    # so payment confirmation works even though the client never logs in.
    pesapal_order_tracking_id = db.Column(db.String(100))
    pesapal_merchant_reference = db.Column(db.String(100))
    payment_method = db.Column(db.String(50))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    sent_at = db.Column(db.DateTime)
    paid_at = db.Column(db.DateTime)

    client = db.relationship('Client', backref='invoices')
    project = db.relationship('Project', backref='invoices')
    created_by = db.relationship('User')
    items = db.relationship('InvoiceItem', backref='invoice', cascade='all, delete-orphan', order_by='InvoiceItem.id')

    @property
    def subtotal(self):
        return round(sum(item.total for item in self.items), 2)

    @property
    def tax_amount(self):
        return round(self.subtotal * (self.tax_rate or 0) / 100, 2)

    @property
    def total(self):
        return round(self.subtotal + self.tax_amount, 2)

    @property
    def is_overdue(self):
        return bool(
            self.due_date
            and self.due_date < datetime.utcnow().date()
            and self.status == InvoiceStatus.SENT
        )

    @property
    def is_payable(self):
        """Whether the client can pay this invoice via the portal."""
        return self.status == InvoiceStatus.SENT

    def __repr__(self):
        return f'<Invoice {self.invoice_number}>'


class ContractStatus:
    """Plain constants over Contract.status, which is a VARCHAR(20) column -
    NOT a Postgres enum type like ProjectStatus/TaskStatus/UserRole in
    models/models.py. Adding a value here therefore needs no enum DDL at all:
    the database already accepts any string that fits, so VOIDED below is a
    pure application-level addition (same as QuoteStatus/InvoiceStatus above).
    """
    DRAFT = 'draft'
    SENT = 'sent'
    SIGNED = 'signed'
    DECLINED = 'declined'
    # Withdrawn by the sender before it was signed. A terminal state: the
    # contract can no longer be signed, declined, re-sent or edited, and its
    # public link keeps rendering (so the client sees a clear "withdrawn"
    # banner rather than a confusing 404) but offers no signing UI.
    VOIDED = 'voided'

    CHOICES = [DRAFT, SENT, SIGNED, DECLINED, VOIDED]


class Contract(db.Model):
    """A contract document sent to a client for signature via the same
    tokenized no-login portal pattern as Quote/Invoice above.

    Immutability guarantee: body is editable ONLY while status is draft. At
    send time body_snapshot is frozen to a copy of body - that snapshot is
    the legal text the client actually signs, and api/contracts.py blocks
    editing once the contract has been sent. The signature/audit fields
    (signer_name, signature_image, signer_ip, signer_user_agent, signed_at)
    are written by the stage-2 portal signing flow, never by staff routes.
    """
    __tablename__ = 'contracts'

    id = db.Column(db.Integer, primary_key=True)
    contract_number = db.Column(db.String(20), unique=True, nullable=False)

    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    title = db.Column(db.String(150), nullable=False)
    # The contract terms - plain text with line breaks, rendered through the
    # |nl2br|safe chain (nl2br HTML-escapes first, see filters.py).
    body = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default=ContractStatus.DRAFT, nullable=False)

    public_token = db.Column(db.String(64), unique=True, default=generate_public_token, nullable=False)

    # Frozen copy of body taken AT SEND TIME - what the client actually signs.
    body_snapshot = db.Column(db.Text)

    # Amendment chain (see migrations/add_contract_amendments.py). A sent /
    # signed / declined contract is immutable - that is the whole point of a
    # signature - so "changing" one means issuing a NEW contract that points
    # back at the one it replaces. supersedes_id is that pointer (self-
    # referential FK, nullable: an original amends nothing), and version is
    # its position in the chain: 1 for an original, N+1 for an amendment of a
    # version-N contract. version is nullable at the DB level (additive
    # migration, DEFAULT 1 backfills existing rows) - read it through
    # version_number below, never raw, so a NULL still prints as 1.
    supersedes_id = db.Column(db.Integer, db.ForeignKey('contracts.id'), nullable=True)
    version = db.Column(db.Integer, default=1)

    sent_at = db.Column(db.DateTime)
    signed_at = db.Column(db.DateTime)
    declined_at = db.Column(db.DateTime)
    decline_reason = db.Column(db.Text)
    # When the sender withdrew this contract (status VOIDED). Never set on a
    # signed contract - api/contracts.py refuses to void one.
    voided_at = db.Column(db.DateTime)

    # Signature audit trail (stage-2 portal signing flow writes these).
    signer_name = db.Column(db.String(150))
    signature_image = db.Column(db.Text)  # data: URL PNG from a canvas, capped ~200KB at validation
    signer_ip = db.Column(db.String(64))
    signer_user_agent = db.Column(db.String(300))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # foreign_keys= is explicit on every relationship here (the
    # AmbiguousForeignKeysError lesson from ActivityLog in models/models.py):
    # a second FK to any of these tables added later must not silently break
    # every mapper in the registry.
    client = db.relationship('Client', foreign_keys=[client_id], backref='contracts')
    project = db.relationship('Project', foreign_keys=[project_id], backref='contracts')
    created_by = db.relationship('User', foreign_keys=[created_by_id])

    # Self-referential amendment link, navigable both ways:
    #   contract.supersedes     -> the older contract this one amends (or None)
    #   contract.superseded_by  -> the newer contract(s) amending this one
    # remote_side=[id] is what tells SQLAlchemy which end of contracts.id <->
    # contracts.supersedes_id is the "one" side; without it a self-referential
    # FK is ambiguous and the mapper can't be configured at all (the same
    # class of registry-wide failure as the AmbiguousForeignKeys lesson on
    # ActivityLog in models/models.py, which is also why foreign_keys= is
    # spelled out here alongside it).
    # superseded_by is a LIST, not uselist=False: nothing stops an owner
    # amending the same original twice (the original is left untouched and
    # stays amendable), and a scalar relationship would emit a warning and
    # silently drop one of them. latest_amendment below is what display code
    # uses when it wants the single newest one.
    supersedes = db.relationship(
        'Contract',
        remote_side=[id],
        foreign_keys=[supersedes_id],
        backref=db.backref('superseded_by', lazy=True),
    )

    @property
    def version_number(self):
        """This contract's version, treating a NULL as 1.

        The version column is nullable (additive migration), so every display
        and every increment goes through this instead of reading .version raw.
        """
        return self.version or 1

    @property
    def latest_amendment(self):
        """The newest contract superseding this one, or None.

        Highest version wins, id as the tiebreaker - so "Superseded by" always
        points at the most recent amendment even in the rare case an owner
        amended the same original more than once.
        """
        if not self.superseded_by:
            return None
        return sorted(self.superseded_by, key=lambda c: (c.version_number, c.id))[-1]

    @property
    def is_signable(self):
        """Whether the client can still sign/decline this contract via the portal.

        VOIDED is excluded automatically by this equality check - a withdrawn
        contract is no longer SENT - which is exactly what stops the portal
        rendering any signing UI for it. api/portal.py's sign/decline routes
        re-check this server-side rather than trusting the hidden UI.
        """
        return self.status == ContractStatus.SENT

    @property
    def is_voidable(self):
        """Whether the sender can still withdraw this contract.

        Only a SENT (i.e. not yet signed or declined) contract - a signed one
        is a legal record, and a draft can simply be deleted.
        """
        return self.status == ContractStatus.SENT

    @property
    def is_amendable(self):
        """Whether a new draft version can be cloned off this contract.

        Anything that has actually gone out to the client and reached a
        settled state: sent (awaiting signature), signed, or declined. A draft
        is still directly editable, and a voided contract was withdrawn
        deliberately - amend the version that superseded it instead.
        """
        return self.status in (ContractStatus.SENT, ContractStatus.SIGNED, ContractStatus.DECLINED)

    def __repr__(self):
        return f'<Contract {self.contract_number}>'


class InvoiceItem(db.Model):
    __tablename__ = 'invoice_items'

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoices.id'), nullable=False)
    description = db.Column(db.String(255), nullable=False)
    quantity = db.Column(db.Float, default=1)
    unit_price = db.Column(db.Float, default=0)

    @property
    def total(self):
        return round((self.quantity or 0) * (self.unit_price or 0), 2)
