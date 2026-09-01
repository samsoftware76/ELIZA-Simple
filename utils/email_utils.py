"""
Email utility functions for the ELIZA Project Management App
"""
import os
from flask import render_template, current_app
from flask_mail import Mail, Message
import logging
import os

logger = logging.getLogger(__name__)

mail = Mail()

def send_email(subject, recipients, text_body, html_body=None, sender=None):
    """
    Send an email with the given parameters.

    Sent synchronously, not via a background thread. A background Thread
    works on a normal long-running server, but on Vercel (or any serverless
    platform) the execution environment can be frozen the instant the HTTP
    response is sent - the thread often never gets to finish before that
    happens, so emails silently never go out. Sending inline is slightly
    slower per request but is the only version that reliably completes.
    A failure here is logged and swallowed rather than raised, so a broken
    mail server doesn't turn into a 500 on the calling route (registration,
    quote/invoice sending, etc. all call this and shouldn't hard-fail if
    mail happens to be down).

    Args:
        subject (str): Email subject
        recipients (list): List of recipient email addresses
        text_body (str): Plain text email body
        html_body (str, optional): HTML email body. Defaults to None.
        sender (str, optional): Sender email address. Defaults to app config value.
    """
    app = current_app._get_current_object()
    sender = sender or app.config['MAIL_DEFAULT_SENDER']

    msg = Message(subject, sender=sender, recipients=recipients)
    msg.body = text_body
    if html_body:
        msg.html = html_body

    try:
        mail.send(msg)
    except Exception as e:
        logger.error(f"Failed to send email '{subject}' to {recipients}: {str(e)}")

def send_task_assignment_notification(task, user):
    """
    Send an email notification when a task is assigned to a user
    
    Args:
        task: The Task object
        user: The User object the task is assigned to
    """
    subject = f"[ELIZA] Task Assigned: {task.title}"
    recipients = [user.email]
    
    try:
        logger.info(f"Sending task assignment notification to {user.email}")
        text_body = f"Hello {user.first_name},\n\nYou have been assigned a new task: {task.title}\n\nDescription: {task.description}\n\nDue Date: {task.due_date}\n\nRegards,\nELIZA Project Management Team"
        html_body = render_template('emails/task_assignment.html', user=user, task=task)
        send_email(subject, recipients, text_body, html_body)
        logger.info(f"Task assignment notification sent successfully to {user.email}")
    except Exception as e:
        logger.error(f"Failed to send task assignment notification: {str(e)}")

def test_email_configuration(recipient_email=None):
    """
    Send a test email to verify email configuration is working
    
    Args:
        recipient_email (str, optional): Email address to send test to. 
                                        If None, uses the configured MAIL_USERNAME.
    
    Returns:
        bool: True if email sent successfully, False otherwise
    """
    try:
        app = current_app._get_current_object()
        sender = app.config['MAIL_DEFAULT_SENDER']
        recipient = recipient_email or app.config['MAIL_USERNAME']
        
        logger.info(f"Sending test email from {sender} to {recipient}")
        
        subject = "[ELIZA] Email Configuration Test"
        text_body = "This is a test email to verify that the ELIZA Project Management App email configuration is working correctly."
        html_body = "<h1>ELIZA Email Test</h1><p>This is a test email to verify that the ELIZA Project Management App email configuration is working correctly.</p>"
        
        msg = Message(subject, sender=sender, recipients=[recipient])
        msg.body = text_body
        msg.html = html_body
        
        # Send email synchronously for testing
        mail.send(msg)
        
        logger.info("Test email sent successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to send test email: {str(e)}")
        return False
    
    text_body = f"""
Hello {user.first_name},

You have been assigned a new task in the ELIZA Project Management system:

Task: {task.title}
Project: {task.project.title}
Priority: {"High" if task.priority == 3 else "Medium" if task.priority == 2 else "Low"}
Due Date: {task.due_date.strftime('%B %d, %Y') if task.due_date else 'Not specified'}

Description:
{task.description or 'No description provided.'}

You can view the task details and start working on it by clicking the link below:
{current_app.config['BASE_URL']}/tasks/{task.id}

Thank you,
ELIZA Project Management System
"""
    
    html_body = render_template('emails/task_assignment.html', 
                               user=user, 
                               task=task,
                               base_url=current_app.config['BASE_URL'])
    
    send_email(subject, recipients, text_body, html_body)

