"""
Subscription routes for ELIZA Project Management App
"""
from datetime import datetime, timedelta
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, jsonify, session
from flask_login import login_required, current_user
from models import db
from models.models import User, Client
from models.subscription import SubscriptionPlan, Subscription, Payment
from api.payment import PesaPalPayment
from utils.analytics import log_event
from utils.plan_limits import cycle_length, get_active_subscription, usage_summary

# Create blueprint
subscription_bp = Blueprint('subscription', __name__)

# Initialize PesaPal payment
pesapal = PesaPalPayment()

# The three plans this app sells, in ascending price order.
#
# This used to be an if/elif chain inside subscribe() with the prices and
# limits repeated a second time in the "create the plan row if missing" branch,
# and a THIRD time hardcoded into templates/subscription/plans.html. The page
# now renders from this list, so the marketing copy, the prices used to decide
# upgrade-vs-downgrade, and the row seeded into subscription_plans can't drift
# apart. Live DB values still win where a plan row already exists (see
# _plan_card_prices below) - this is the fallback/seed, not the source of truth
# for an existing row.
#
# 'bullets' is (text, included) so the page can show a plan's exclusions the
# same way it always did. max_* follow the models/subscription.py convention:
# 0 means unlimited.
PLAN_CATALOG = [
    {
        'slug': 'basic',
        'name': 'Basic',
        'price_monthly': 9.99,
        'price_yearly': 99.99,
        'max_projects': 5,
        'max_users': 3,
        'max_clients': 10,
        'features_text': 'Basic features: Up to 5 projects, 10 clients, 3 team members',
        'highlight': False,
        'bullets': [
            ('Up to 5 projects', True),
            ('Up to 10 clients', True),
            ('3 team members', True),
            ('Basic reporting', True),
            ('Email notifications', True),
            ('Bulk email campaigns', False),
            ('Advanced analytics', False),
        ],
    },
    {
        'slug': 'professional',
        'name': 'Professional',
        'price_monthly': 19.99,
        'price_yearly': 199.99,
        'max_projects': 15,
        'max_users': 10,
        'max_clients': 30,
        'features_text': 'Advanced features: Up to 15 projects, 30 clients, 10 team members',
        'highlight': True,
        'bullets': [
            ('Up to 15 projects', True),
            ('Up to 30 clients', True),
            ('10 team members', True),
            ('Advanced reporting', True),
            ('Email notifications', True),
            ('Bulk email campaigns', True),
            ('Advanced analytics', False),
        ],
    },
    {
        'slug': 'enterprise',
        'name': 'Enterprise',
        'price_monthly': 49.99,
        'price_yearly': 499.99,
        'max_projects': 0,   # Unlimited
        'max_users': 0,      # Unlimited
        'max_clients': 0,    # Unlimited
        'features_text': 'Enterprise features: Unlimited projects, clients, and team members',
        'highlight': False,
        'bullets': [
            ('Unlimited projects', True),
            ('Unlimited clients', True),
            ('Unlimited team members', True),
            ('Advanced reporting', True),
            ('Email notifications', True),
            ('Bulk email campaigns', True),
            ('Advanced analytics', True),
        ],
    },
]

PLAN_BY_SLUG = {entry['slug']: entry for entry in PLAN_CATALOG}

VALID_CYCLES = ('monthly', 'yearly')


def _require_owner():
    """Only the account owner may change the plan.

    Subscriptions hang off the OWNER's user row (Subscription.user_id ==
    get_owner_id() everywhere it is read - see home() and
    inject_sidebar_plan() in api/index.py), so a team member "subscribing"
    would silently create or mutate their owner's billing. Mirrors
    api/team.py's _require_owner() flash+redirect shape exactly, except this
    redirects to the plans page rather than home: plans() itself does NOT call
    this helper (a team member is allowed to LOOK at the page and see the
    account's usage), so there is no redirect loop.

    Returns a redirect response if the current user isn't the owner, else None.
    """
    if current_user.id != current_user.get_owner_id():
        flash('Your account owner manages billing - only they can change the subscription plan.', 'danger')
        return redirect(url_for('subscription.plans'))
    return None


