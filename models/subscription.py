"""
Subscription models for ELIZA Project Management App
"""
from datetime import datetime
from models import db

class SubscriptionPlan(db.Model):
    """Subscription plan model"""
    __tablename__ = 'subscription_plans'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, unique=True)
    description = db.Column(db.Text)
    price_monthly = db.Column(db.Float, nullable=False)
    price_yearly = db.Column(db.Float, nullable=False)
    max_projects = db.Column(db.Integer, default=0)  # 0 means unlimited
    max_users = db.Column(db.Integer, default=0)     # 0 means unlimited
    max_clients = db.Column(db.Integer, default=0)   # 0 means unlimited
    features = db.Column(db.Text)  # JSON string of features
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    # foreign_keys is required now that Subscription has a SECOND FK to this
    # table (Subscription.pending_plan_id) - without it SQLAlchemy can't tell
    # which column this relationship (and its `plan` backref, used everywhere)
    # should join on and raises AmbiguousForeignKeysError at mapper configure
    # time. Pinning it to plan_id keeps `subscription.plan` meaning exactly
    # what it has always meant: the plan the account is ACTUALLY on.
    subscriptions = db.relationship('Subscription', backref='plan', lazy=True,
                                    foreign_keys='Subscription.plan_id')
    
    def __repr__(self):
        return f'<SubscriptionPlan {self.name}>'

class Subscription(db.Model):
    """User subscription model"""
    __tablename__ = 'subscriptions'
    
    # These are the columns that actually exist in the database
    # Based on the database query: ['updated_at', 'user_id', 'plan_id', 'start_date', 'end_date', 'is_active', 'is_trial', 'id', 'created_at', 'billing_cycle']
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    plan_id = db.Column(db.Integer, db.ForeignKey('subscription_plans.id'), nullable=False)
    start_date = db.Column(db.DateTime, default=datetime.utcnow)
    end_date = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, default=True)
    is_trial = db.Column(db.Boolean, default=False)
    billing_cycle = db.Column(db.String(10), default='monthly')  # 'monthly' or 'yearly'
    # Pending plan change (see migrations/add_subscription_plan_change.py).
    # All nullable/additive: NULL across all three means "no plan change in
    # flight", which is every pre-existing row. Set by
    # api/subscription.py's subscribe() so that changing plan NEVER mutates the
    # plan the account is actually on until the change is legitimately due -
    # previously subscribe() deactivated the paid subscription outright and
    # dropped the customer into a fresh trial.
    #
    # Two distinct cases, told apart by pending_change_at:
    #   pending_change_at IS NULL  -> an UPGRADE awaiting payment. Applied only
    #       by payment_callback() once PesaPal confirms the money arrived.
    #   pending_change_at IS SET   -> a DOWNGRADE scheduled for the end of the
    #       already-paid-for cycle. No payment involved; applied lazily on read
    #       by utils/plan_limits.apply_due_plan_change() once that date passes,
    #       since this app has no scheduler.
    pending_plan_id = db.Column(db.Integer, db.ForeignKey('subscription_plans.id'))
    pending_billing_cycle = db.Column(db.String(10))
    pending_change_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Define relationships
    user = db.relationship('User', backref='subscriptions')
    # Explicit foreign_keys: there are now TWO FKs from this table to
    # subscription_plans (plan_id and pending_plan_id), so SQLAlchemy can't
    # pick one on its own. `plan` itself stays the backref declared on
    # SubscriptionPlan.subscriptions above, which needs the same disambiguation.
    pending_plan = db.relationship('SubscriptionPlan', foreign_keys=[pending_plan_id])
    
    def __repr__(self):
        return f'<Subscription {self.id}>'
    
    def get_trial_end_date(self):
        """Calculate the trial end date (14 days after start date)"""
        from datetime import timedelta
        return self.start_date + timedelta(days=14)
    
    def is_trial_active(self):
        """Check if the trial is still active"""
        from datetime import datetime, timedelta
        return self.is_trial and self.is_active and (datetime.utcnow() < self.get_trial_end_date())
    
    def days_left_in_trial(self):
        """Calculate days left in trial"""
        from datetime import datetime, timedelta
        if not self.is_trial or not self.is_active:
            return 0
        days_left = (self.get_trial_end_date() - datetime.utcnow()).days
        return max(0, days_left)

class Payment(db.Model):
    """Payment model for subscriptions"""
    __tablename__ = 'payments'
    
    id = db.Column(db.Integer, primary_key=True)
    subscription_id = db.Column(db.Integer, db.ForeignKey('subscriptions.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(3), default='USD')
    status = db.Column(db.String(20), default='pending')  # 'pending', 'completed', 'failed'
    payment_method = db.Column(db.String(50))
    payment_date = db.Column(db.DateTime)
    order_tracking_id = db.Column(db.String(100))
    # Note: These fields might not exist in the database yet
    # We'll handle them in the __init__ method
    # merchant_reference = db.Column(db.String(100))
    # payment_account = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __init__(self, **kwargs):
        """Custom initializer to handle missing database columns"""
        # Remove fields that might not exist in the database
        merchant_reference = kwargs.pop('merchant_reference', None)
        payment_account = kwargs.pop('payment_account', None)
        
        # Initialize with the remaining fields
        super(Payment, self).__init__(**kwargs)
        
        # Store the removed fields as instance attributes (not database columns)
        self._merchant_reference = merchant_reference
        self._payment_account = payment_account
    
    # Define relationships
    subscription = db.relationship('Subscription', backref='payments')
    
    def __repr__(self):
        return f'<Payment {self.id}>'
