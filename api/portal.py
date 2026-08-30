"""
Client-facing portal for the ELIZA Project Management App.

No login required: clients reach these pages via an unguessable tokenized
link emailed to them (see api/billing.py's quote_send/invoice_send). A quote
can be accepted or declined here; an invoice can be paid here via PesaPal.

Payment confirmation is looked up by the invoice's own public_token in the
URL, never by Flask session - unlike the subscription payment flow in
api/subscription.py, this must work for a client who never logs in and may
complete payment on a different device than the one that opened the link.
"""
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app

from models import db
from models.billing import Quote, Invoice, QuoteStatus, InvoiceStatus
from api.payment import PesaPalPayment
from utils.email_utils import send_quote_response_notification
from utils.document_print import build_print_context

portal_bp = Blueprint('portal', __name__, url_prefix='/portal')

pesapal = PesaPalPayment()


def _brand_context(owner):
    """Branding shown to the client: the quote/invoice creator's own business
    name/logo if they've set one (see /profile), falling back to ELIZA so an
    unconfigured account still looks presentable."""
    return {
        'brand_name': (owner.business_name or 'ELIZA') if owner else 'ELIZA',
        'brand_logo_url': (owner.business_logo_url if owner else None) or None,
    }


def _split_name(full_name):
    """Best-effort split of a single 'contact_person' field into first/last name for PesaPal"""
    parts = (full_name or '').strip().split(' ', 1)
    first = parts[0] if parts else 'Client'
    last = parts[1] if len(parts) > 1 else ''
    return first, last


# --------------------------------------------------------------------------
# Quotes
# --------------------------------------------------------------------------

@portal_bp.route('/quote/<token>')
def view_quote(token):
    """Client-facing quote view: accept or decline"""
    quote = Quote.query.filter_by(public_token=token).first_or_404()
    return render_template('portal/quote.html', quote=quote, **_brand_context(quote.created_by))


@portal_bp.route('/quote/<token>/print')
def print_quote(token):
    """Standalone printable/PDF view of a quote (client view, no login required)"""
    quote = Quote.query.filter_by(public_token=token).first_or_404()
    return render_template('print/document.html', **build_print_context(quote, 'Quote'))