def _get_or_create_plan(entry):
    """The SubscriptionPlan row for a catalog entry, seeding it if absent.

    Same lazy-seed behaviour subscribe() has always had - the plans table is
    populated on first use rather than by a fixture - just moved out of the
    route and driven off PLAN_CATALOG instead of a duplicated if/elif chain.
    """
    plan = SubscriptionPlan.query.filter_by(name=entry['name']).first()
    if plan:
        return plan
    plan = SubscriptionPlan(
        name=entry['name'],
        description=f"{entry['name']} subscription plan",
        price_monthly=entry['price_monthly'],
        price_yearly=entry['price_yearly'],
        max_projects=entry['max_projects'],
        max_users=entry['max_users'],
        max_clients=entry['max_clients'],
        features=entry['features_text'],
    )
    db.session.add(plan)
    db.session.commit()
    return plan


def _plan_card_prices(entry):
    """(plan_row_or_None, price_monthly, price_yearly) for a catalog entry.

    Reads the live subscription_plans row when there is one so the page never
    advertises a price different from the one the account is compared against,
    and falls back to the catalog for a plan not yet seeded.
    """
    plan = SubscriptionPlan.query.filter_by(name=entry['name']).first()
    if plan:
        return plan, plan.price_monthly, plan.price_yearly
    return None, entry['price_monthly'], entry['price_yearly']


def _plan_amount(plan, billing_cycle, currency):
    """Amount to charge for a plan on a cycle, in the requested currency.

    Same arithmetic (and the same rough hardcoded UGX rate) trial_payment has
    always used, pulled out so the plan-change checkout charges identically.
    """
    amount = plan.price_monthly if billing_cycle == 'monthly' else plan.price_yearly
    if currency == 'UGX':
        # Simple conversion rate (1 USD = 3700 UGX approx)
        amount = amount * 3700
    return amount


def _start_pesapal_checkout(subscription, plan, billing_cycle, currency, description):
    """Create a PesaPal order and return its redirect URL, or None.

    Extracted from trial_payment() so the trial-conversion checkout and the
    paid plan-change checkout create the order, stash session['pending_payment']
    and hand back to PesaPal in exactly one way. payment_callback() below reads
    that session key, so there must only be one writer of it.
    """
    amount = _plan_amount(plan, billing_cycle, currency)
    current_app.logger.info(f"Calculated amount: {amount} {currency}")

    # Generate a unique order ID
    order_id = f"ELIZA-SUB-{subscription.id}-{int(datetime.now().timestamp())}"

    # Create callback URL
    callback_url = url_for('subscription.payment_callback', _external=True)
    current_app.logger.info(f"Callback URL: {callback_url}")

    # Get user details for payment
    user_email = current_user.email
    user_phone = getattr(current_user, 'phone', '') or ''
    user_first_name = getattr(current_user, 'first_name', '') or current_user.username
    user_last_name = getattr(current_user, 'last_name', '') or ''

    payment_data = pesapal.create_payment_order(
        amount=amount,
        description=description,
        email=user_email,
        phone=user_phone,
        first_name=user_first_name,
        last_name=user_last_name,
        callback_url=callback_url,
        currency=currency
    )

    current_app.logger.info(f"PesaPal payment response: {payment_data}")

    if not payment_data or 'redirect_url' not in payment_data:
        return None

    # Store payment details in session for the callback to correlate. (The
    # Payment row is still not written here - the payments table is missing
    # columns the model expects; that predates this change and is unrelated to
    # the plan-change work.)
    try:
        session['pending_payment'] = {
            'subscription_id': subscription.id,
            'amount': amount,
            'currency': currency,
            'order_tracking_id': payment_data.get('order_tracking_id'),
            'merchant_reference': payment_data.get('merchant_reference') or order_id,
        }
        current_app.logger.info(f"Stored payment details in session: {session['pending_payment']}")
    except Exception as e:
        current_app.logger.warning(f"Could not store payment in session: {str(e)}")

    return payment_data['redirect_url']


