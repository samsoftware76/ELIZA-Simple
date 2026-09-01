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

# Create blueprint
subscription_bp = Blueprint('subscription', __name__)

# Initialize PesaPal payment
pesapal = PesaPalPayment()

@subscription_bp.route('/plans')
def plans():
    """View subscription plans"""
    return render_template('subscription/plans.html')

@subscription_bp.route('/subscribe/<plan>/<cycle>')
@login_required
def subscribe(plan, cycle):
    """Start a 14-day free trial for a subscription plan"""
    # Get plan details based on the plan name
    if plan == 'basic':
        plan_name = 'Basic'
        price_monthly = 9.99
        price_yearly = 99.99
    elif plan == 'professional':
        plan_name = 'Professional'
        price_monthly = 19.99
        price_yearly = 199.99
    elif plan == 'enterprise':
        plan_name = 'Enterprise'
        price_monthly = 49.99
        price_yearly = 499.99
    else:
        flash('Invalid subscription plan.', 'danger')
        return redirect(url_for('subscription.plans'))
    
    # Get or create subscription plan
    subscription_plan = SubscriptionPlan.query.filter_by(name=plan_name).first()
    if not subscription_plan:
        # Set plan details based on the plan name
        if plan_name == 'Basic':
            max_projects = 5
            max_users = 3
            max_clients = 10
            features_text = 'Basic features: Up to 5 projects, 10 clients, 3 team members'
        elif plan_name == 'Professional':
            max_projects = 15
            max_users = 10
            max_clients = 30
            features_text = 'Advanced features: Up to 15 projects, 30 clients, 10 team members'
        else:  # Enterprise
            max_projects = 0  # Unlimited
            max_users = 0     # Unlimited
            max_clients = 0   # Unlimited
            features_text = 'Enterprise features: Unlimited projects, clients, and team members'
            
        subscription_plan = SubscriptionPlan(
            name=plan_name,
            description=f'{plan_name} subscription plan',
            price_monthly=price_monthly,
            price_yearly=price_yearly,
            max_projects=max_projects,
            max_users=max_users,
            max_clients=max_clients,
            features=features_text
        )
        db.session.add(subscription_plan)
        db.session.commit()
    
    try:
        # Calculate dates
        start_date = datetime.utcnow()
        end_date = start_date + timedelta(days=30 if cycle == 'monthly' else 365)
        
        # Check if user already has an active subscription
        existing_subscription = Subscription.query.filter_by(user_id=current_user.id, is_active=True).first()
        
        if existing_subscription:
            # Update existing subscription
            existing_subscription.is_active = False
            db.session.commit()
            
        # Create new trial subscription
        subscription = Subscription(
            user_id=current_user.id,
            plan_id=subscription_plan.id,
            start_date=start_date,
            end_date=end_date,
            is_active=True,
            is_trial=True,
            billing_cycle=cycle
        )
        db.session.add(subscription)
        db.session.commit()
        log_event('subscription_trial_started', user_id=current_user.id, plan=plan_name)

        # Calculate trial end date for the flash message
        trial_end_date = subscription.get_trial_end_date()
        flash(f'Your 14-day free trial of the {plan_name} plan has started! You will be asked to provide payment details before {trial_end_date.strftime("%B %d, %Y")} to continue using the service.', 'success')
        return redirect(url_for('home'))
    except Exception as e:
        db.session.rollback()
        flash(f'Error starting trial: {str(e)}', 'danger')
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
            
            current_app.logger.info(f"Selected currency: {currency}, billing cycle: {billing_cycle}")
            
            # Determine amount based on billing cycle and currency
            if billing_cycle == 'monthly':
                amount = plan.price_monthly
            else:  # yearly
                amount = plan.price_yearly
                
            # Convert to UGX if needed
            if currency == 'UGX':
                # Simple conversion rate (1 USD = 3700 UGX approx)
                amount = amount * 3700
            
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
            
            current_app.logger.info(f"User details: {user_email}, {user_first_name}, {user_last_name}")
            
            # Prepare PesaPal payment
            payment_data = pesapal.create_payment_order(
                amount=amount,
                description=f"{plan.name} Plan {billing_cycle.capitalize()} Subscription",
                email=user_email,
                phone=user_phone,
                first_name=user_first_name,
                last_name=user_last_name,
                callback_url=callback_url,
                currency=currency
            )
            
            current_app.logger.info(f"PesaPal payment response: {payment_data}")
            
            if payment_data and 'redirect_url' in payment_data:
                # Store the redirect URL before overwriting payment_data
                redirect_url = payment_data['redirect_url']
                
                # Create payment record with only the fields that exist in the database
                payment_record = {
                    'subscription_id': subscription.id,
                    'amount': amount,
                    'currency': currency,
                    'status': 'pending',
                    'payment_method': ''
                }
                
                # Add order_tracking_id if available in the response
                if 'order_tracking_id' in payment_data:
                    payment_record['order_tracking_id'] = payment_data['order_tracking_id']
                
                # Add merchant_reference as a field that will be handled by the Payment model's __init__ method
                # This won't be stored in the database but will be available as an instance attribute
                payment_record['merchant_reference'] = payment_data.get('merchant_reference') or order_id
                
                # Add payment_account as a field that will be handled by the Payment model's __init__ method
                # This won't be stored in the database but will be available as an instance attribute
                payment_record['payment_account'] = ''
                
                # Skip creating the payment record for now due to database schema issues
                # We'll just store the payment details in the session for later use
                try:
                    # Store payment details in session for later reference
                    session_payment_data = {
                        'subscription_id': subscription.id,
                        'amount': amount,
                        'currency': currency,
                        'order_tracking_id': payment_data.get('order_tracking_id'),
                        'merchant_reference': payment_data.get('merchant_reference') or order_id
                    }
                    session['pending_payment'] = session_payment_data
                    current_app.logger.info(f"Stored payment details in session: {session_payment_data}")
                except Exception as e:
                    current_app.logger.warning(f"Could not store payment in session: {str(e)}")
                
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
        
        # If we couldn't find the subscription ID in the session, try to create a payment record
        # with the information we have
        if not subscription_id:
            current_app.logger.warning("No pending payment found in session, trying to find subscription")
            # Try to find a subscription that matches this user
            subscriptions = Subscription.query.filter_by(user_id=current_user.id, is_trial=True).all()
            if subscriptions:
                # Use the most recent subscription
                subscription = max(subscriptions, key=lambda s: s.created_at)
                subscription_id = subscription.id
                current_app.logger.info(f"Found subscription by user ID: {subscription_id}")
        
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
        pesapal_status = payment_status.get('status', 'UNKNOWN')
        current_app.logger.info(f"Processing payment with status: {pesapal_status}")
        
        # Update the subscription status if payment is completed
        if pesapal_status.upper() in ['COMPLETED', 'PAID']:
            # Activate the subscription
            subscription.is_trial = False
            subscription.is_active = True
            
            # Set the subscription end date based on billing cycle
            if subscription.billing_cycle == 'monthly':
                subscription.end_date = datetime.utcnow() + timedelta(days=30)
            else:  # yearly
                subscription.end_date = datetime.utcnow() + timedelta(days=365)
            
            db.session.commit()
            current_app.logger.info(f"Subscription {subscription.id} activated after successful payment")
            log_event('subscription_converted', user_id=subscription.user_id, plan=subscription.plan.name)

            # Clear the pending payment from session
            if 'pending_payment' in session:
                session.pop('pending_payment')
            
            flash('Payment completed successfully! Your subscription is now active.', 'success')
        elif pesapal_status.upper() in ['FAILED', 'CANCELLED', 'INVALID']:
            flash(f'Payment was not successful. Status: {pesapal_status}. Please try again.', 'warning')
        else:
            flash(f'Payment is being processed. Status: {pesapal_status}.', 'info')
        
        return redirect(url_for('subscription.subscription_status'))
    
    except Exception as e:
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