def send_task_update_notification(task, update_type, updated_by):
    """
    Send an email notification when a task is updated
    
    Args:
        task: The Task object
        update_type (str): Type of update (status_change, due_date_change, etc.)
        updated_by: The User who made the update
    """
    # Only send notifications to task assignee and project team members
    recipients = []
    
    # Add task assignee if exists and is not the updater
    if task.assignee_id and task.assignee_id != updated_by.id:
        recipients.append(task.assigned_to.email)
    
    # Add project team members who are not the updater
    for member in task.project.project_members:
        if member.user_id != updated_by.id and member.user.email not in recipients:
            recipients.append(member.user.email)
    
    # Don't send if no recipients
    if not recipients:
        return
    
    subject = f"[ELIZA] Task Updated: {task.title}"
    
    update_message = ""
    if update_type == "status_change":
        update_message = f"The status has been changed to: {task.status.value}"
    elif update_type == "due_date_change":
        update_message = f"The due date has been changed to: {task.due_date.strftime('%B %d, %Y') if task.due_date else 'Not specified'}"
    elif update_type == "assignment_change":
        if task.assignee_id:
            update_message = f"The task has been assigned to: {task.assigned_to.username}"
        else:
            update_message = "The task is now unassigned"
    else:
        update_message = "The task has been updated"
    
    text_body = f"""
Hello,

A task in the ELIZA Project Management system has been updated by {updated_by.username}:

Task: {task.title}
Project: {task.project.title}
{update_message}

You can view the task details by clicking the link below:
{current_app.config['BASE_URL']}/tasks/{task.id}

Thank you,
ELIZA Project Management System
"""
    
    html_body = render_template('emails/task_update.html', 
                               task=task,
                               update_type=update_type,
                               update_message=update_message,
                               updated_by=updated_by,
                               base_url=current_app.config['BASE_URL'])
    
    send_email(subject, recipients, text_body, html_body)

def send_bulk_email(subject, recipients, template, template_data=None):
    """
    Send a bulk email to multiple recipients
    
    Args:
        subject (str): Email subject
        recipients (list): List of recipient email addresses
        template (str): Template name (without .html extension)
        template_data (dict, optional): Data to pass to the template. Defaults to None.
    """
    if not recipients:
        return
    
    # Default template data
    template_data = template_data or {}
    
    # Add base_url to template data
    template_data['base_url'] = current_app.config['BASE_URL']
    
    # Prepare email content
    html_body = render_template(f'emails/{template}.html', **template_data)
    text_body = f"""Hello,

{subject}

Please view this email with an HTML-compatible email client to see the full content.

Thank you,
ELIZA Project Management System
"""
    
    # Send email to all recipients
    send_email(subject, recipients, text_body, html_body)

def send_password_reset_email(user, reset_url):
    """
    Email a user a link to reset their password.

    Args:
        user: The User object requesting the reset
        reset_url (str): Fully-qualified, signed, time-limited reset link
    """
    subject = "[ELIZA] Reset your password"
    recipients = [user.email]

    try:
        text_body = (
            f"Hello {user.first_name},\n\n"
            f"We received a request to reset your ELIZA password. This link expires in 30 minutes:\n"
            f"{reset_url}\n\n"
            f"If you didn't request this, you can ignore this email.\n\n"
            f"Thank you,\nELIZA Project Management"
        )
        html_body = render_template('emails/password_reset.html', user=user, reset_url=reset_url)
        send_email(subject, recipients, text_body, html_body)
        logger.info(f"Password reset email sent to {user.email}")
    except Exception as e:
        logger.error(f"Failed to send password reset email to {user.email}: {str(e)}")