@subscription_bp.route('/plans')
def plans():
    """View subscription plans, aware of the plan the account is already on.

    Stays open to anonymous visitors (it is the public pricing page, linked
    from the marketing site and the sidebar alike) - everything plan-aware is
    behind the is_authenticated check and simply doesn't render for a visitor,
    who sees the old "Choose Monthly/Yearly" page.

    A team MEMBER sees the page and the account's real usage (it is their
    account's usage too) but gets the "your account owner manages billing"
    note instead of buttons. That is presentation only: subscribe() and
    change_plan() below both call _require_owner() themselves.
    """
    subscription = None
    usage = None
    is_owner = True
    pending_plan_name = None

    if current_user.is_authenticated:
        owner_id = current_user.get_owner_id()
        is_owner = current_user.id == owner_id
        usage = usage_summary(owner_id)
        subscription = usage['subscription']
        if subscription and subscription.pending_plan_id and subscription.pending_plan:
            pending_plan_name = subscription.pending_plan.name

    # Compare by price_monthly, per the plan-change rules in subscribe():
    # a dearer plan is an Upgrade, a cheaper one a Downgrade.
    current_plan_name = subscription.plan.name if subscription and subscription.plan else None
    current_price = subscription.plan.price_monthly if subscription and subscription.plan else None

    cards = []
    for entry in PLAN_CATALOG:
        plan_row, price_monthly, price_yearly = _plan_card_prices(entry)
        is_current = (current_plan_name is not None and entry['name'] == current_plan_name)
        if current_price is None or is_current:
            action = 'choose'
        elif price_monthly > current_price:
            action = 'upgrade'
        elif price_monthly < current_price:
            action = 'downgrade'
        else:
            # Same price, different plan - neither an upgrade nor a downgrade.
            action = 'switch'
        cards.append({
            'slug': entry['slug'],
            'name': entry['name'],
            'price_monthly': price_monthly,
            'price_yearly': price_yearly,
            'bullets': entry['bullets'],
            'highlight': entry['highlight'],
            'is_current': is_current,
            'action': action,
            'action_label': {
                'choose': 'Choose',
                'upgrade': 'Upgrade',
                'downgrade': 'Downgrade',
                'switch': 'Switch',
            }[action],
            'is_pending': (pending_plan_name is not None and entry['name'] == pending_plan_name),
        })

    return render_template('subscription/plans.html',
                           cards=cards,
                           subscription=subscription,
                           usage=usage,
                           is_owner=is_owner,
                           pending_plan_name=pending_plan_name)


@subscription_bp.route('/subscribe/<plan>/<cycle>')
@login_required
def subscribe(plan, cycle):
    """Start a trial, switch plan during a trial, or route a PAID plan change.

    Three cases, and only the first one writes a new subscription row:

    1. No active subscription (or the only one has expired): unchanged from
       before - start the 14-day free trial. This is the new-customer path.

    2. Active TRIAL, different plan/cycle: stay in the trial, just switch
       plan_id/billing_cycle. The ORIGINAL start_date is deliberately KEPT, so
       the 14-day clock (Subscription.get_trial_end_date() is start_date + 14)
       is not silently reset - switching plans during a trial must not hand out
       a second free fortnight, and the customer gets what remains of the trial
       they started. end_date is re-derived from the (possibly new) cycle off
       that same start_date so the row stays internally consistent.

    3. Active PAID subscription: this route now mutates NOTHING. It redirects
       to change_plan() below, which confirms the change behind a CSRF-
       protected POST. This is the regression being fixed: previously this
       route set is_active=False on the paid subscription and created a fresh
       trial, so a paying customer who clicked another plan lost the plan they
       had paid for.
    """
    entry = PLAN_BY_SLUG.get(plan)
    if not entry:
        flash('Invalid subscription plan.', 'danger')
        return redirect(url_for('subscription.plans'))
    if cycle not in VALID_CYCLES:
        # Previously any string was accepted here and anything that wasn't
        # 'monthly' silently became a 365-day period.
        flash('Invalid billing cycle.', 'danger')
        return redirect(url_for('subscription.plans'))

    not_owner = _require_owner()
    if not_owner:
        return not_owner

    owner_id = current_user.get_owner_id()
    subscription_plan = _get_or_create_plan(entry)
    existing = get_active_subscription(owner_id)

    # --- Case 3: an active PAID plan. Never destructive, never immediate.
    if existing and not existing.is_trial and not _is_expired(existing):
        if existing.plan_id == subscription_plan.id and existing.billing_cycle == cycle:
            flash(f'You are already on the {entry["name"]} plan, billed {cycle}.', 'info')
            return redirect(url_for('subscription.plans'))
        return redirect(url_for('subscription.change_plan', plan=plan, cycle=cycle))

    # --- Case 2: an active trial switching plan/cycle.
    if existing and existing.is_trial and not _is_expired(existing):
        if existing.plan_id == subscription_plan.id and existing.billing_cycle == cycle:
            flash(f'Your trial is already on the {entry["name"]} plan, billed {cycle}.', 'info')
            return redirect(url_for('subscription.plans'))
        try:
            existing.plan_id = subscription_plan.id
            existing.billing_cycle = cycle
            # start_date untouched on purpose - see the docstring above.
            existing.end_date = existing.start_date + timedelta(days=cycle_length(cycle))
            db.session.commit()
            log_event('subscription_trial_plan_switched', user_id=current_user.id, plan=entry['name'])
            trial_end_date = existing.get_trial_end_date()
            flash(
                f'Your trial has been switched to the {entry["name"]} plan. Your trial still ends on '
                f'{trial_end_date.strftime("%B %d, %Y")} - switching plans does not restart it.',
                'success')
            return redirect(url_for('subscription.plans'))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error switching trial plan: {str(e)}")
            flash(f'Error switching plan: {str(e)}', 'danger')
            return redirect(url_for('subscription.plans'))

    # --- Case 1: no active subscription, or only an expired one. Unchanged
    # behaviour: start the 14-day trial.
    try:
        start_date = datetime.utcnow()
        end_date = start_date + timedelta(days=cycle_length(cycle))

        if existing:
            # Retire the expired row. Done in the SAME transaction as the new
            # row below (one commit, not two) so a failure can never leave the
            # account with no subscription at all.
            existing.is_active = False

        subscription = Subscription(
            user_id=owner_id,
            plan_id=subscription_plan.id,
            start_date=start_date,
            end_date=end_date,
            is_active=True,
            is_trial=True,
            billing_cycle=cycle
        )
        db.session.add(subscription)
        db.session.commit()
        log_event('subscription_trial_started', user_id=current_user.id, plan=entry['name'])

        # Calculate trial end date for the flash message
        trial_end_date = subscription.get_trial_end_date()
        flash(f'Your 14-day free trial of the {entry["name"]} plan has started! You will be asked to provide payment details before {trial_end_date.strftime("%B %d, %Y")} to continue using the service.', 'success')
        return redirect(url_for('home'))
    except Exception as e:
        db.session.rollback()
        flash(f'Error starting trial: {str(e)}', 'danger')
        return redirect(url_for('subscription.plans'))


