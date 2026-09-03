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
            flash('A subject, a message and at least one recipient are all required.', 'danger')
            return redirect(url_for('email.bulk_email'))
        
        # Get clients - scoped to this user's own so ids for another tenant's
        # clients can't be used to email their customers.
        clients = Client.query.filter(Client.id.in_(client_ids), Client.owner_id == current_user.get_owner_id()).all()
        if not clients:
            flash('No clients selected.', 'danger')
            return redirect(url_for('email.bulk_email'))
        if len(clients) < len(client_ids):
            # Some submitted ids didn't belong to this user - drop them quietly
            # rather than error, but say so instead of pretending all were valid.
            flash('Some selected recipients were skipped because they were not found.', 'warning')
        
        # Get recipient emails
        recipients = [client for client in clients if client.email]
        if not recipients:
            flash('No valid email addresses found for selected clients.', 'danger')
            return redirect(url_for('email.bulk_email'))

        # One email per client, not one shared blast: each recipient needs
        # their own unsubscribe_token baked into the template so the
        # unsubscribe link in their copy only ever unsubscribes them, not
        # whichever client the first ?token= in a shared send happened to
        # belong to.
        for client in recipients:
            template_data = {
                'subject': subject,
                'message': message,
                'cta_text': cta_text,
                'cta_url': cta_url,
                'unsubscribe_token': client.unsubscribe_token
            }
            send_bulk_email(subject, [client.email], 'bulk_update', template_data)

        flash(f'Bulk email sent to {len(recipients)} clients.', 'success')
        return redirect(url_for('clients'))
    
    # Get all clients for selection - scoped to this user's own.
    clients = Client.query.filter_by(owner_id=current_user.get_owner_id()).all()
    return render_template('email/bulk.html', clients=clients)

@email_bp.route('/email/unsubscribe')
def unsubscribe():
    """Unsubscribe from emails.

    Looked up by unsubscribe_token, not by a bare ?email= param: this route
    has no @login_required (a client clicking a link in their inbox is never
    logged in), so email was previously the *only* thing gating who could be
    unsubscribed - anyone who knew or guessed a client's email address could
    opt them out, for any tenant, with no auth at all. The token is
    unguessable (secrets.token_urlsafe(32) - see
    migrations/add_client_unsubscribe_token.py) so it can safely stand in as
    the auth for this one action.
    """
    token = request.args.get('token')
    if not token:
        flash('Invalid unsubscribe request.', 'danger')
        return redirect(url_for('home'))

    client = Client.query.filter_by(unsubscribe_token=token).first_or_404()
    client.email_opt_out = True
    db.session.commit()
    flash('You have been unsubscribed from future emails.', 'success')

    return redirect(url_for('home'))
