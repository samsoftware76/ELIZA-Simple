"""
Main routes for ELIZA Project Management App
"""
from flask import Blueprint, render_template, redirect, url_for, request
from models.subscription import SubscriptionPlan
from flask_login import current_user

main_bp = Blueprint('main', __name__)

@main_bp.route('/')
def landing():
    """Landing page for the application"""
    return handle_landing_page()

# Method to handle landing page requests directly
def handle_landing_page():
    """Handler for landing page that can be called directly"""
    # If user is already logged in, redirect to home
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    
    # Get all active subscription plans
    plans = SubscriptionPlan.query.filter_by(is_active=True).all()
    
    return render_template('landing.html', plans=plans)

@main_bp.route('/home')
def home():
    """Home page for logged in users"""
    # This route should already exist in your application
    # If not, you can implement it here
    return render_template('home.html')