def _is_expired(subscription):
    """Has this subscription's period already run out?

    A trial expires 14 days after start_date (the definition
    Subscription.is_trial_active() already uses); a paid subscription expires
    at end_date. An expired row is treated by subscribe() as "no subscription",
    which is the pre-existing behaviour - clicking a plan with a long-dead
    trial still starts a fresh trial exactly as it did before.
    """
    if subscription is None:
        return True
    if subscription.is_trial:
        return datetime.utcnow() >= subscription.get_trial_end_date()
    return subscription.end_date is not None and datetime.utcnow() >= subscription.end_date


@subscription_bp.route('/subscription/change/<plan>/<cycle>', methods=['GET', 'POST'])
@login_required
def change_plan(plan, cycle):
    """Change the plan on an ACTIVE PAID subscription - confirm, then act.

    GET renders the confirmation; POST (CSRF-protected by the form, like every
    other POST in the app) performs it. Nothing is mutated on the GET, and the
    existing paid subscription is never cancelled by either.

    Two outcomes, decided by price_monthly:

      * UPGRADE (dearer plan): redirect into the existing PesaPal flow for the
        new plan/cycle. The target is parked on the row as pending_plan_id /
        pending_billing_cycle with pending_change_at NULL, and only
        payment_callback() moves it onto plan_id once PesaPal confirms the
        payment. Until then the customer keeps exactly the plan they have.

      * DOWNGRADE (cheaper plan): scheduled for the end of the cycle they have
        already paid for - pending_change_at is set to the current end_date and
        NO payment is taken. utils/plan_limits.apply_due_plan_change() applies
        it lazily once that date passes, because this app has no scheduler.
        KNOWN LIMITATION: there is no recurring billing in this app at all
        (nothing charges anyone at renewal), so "takes effect at cycle end"
        means the plan and its limits change then, not that a smaller charge is
        collected then.
    """
    entry = PLAN_BY_SLUG.get(plan)
    if not entry:
        flash('Invalid subscription plan.', 'danger')
        return redirect(url_for('subscription.plans'))
    if cycle not in VALID_CYCLES:
        flash('Invalid billing cycle.', 'danger')
        return redirect(url_for('subscription.plans'))

    not_owner = _require_owner()
    if not_owner:
        return not_owner

    owner_id = current_user.get_owner_id()
    subscription = get_active_subscription(owner_id)

    # This route is only for an active PAID plan; everything else belongs to
    # subscribe() (start a trial / switch during a trial).
    if not subscription or subscription.is_trial or _is_expired(subscription):
        return redirect(url_for('subscription.subscribe', plan=plan, cycle=cycle))

    target_plan = _get_or_create_plan(entry)
    if subscription.plan_id == target_plan.id and subscription.billing_cycle == cycle:
        flash(f'You are already on the {entry["name"]} plan, billed {cycle}.', 'info')
        return redirect(url_for('subscription.plans'))

    current_plan = subscription.plan
    is_upgrade = target_plan.price_monthly > (current_plan.price_monthly if current_plan else 0)
    amount_due = target_plan.price_monthly if cycle == 'monthly' else target_plan.price_yearly
    effective_at = subscription.end_date or datetime.utcnow()

    if request.method == 'POST':
        currency = request.form.get('currency', 'USD')
        try:
            if is_upgrade:
                # Park the target; do NOT touch plan_id/billing_cycle/is_active.
                subscription.pending_plan_id = target_plan.id
                subscription.pending_billing_cycle = cycle
                subscription.pending_change_at = None  # "apply on payment"
                db.session.commit()

                redirect_url = _start_pesapal_checkout(
                    subscription, target_plan, cycle, currency,
                    f"{target_plan.name} Plan {cycle.capitalize()} Subscription")
                if redirect_url:
                    log_event('subscription_upgrade_started', user_id=current_user.id, plan=target_plan.name)
                    current_app.logger.info(f"Redirecting to: {redirect_url}")
                    return redirect(redirect_url)

                # Payment couldn't be started - clear the parked change so a
                # later confirmed payment for something else can't apply it.
                subscription.pending_plan_id = None
                subscription.pending_billing_cycle = None
                db.session.commit()
                flash('Failed to initialize payment. PesaPal did not return a redirect URL. '
                      'Your current plan is unchanged.', 'danger')
                return redirect(url_for('subscription.plans'))

            # Downgrade (or a same-price switch): schedule it, charge nothing.
            subscription.pending_plan_id = target_plan.id
            subscription.pending_billing_cycle = cycle
            subscription.pending_change_at = effective_at
            db.session.commit()
            log_event('subscription_downgrade_scheduled', user_id=current_user.id, plan=target_plan.name)
            flash(f'Your plan will change to {target_plan.name} on '
                  f'{effective_at.strftime("%B %d, %Y")}. Nothing changes before then and you have not been charged.',
                  'success')
            return redirect(url_for('subscription.plans'))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error changing plan: {str(e)}")
            flash(f'Error changing plan: {str(e)}. Your current plan is unchanged.', 'danger')
            return redirect(url_for('subscription.plans'))

    return render_template('subscription/change_plan.html',
                           subscription=subscription,
                           current_plan=current_plan,
                           target_plan=target_plan,
                           target_slug=plan,
                           cycle=cycle,
                           is_upgrade=is_upgrade,
                           amount_due=amount_due,
                           effective_at=effective_at)


