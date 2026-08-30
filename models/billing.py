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