def send_team_invite_email(invite, accept_url):
    """
    Email an invited team member a link to accept a team invite.

    Args:
        invite: The TeamInvite object
        accept_url (str): Fully-qualified, signed, time-limited accept-invite link
    """
    subject = "[ELIZA] You've been invited to join a team on ELIZA"
    recipients = [invite.invitee_email]

    try:
        inviter_name = invite.inviter.business_name or invite.inviter.get_full_name()
        text_body = (
            f"Hello,\n\n"
            f"{inviter_name} has invited you to join their team on ELIZA as a {invite.role.value.replace('_', ' ').title()}. "
            f"This link expires in 7 days:\n"
            f"{accept_url}\n\n"
            f"If you weren't expecting this invite, you can ignore this email.\n\n"
            f"Thank you,\nELIZA Project Management"
        )
        html_body = render_template('emails/team_invite.html', invite=invite, inviter_name=inviter_name, accept_url=accept_url)
        send_email(subject, recipients, text_body, html_body)
        logger.info(f"Team invite email sent to {invite.invitee_email}")
    except Exception as e:
        logger.error(f"Failed to send team invite email to {invite.invitee_email}: {str(e)}")


def send_quote_email(quote, portal_url):
    """
    Email a client a link to view and respond to a quote via the client portal.

    Args:
        quote: The Quote object
        portal_url (str): Fully-qualified tokenized portal link to the quote
    """
    if not quote.client.email:
        logger.warning(f"Cannot send quote {quote.quote_number}: client has no email address")
        return

    subject = f"[ELIZA] Quote {quote.quote_number}: {quote.title}"
    recipients = [quote.client.email]

    try:
        text_body = (
            f"Hello {quote.client.contact_person},\n\n"
            f"{quote.created_by.get_full_name()} has sent you a quote: {quote.title}\n"
            f"Quote number: {quote.quote_number}\n"
            f"Total: {quote.total:.2f} {quote.currency}\n\n"
            f"View and respond to this quote here:\n{portal_url}\n\n"
            f"Thank you,\nELIZA Project Management Team"
        )
        html_body = render_template('emails/quote_notification.html', quote=quote, portal_url=portal_url)
        send_email(subject, recipients, text_body, html_body)
        logger.info(f"Quote {quote.quote_number} emailed to {quote.client.email}")
    except Exception as e:
        logger.error(f"Failed to send quote email for {quote.quote_number}: {str(e)}")


def send_invoice_email(invoice, portal_url):
    """
    Email a client a link to view and pay an invoice via the client portal.

    Args:
        invoice: The Invoice object
        portal_url (str): Fully-qualified tokenized portal link to the invoice
    """
    if not invoice.client.email:
        logger.warning(f"Cannot send invoice {invoice.invoice_number}: client has no email address")
        return

    subject = f"[ELIZA] Invoice {invoice.invoice_number}: {invoice.title}"
    recipients = [invoice.client.email]

    try:
        due = invoice.due_date.strftime('%B %d, %Y') if invoice.due_date else 'on receipt'
        text_body = (
            f"Hello {invoice.client.contact_person},\n\n"
            f"{invoice.created_by.get_full_name()} has sent you an invoice: {invoice.title}\n"
            f"Invoice number: {invoice.invoice_number}\n"
            f"Amount due: {invoice.total:.2f} {invoice.currency}\n"
            f"Due: {due}\n\n"
            f"View and pay this invoice here:\n{portal_url}\n\n"
            f"Thank you,\nELIZA Project Management Team"
        )
        html_body = render_template('emails/invoice_notification.html', invoice=invoice, portal_url=portal_url)
        send_email(subject, recipients, text_body, html_body)
        logger.info(f"Invoice {invoice.invoice_number} emailed to {invoice.client.email}")
    except Exception as e:
        logger.error(f"Failed to send invoice email for {invoice.invoice_number}: {str(e)}")