@subscription_bp.route('/subscription/change/cancel', methods=['POST'])
@login_required
def cancel_plan_change():
    """Drop a pending plan change (a scheduled downgrade, or an upgrade whose
    payment was never completed). Leaves the plan the account is actually on
    completely untouched."""
    not_owner = _require_owner()
    if not_owner:
        return not_owner

    subscription = get_active_subscription(current_user.get_owner_id())
    if not subscription or not subscription.pending_plan_id:
        flash('There is no pending plan change to cancel.', 'info')
        return redirect(url_for('subscription.plans'))

    try:
        subscription.pending_plan_id = None
        subscription.pending_billing_cycle = None
        subscription.pending_change_at = None
        db.session.commit()
        flash('Your pending plan change has been cancelled. Your current plan is unchanged.', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error cancelling plan change: {str(e)}")
        flash('Could not cancel the pending plan change. Please try again.', 'danger')
    return redirect(url_for('subscription.plans'))


@subscription_bp.route('/trial/payment/<int:subscription_id>', methods=['GET', 'POST'])
@login_required
def trial_payment(subscription_id):
    """Process payment after trial period using PesaPal"""
    subscription = Subscription.query.get_or_404(subscription_id)

    # Verify that the subscription belongs to the current user
    if subscription.user_id != current_user.id:
        flash('You do not have permission to access this subscription.', 'danger')
        return redirect(url_for('home'))

    # Check if the subscription is in trial status
    if not subscription.is_trial:
        flash('This subscription is not in trial status.', 'danger')
        return redirect(url_for('home'))

    # Get the subscription plan
    plan = subscription.plan

    # Calculate trial end date
    trial_end_date = subscription.get_trial_end_date()

    if request.method == 'POST':
        try:
            # Log form data for debugging
            current_app.logger.info(f"Payment form data: {request.form}")

            # Get currency and amount from form
            currency = request.form.get('currency', 'USD')
            billing_cycle = request.form.get('billing_cycle', subscription.billing_cycle)
            if billing_cycle not in VALID_CYCLES:
                billing_cycle = subscription.billing_cycle

            current_app.logger.info(f"Selected currency: {currency}, billing cycle: {billing_cycle}")

            # The cycle chosen on this form is what the customer is paying for,
            # so persist it before handing off - otherwise the callback sets
            # end_date from a cycle they didn't pick.
            if billing_cycle != subscription.billing_cycle:
                subscription.billing_cycle = billing_cycle
                db.session.commit()

            redirect_url = _start_pesapal_checkout(
                subscription, plan, billing_cycle, currency,
                f"{plan.name} Plan {billing_cycle.capitalize()} Subscription")

            if redirect_url:
                # Redirect to PesaPal payment page
                current_app.logger.info(f"Redirecting to: {redirect_url}")
                return redirect(redirect_url)
            else:
                error_msg = 'Failed to initialize payment. PesaPal did not return a redirect URL.'
                current_app.logger.error(error_msg)
                flash(error_msg, 'danger')
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            db.session.rollback()
            current_app.logger.error(f"PesaPal payment error: {str(e)}")
            current_app.logger.error(f"Error details: {error_details}")
            flash(f'Error processing payment: {str(e)}', 'danger')

    return render_template('subscription/payment.html', subscription=subscription, plan=plan, trial_end_date=trial_end_date)

@subscription_bp.route('/trial/cancel/<int:subscription_id>', methods=['GET', 'POST'])
@login_required
def cancel_trial(subscription_id):
    """Cancel a trial subscription"""
    subscription = Subscription.query.get_or_404(subscription_id)

    # Verify that the subscription belongs to the current user
    if subscription.user_id != current_user.id:
        flash('You do not have permission to access this subscription.', 'danger')
        return redirect(url_for('home'))

    if request.method == 'POST':
        reason = request.form.get('reason', '')
        subscription.is_active = False
        db.session.commit()

        flash('Your subscription has been cancelled.', 'success')
        return redirect(url_for('home'))

    return render_template('subscription/cancel.html', subscription=subscription)

@subscription_bp.route('/payment/callback')
def payment_callback():
    """Handle payment callback from PesaPal"""
    # Get the order tracking ID from the request
    order_tracking_id = request.args.get('OrderTrackingId')
    if not order_tracking_id:
        current_app.logger.error("Payment callback received without OrderTrackingId")
        flash('Invalid payment callback.', 'danger')
        return redirect(url_for('home'))

    current_app.logger.info(f"Payment callback received for order: {order_tracking_id}")
    current_app.logger.info(f"Callback parameters: {request.args}")

    # Get the payment status from PesaPal
    try:
        payment_status = pesapal.get_transaction_status(order_tracking_id)
        current_app.logger.info(f"PesaPal payment status: {payment_status}")

        if not payment_status:
            flash('Failed to get payment status from PesaPal.', 'danger')
            return redirect(url_for('home'))

        # Get payment details from session
        pending_payment = session.get('pending_payment')
        current_app.logger.info(f"Pending payment from session: {pending_payment}")

        # Get the subscription ID from session or try to find it by order tracking ID
        subscription_id = None
        if pending_payment and 'subscription_id' in pending_payment:
            subscription_id = pending_payment['subscription_id']
            current_app.logger.info(f"Found subscription ID in session: {subscription_id}")

        # If we couldn't find the subscription ID in the session, fall back to
        # this account's own active subscriptions. is_authenticated is checked
        # first: PesaPal also calls this URL server-to-server as the IPN, where
        # there is no logged-in user and touching current_user.id would raise.
        if not subscription_id:
            current_app.logger.warning("No pending payment found in session, trying to find subscription")
            if current_user.is_authenticated:
                owner_id = current_user.get_owner_id()
                candidates = Subscription.query.filter_by(user_id=owner_id, is_active=True).all()
                # Prefer a subscription with an upgrade actually awaiting
                # payment; otherwise fall back to the trial-conversion case
                # this fallback originally handled.
                pool = [s for s in candidates if s.pending_plan_id and s.pending_change_at is None]
                if not pool:
                    pool = [s for s in candidates if s.is_trial]
                if pool:
                    subscription_id = max(pool, key=lambda s: s.created_at or datetime.min).id
                    current_app.logger.info(f"Found subscription by owner ID: {subscription_id}")
            else:
                current_app.logger.warning("Payment callback has no session payment and no authenticated user")

        if not subscription_id:
            current_app.logger.error("Could not determine subscription ID for payment")
            flash('Could not find your subscription. Please contact support.', 'danger')
            return redirect(url_for('home'))

        # Get the subscription
        subscription = Subscription.query.get(subscription_id)
        if not subscription:
            current_app.logger.error(f"Subscription not found: {subscription_id}")
            flash('Subscription not found.', 'danger')
            return redirect(url_for('home'))

        # Update the subscription status based on payment status
        # PesaPalPayment.get_transaction_status() returns the description under
        # 'payment_status' (see api/payment.py) - reading only 'status' here
        # meant this ALWAYS saw 'UNKNOWN' and no payment ever activated a
        # subscription. 'status' is still read as a fallback so nothing that
        # did somehow depend on it changes shape.
        pesapal_status = (payment_status.get('payment_status')
                          or payment_status.get('status')
                          or 'UNKNOWN')
        current_app.logger.info(f"Processing payment with status: {pesapal_status}")

        # Update the subscription status if payment is completed
        if pesapal_status.upper() in ['COMPLETED', 'PAID']:
            # A paid plan change waiting on this payment (pending_change_at is
            # NULL for those; a scheduled downgrade has it set and is never
            # applied here). This is the ONLY place an upgrade is moved onto
            # plan_id, so an unpaid upgrade can never take effect.
            changed_plan_name = None
            if subscription.pending_plan_id and subscription.pending_change_at is None:
                subscription.plan_id = subscription.pending_plan_id
                subscription.billing_cycle = subscription.pending_billing_cycle or subscription.billing_cycle
                subscription.pending_plan_id = None
                subscription.pending_billing_cycle = None
                # A newly paid-for cycle starts now.
                subscription.start_date = datetime.utcnow()
                changed_plan_name = subscription.plan.name if subscription.plan else None

            # Activate the subscription
            subscription.is_trial = False
            subscription.is_active = True

            # Set the subscription end date based on billing cycle
            subscription.end_date = datetime.utcnow() + timedelta(days=cycle_length(subscription.billing_cycle))

            db.session.commit()
            current_app.logger.info(f"Subscription {subscription.id} activated after successful payment")
            if changed_plan_name:
                log_event('subscription_plan_changed', user_id=subscription.user_id, plan=changed_plan_name)
            else:
                log_event('subscription_converted', user_id=subscription.user_id, plan=subscription.plan.name)

            # Clear the pending payment from session
            if 'pending_payment' in session:
                session.pop('pending_payment')

            if changed_plan_name:
                flash(f'Payment completed successfully! You are now on the {changed_plan_name} plan.', 'success')
            else:
                flash('Payment completed successfully! Your subscription is now active.', 'success')
        elif pesapal_status.upper() in ['FAILED', 'CANCELLED', 'INVALID']:
            flash(f'Payment was not successful. Status: {pesapal_status}. Please try again.', 'warning')
        else:
            flash(f'Payment is being processed. Status: {pesapal_status}.', 'info')

        return redirect(url_for('subscription.subscription_status'))

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error processing payment callback: {str(e)}")
        import traceback
        current_app.logger.error(f"Traceback: {traceback.format_exc()}")
        flash('An error occurred while processing your payment. Please contact support.', 'danger')
        return redirect(url_for('home'))

@subscription_bp.route('/subscription/status')
@login_required
def subscription_status():
    """View subscription status"""
    subscription = Subscription.query.filter_by(user_id=current_user.id, is_active=True).first()
    if not subscription:
        return render_template('subscription/status.html', subscription=None)

    return render_template('subscription/status.html', subscription=subscription)