@portal_bp.route('/quote/<token>/accept', methods=['POST'])
def accept_quote(token):
    """Client accepts the quote"""
    quote = Quote.query.filter_by(public_token=token).first_or_404()
    if not quote.is_respondable:
        flash('This quote can no longer be responded to.', 'warning')
        return redirect(url_for('portal.view_quote', token=token))

    try:
        quote.status = QuoteStatus.ACCEPTED
        quote.responded_at = datetime.utcnow()
        db.session.commit()
        send_quote_response_notification(quote)
        flash('Thank you! The quote has been accepted.', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error accepting quote {token}: {str(e)}")
        flash('Something went wrong recording your response. Please try again.', 'danger')

    return redirect(url_for('portal.view_quote', token=token))


@portal_bp.route('/quote/<token>/decline', methods=['POST'])
def decline_quote(token):
    """Client declines the quote, optionally with a reason"""
    quote = Quote.query.filter_by(public_token=token).first_or_404()
    if not quote.is_respondable:
        flash('This quote can no longer be responded to.', 'warning')
        return redirect(url_for('portal.view_quote', token=token))

    try:
        quote.status = QuoteStatus.DECLINED
        quote.responded_at = datetime.utcnow()
        quote.decline_reason = (request.form.get('reason') or '').strip()[:2000]
        db.session.commit()
        send_quote_response_notification(quote)
        flash('The quote has been declined.', 'info')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error declining quote {token}: {str(e)}")
        flash('Something went wrong recording your response. Please try again.', 'danger')

    return redirect(url_for('portal.view_quote', token=token))


# --------------------------------------------------------------------------
# Invoices
# --------------------------------------------------------------------------

@portal_bp.route('/invoice/<token>')
def view_invoice(token):
    """Client-facing invoice view: pay via PesaPal"""
    invoice = Invoice.query.filter_by(public_token=token).first_or_404()
    pesapal_configured = bool(pesapal.consumer_key and pesapal.consumer_secret)
    return render_template('portal/invoice.html', invoice=invoice, pesapal_configured=pesapal_configured,
                            **_brand_context(invoice.created_by))


@portal_bp.route('/invoice/<token>/print')
def print_invoice(token):
    """Standalone printable/PDF view of an invoice (client view, no login required)"""
    invoice = Invoice.query.filter_by(public_token=token).first_or_404()
    return render_template('print/document.html', **build_print_context(invoice, 'Invoice'))


@portal_bp.route('/invoice/<token>/pay', methods=['POST'])
def pay_invoice(token):
    """Kick off a PesaPal payment order for this invoice and redirect the client to it"""
    invoice = Invoice.query.filter_by(public_token=token).first_or_404()

    if not invoice.is_payable:
        flash('This invoice is not open for payment.', 'warning')
        return redirect(url_for('portal.view_invoice', token=token))

    client = invoice.client
    first_name, last_name = _split_name(client.contact_person)
    callback_url = url_for('portal.invoice_payment_callback', token=token, _external=True)

    try:
        payment_data = pesapal.create_payment_order(
            amount=invoice.total,
            description=f"Invoice {invoice.invoice_number} - {invoice.title}"[:100],
            email=client.email,
            phone=client.phone,
            first_name=first_name,
            last_name=last_name,
            callback_url=callback_url,
            currency=invoice.currency or 'USD',
        )

        if payment_data and payment_data.get('redirect_url'):
            invoice.pesapal_merchant_reference = payment_data.get('merchant_reference')
            invoice.pesapal_order_tracking_id = payment_data.get('order_tracking_id')
            db.session.commit()
            return redirect(payment_data['redirect_url'])

        current_app.logger.error(f"PesaPal did not return a redirect URL for invoice {invoice.invoice_number}: {payment_data}")
        flash('Could not start the payment. Please try again shortly or contact us.', 'danger')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error starting payment for invoice {invoice.invoice_number}: {str(e)}")
        flash('Could not start the payment. Please try again shortly or contact us.', 'danger')

    return redirect(url_for('portal.view_invoice', token=token))


@portal_bp.route('/invoice/<token>/payment/callback')
def invoice_payment_callback(token):
    """PesaPal redirects (and calls as IPN) here after a payment attempt.

    Looked up entirely by the invoice's token + PesaPal's own order tracking
    ID - no session dependency, so this works whether the client is redirected
    back on the same device or PesaPal calls it server-to-server."""
    invoice = Invoice.query.filter_by(public_token=token).first_or_404()

    order_tracking_id = request.args.get('OrderTrackingId') or invoice.pesapal_order_tracking_id
    if not order_tracking_id:
        flash('Invalid payment callback.', 'danger')
        return redirect(url_for('portal.view_invoice', token=token))

    try:
        status = pesapal.get_transaction_status(order_tracking_id)
        if not status:
            flash('Could not confirm payment status with PesaPal yet. If you completed payment, it will update shortly.', 'info')
            return redirect(url_for('portal.view_invoice', token=token))

        pesapal_status = (status.get('payment_status') or '').upper()
        if pesapal_status in ('COMPLETED', 'PAID'):
            if invoice.status != InvoiceStatus.PAID:
                invoice.status = InvoiceStatus.PAID
                invoice.paid_at = datetime.utcnow()
                invoice.payment_method = status.get('payment_method') or 'pesapal'
                db.session.commit()
            flash('Payment received. Thank you!', 'success')
        elif pesapal_status in ('FAILED', 'INVALID'):
            flash(f'Payment was not successful (status: {pesapal_status}). Please try again.', 'danger')
        else:
            flash(f'Payment is still processing (status: {pesapal_status or "unknown"}). Refresh this page in a moment.', 'info')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error processing payment callback for invoice {invoice.invoice_number}: {str(e)}")
        flash('An error occurred while confirming your payment. If you were charged, please contact us.', 'danger')

    return redirect(url_for('portal.view_invoice', token=token))