def send_quote_response_notification(quote):
    """
    Notify the staff member who created a quote that the client has responded to it.

    Args:
        quote: The Quote object (status already updated to accepted/declined)
    """
    if not quote.created_by or not quote.created_by.email:
        logger.warning(f"Cannot notify quote {quote.quote_number} response: no creator email")
        return

    accepted = quote.status == 'accepted'
    subject = f"[ELIZA] Quote {quote.quote_number} {'Accepted' if accepted else 'Declined'} by {quote.client.name}"
    recipients = [quote.created_by.email]

    try:
        text_body = (
            f"Hello {quote.created_by.first_name},\n\n"
            f"{quote.client.name} has {'accepted' if accepted else 'declined'} quote {quote.quote_number}: {quote.title}\n"
            + (f"Reason given: {quote.decline_reason}\n" if not accepted and quote.decline_reason else "")
            + f"\nView it here: {current_app.config['BASE_URL']}/quotes/{quote.id}\n\n"
            f"Thank you,\nELIZA Project Management System"
        )
        html_body = render_template(
            'emails/quote_response_notification.html',
            quote=quote,
            accepted=accepted,
            base_url=current_app.config['BASE_URL'],
        )
        send_email(subject, recipients, text_body, html_body)
        logger.info(f"Quote {quote.quote_number} response notification sent to {quote.created_by.email}")
    except Exception as e:
        logger.error(f"Failed to send quote response notification for {quote.quote_number}: {str(e)}")


def send_invoice_paid_notification(invoice):
    """
    Notify the staff member who created an invoice that the client has paid it.

    This is the one notification that matters most in the whole app: a
    client paying via the portal's PesaPal flow updates the database, but
    nothing else tells the business owner money actually arrived unless
    they happen to check the invoices list themselves.

    Args:
        invoice: The Invoice object (status already updated to paid)
    """
    if not invoice.created_by or not invoice.created_by.email:
        logger.warning(f"Cannot notify invoice {invoice.invoice_number} payment: no creator email")
        return

    subject = f"[ELIZA] Payment received: Invoice {invoice.invoice_number} ({invoice.client.name})"
    recipients = [invoice.created_by.email]

    try:
        text_body = (
            f"Hello {invoice.created_by.first_name},\n\n"
            f"Good news - {invoice.client.name} just paid invoice {invoice.invoice_number}: {invoice.title}\n"
            f"Amount: {invoice.total:.2f} {invoice.currency}\n"
            f"Method: {invoice.payment_method or 'pesapal'}\n\n"
            f"View it here: {current_app.config['BASE_URL']}/invoices/{invoice.id}\n\n"
            f"Thank you,\nELIZA Project Management System"
        )
        html_body = render_template(
            'emails/invoice_paid_notification.html',
            invoice=invoice,
            base_url=current_app.config['BASE_URL'],
        )
        send_email(subject, recipients, text_body, html_body)
        logger.info(f"Invoice {invoice.invoice_number} payment notification sent to {invoice.created_by.email}")
    except Exception as e:
        logger.error(f"Failed to send invoice payment notification for {invoice.invoice_number}: {str(e)}")


def send_task_comment_notification(task, comment, commented_by):
    """
    Send an email notification when a comment is added to a task
    
    Args:
        task: The Task object
        comment: The Comment object
        commented_by: The User who made the comment
    """
    # Only send notifications to task assignee and project team members
    recipients = []
    
    # Add task assignee if exists and is not the commenter
    if task.assignee_id and task.assignee_id != commented_by.id:
        recipients.append(task.assigned_to.email)
    
    # Add project team members who are not the commenter
    for member in task.project.project_members:
        if member.user_id != commented_by.id and member.user.email not in recipients:
            recipients.append(member.user.email)
    
    # Don't send if no recipients
    if not recipients:
        return
    
    subject = f"[ELIZA] New Comment on Task: {task.title}"
    
    text_body = f"""
Hello,

A new comment has been added to a task in the ELIZA Project Management system by {commented_by.username}:

Task: {task.title}
Project: {task.project.title}

Comment:
{comment.content}

You can view the task and reply to the comment by clicking the link below:
{current_app.config['BASE_URL']}/tasks/{task.id}

Thank you,
ELIZA Project Management System
"""
    
    html_body = render_template('emails/task_comment.html', 
                               task=task,
                               comment=comment,
                               commented_by=commented_by,
                               base_url=current_app.config['BASE_URL'])
    
    send_email(subject, recipients, text_body, html_body)
