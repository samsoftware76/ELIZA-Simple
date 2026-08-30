"""
Email routes for ELIZA Project Management App
"""
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user
from models import db, Client
from utils.email_utils import send_bulk_email
from datetime import datetime

# Create blueprint
email_bp = Blueprint('email', __name__)

@email_bp.route('/email/bulk', methods=['GET', 'POST'])
@login_required
def bulk_email():
    """Send bulk email to clients"""
    if request.method == 'POST':
        subject = request.form.get('subject')
        message = request.form.get('message')
        client_ids = request.form.getlist('client_ids')
        cta_text = request.form.get('cta_text')
        cta_url = request.form.get('cta_url')
        
        if not subject or not message or not client_ids:
            flash('Please fill in all required fields.', 'danger')
            return redirect(url_for('email.bulk_email'))
        
        # Get clients
        clients = Client.query.filter(Client.id.in_(client_ids)).all()
        if not clients:
            flash('No clients selected.', 'danger')
            return redirect(url_for('email.bulk_email'))
        
        # Get recipient emails
        recipients = [client.email for client in clients if client.email]
        if not recipients:
            flash('No valid email addresses found for selected clients.', 'danger')
            return redirect(url_for('email.bulk_email'))
        
        # Prepare template data
        template_data = {
            'subject': subject,
            'message': message,
            'cta_text': cta_text,
            'cta_url': cta_url
        }
        
        # Send bulk email
        send_bulk_email(subject, recipients, 'bulk_update', template_data)
        
        flash(f'Bulk email sent to {len(recipients)} clients.', 'success')
        return redirect(url_for('clients'))
    
    # Get all clients for selection
    clients = Client.query.all()
    return render_template('email/bulk.html', clients=clients)

@email_bp.route('/email/unsubscribe')
def unsubscribe():
    """Unsubscribe from emails"""
    email = request.args.get('email')
    if not email:
        flash('Invalid unsubscribe request.', 'danger')
        return redirect(url_for('index'))
    
    # Find client with this email
    client = Client.query.filter_by(email=email).first()
    if client:
        client.email_opt_out = True
        db.session.commit()
        flash('You have been unsubscribed from future emails.', 'success')
    else:
        flash('Email address not found.', 'danger')
    
    return redirect(url_for('index'))
