"""
Staff-facing Quote and Invoice management for the ELIZA Project Management App.

The client-facing counterpart (view/accept/decline a quote, view/pay an
invoice via a tokenized link, no login required) lives in api/portal.py.
"""
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user

from models import db
from models.models import Client, Project
from models.billing import Quote, QuoteItem, Invoice, InvoiceItem, QuoteStatus, InvoiceStatus
from forms import QuoteForm, InvoiceForm
from utils.email_utils import send_quote_email, send_invoice_email
from utils.document_print import build_print_context

billing_bp = Blueprint('billing', __name__)


def parse_line_items(form):
    """Parse dynamic line-item fields (item_description[], item_quantity[], item_unit_price[])
    submitted alongside a QuoteForm/InvoiceForm into a list of dicts. Rows with a blank
    description are dropped (lets the "add row" button leave trailing empty rows)."""
    descriptions = form.getlist('item_description[]')
    quantities = form.getlist('item_quantity[]')
    unit_prices = form.getlist('item_unit_price[]')

    items = []
    for description, quantity, unit_price in zip(descriptions, quantities, unit_prices):
        description = (description or '').strip()
        if not description:
            continue
        try:
            quantity = float(quantity) if quantity not in (None, '') else 1
        except ValueError:
            quantity = 1
        try:
            unit_price = float(unit_price) if unit_price not in (None, '') else 0
        except ValueError:
            unit_price = 0
        items.append({'description': description, 'quantity': quantity, 'unit_price': unit_price})
    return items


# --------------------------------------------------------------------------
# Quotes
# --------------------------------------------------------------------------

@billing_bp.route('/quotes')
@login_required
def quotes_list():
    """List all quotes"""
    # Paginated so the page doesn't load every quote row on every visit.
    page = request.args.get('page', 1, type=int)
    pagination = Quote.query.order_by(Quote.created_at.desc()).paginate(page=page, per_page=20, error_out=False)
    return render_template('quotes/index.html', quotes=pagination.items, pagination=pagination)


@billing_bp.route('/quotes/create', methods=['GET', 'POST'])
@login_required
def quote_create():
    """Create a new quote"""
    form = QuoteForm()

    if form.validate_on_submit():
        items = parse_line_items(request.form)
        if not items:
            flash('Add at least one line item before saving the quote.', 'danger')
        else:
            try:
                quote = Quote(
                    quote_number='PENDING',
                    client_id=form.client_id.data,
                    project_id=form.project_id.data or None,
                    created_by_id=current_user.id,
                    title=form.title.data,
                    currency=form.currency.data,
                    tax_rate=form.tax_rate.data or 0,
                    valid_until=form.valid_until.data,
                    notes=form.notes.data,
                )
                db.session.add(quote)
                db.session.flush()  # assigns quote.id for numbering, still inside the transaction
                quote.quote_number = f"Q-{quote.id:05d}"

                for item in items:
                    db.session.add(QuoteItem(quote_id=quote.id, **item))

                db.session.commit()
                flash(f'Quote {quote.quote_number} created.', 'success')
                return redirect(url_for('billing.quote_detail', quote_id=quote.id))
            except Exception as e:
                db.session.rollback()
                current_app.logger.error(f"Error creating quote: {str(e)}")
                flash(f'Error creating quote: {str(e)}', 'danger')

    return render_template('quotes/form.html', form=form, quote=None, items=[])


@billing_bp.route('/quotes/<int:quote_id>')
@login_required
def quote_detail(quote_id):
    """View a quote (staff view)"""
    quote = Quote.query.get_or_404(quote_id)
    portal_url = current_app.config['BASE_URL'].rstrip('/') + url_for('portal.view_quote', token=quote.public_token)
    return render_template('quotes/detail.html', quote=quote, portal_url=portal_url)


@billing_bp.route('/quotes/<int:quote_id>/print')
@login_required
def quote_print(quote_id):
    """Standalone printable/PDF view of a quote (staff view)"""
    quote = Quote.query.get_or_404(quote_id)
    return render_template('print/document.html', **build_print_context(quote, 'Quote'))


@billing_bp.route('/quotes/<int:quote_id>/edit', methods=['GET', 'POST'])
@login_required
def quote_edit(quote_id):
    """Edit a quote. Allowed for draft and sent quotes (the client hasn't
    responded yet) - blocked once the client has accepted/declined, since
    silently changing terms after a response is misleading."""
    quote = Quote.query.get_or_404(quote_id)
    if quote.status not in (QuoteStatus.DRAFT, QuoteStatus.SENT):
        flash('This quote has already been responded to and can no longer be edited.', 'warning')
        return redirect(url_for('billing.quote_detail', quote_id=quote.id))

    form = QuoteForm(obj=quote)

    if form.validate_on_submit():
        items = parse_line_items(request.form)
        if not items:
            flash('Add at least one line item before saving the quote.', 'danger')
        else:
            try:
                quote.client_id = form.client_id.data
                quote.project_id = form.project_id.data or None
                quote.title = form.title.data
                quote.currency = form.currency.data
                quote.tax_rate = form.tax_rate.data or 0
                quote.valid_until = form.valid_until.data
                quote.notes = form.notes.data

                QuoteItem.query.filter_by(quote_id=quote.id).delete()
                for item in items:
                    db.session.add(QuoteItem(quote_id=quote.id, **item))

                db.session.commit()
                flash(f'Quote {quote.quote_number} updated.', 'success')
                return redirect(url_for('billing.quote_detail', quote_id=quote.id))
            except Exception as e:
                db.session.rollback()
                current_app.logger.error(f"Error updating quote: {str(e)}")
                flash(f'Error updating quote: {str(e)}', 'danger')
    elif request.method == 'GET':
        form.project_id.data = quote.project_id or 0

    return render_template('quotes/form.html', form=form, quote=quote, items=quote.items)


@billing_bp.route('/quotes/<int:quote_id>/send', methods=['POST'])
@login_required
def quote_send(quote_id):
    """Email the client a portal link to view and respond to this quote"""
    quote = Quote.query.get_or_404(quote_id)
    if not quote.client.email:
        flash('This client has no email address on file. Add one before sending.', 'danger')
        return redirect(url_for('billing.quote_detail', quote_id=quote.id))

    try:
        quote.status = QuoteStatus.SENT
        quote.sent_at = datetime.utcnow()
        db.session.commit()

        portal_url = current_app.config['BASE_URL'].rstrip('/') + url_for('portal.view_quote', token=quote.public_token)
        send_quote_email(quote, portal_url)
        flash(f'Quote sent to {quote.client.email}.', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error sending quote: {str(e)}")
        flash(f'Error sending quote: {str(e)}', 'danger')

    return redirect(url_for('billing.quote_detail', quote_id=quote.id))


@billing_bp.route('/quotes/<int:quote_id>/delete')
@login_required
def quote_delete(quote_id):
    """Delete a quote"""
    quote = Quote.query.get_or_404(quote_id)
    try:
        db.session.delete(quote)
        db.session.commit()
        flash('Quote deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting quote: {str(e)}', 'danger')
    return redirect(url_for('billing.quotes_list'))


@billing_bp.route('/quotes/<int:quote_id>/convert', methods=['POST'])
@login_required
def quote_convert_to_invoice(quote_id):
    """Create a draft invoice pre-filled from an accepted quote"""
    quote = Quote.query.get_or_404(quote_id)
    if quote.status != QuoteStatus.ACCEPTED:
        flash('Only accepted quotes can be converted to an invoice.', 'warning')
        return redirect(url_for('billing.quote_detail', quote_id=quote.id))

    try:
        invoice = Invoice(
            invoice_number='PENDING',
            client_id=quote.client_id,
            project_id=quote.project_id,
            quote_id=quote.id,
            created_by_id=current_user.id,
            title=quote.title,
            currency=quote.currency,
            tax_rate=quote.tax_rate,
            notes=quote.notes,
        )
        db.session.add(invoice)
        db.session.flush()
        invoice.invoice_number = f"INV-{invoice.id:05d}"

        for item in quote.items:
            db.session.add(InvoiceItem(
                invoice_id=invoice.id,
                description=item.description,
                quantity=item.quantity,
                unit_price=item.unit_price,
            ))

        db.session.commit()
        flash(f'Invoice {invoice.invoice_number} created from quote {quote.quote_number}. Review it before sending.', 'success')
        return redirect(url_for('billing.invoice_edit', invoice_id=invoice.id))
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error converting quote to invoice: {str(e)}")
        flash(f'Error converting quote: {str(e)}', 'danger')
        return redirect(url_for('billing.quote_detail', quote_id=quote.id))


# --------------------------------------------------------------------------
# Invoices
# --------------------------------------------------------------------------

@billing_bp.route('/invoices')
@login_required
def invoices_list():
    """List all invoices"""
    # Paginated so the page doesn't load every invoice row on every visit.
    page = request.args.get('page', 1, type=int)
    pagination = Invoice.query.order_by(Invoice.created_at.desc()).paginate(page=page, per_page=20, error_out=False)
    return render_template('invoices/index.html', invoices=pagination.items, pagination=pagination)


@billing_bp.route('/invoices/create', methods=['GET', 'POST'])
@login_required
def invoice_create():
    """Create a new invoice"""
    form = InvoiceForm()

    if form.validate_on_submit():
        items = parse_line_items(request.form)
        if not items:
            flash('Add at least one line item before saving the invoice.', 'danger')
        else:
            try:
                invoice = Invoice(
                    invoice_number='PENDING',
                    client_id=form.client_id.data,
                    project_id=form.project_id.data or None,
                    created_by_id=current_user.id,
                    title=form.title.data,
                    currency=form.currency.data,
                    tax_rate=form.tax_rate.data or 0,
                    due_date=form.due_date.data,
                    notes=form.notes.data,
                )
                db.session.add(invoice)
                db.session.flush()
                invoice.invoice_number = f"INV-{invoice.id:05d}"

                for item in items:
                    db.session.add(InvoiceItem(invoice_id=invoice.id, **item))

                db.session.commit()
                flash(f'Invoice {invoice.invoice_number} created.', 'success')
                return redirect(url_for('billing.invoice_detail', invoice_id=invoice.id))
            except Exception as e:
                db.session.rollback()
                current_app.logger.error(f"Error creating invoice: {str(e)}")
                flash(f'Error creating invoice: {str(e)}', 'danger')

    return render_template('invoices/form.html', form=form, invoice=None, items=[])


@billing_bp.route('/invoices/<int:invoice_id>')
@login_required
def invoice_detail(invoice_id):
    """View an invoice (staff view)"""
    invoice = Invoice.query.get_or_404(invoice_id)
    portal_url = current_app.config['BASE_URL'].rstrip('/') + url_for('portal.view_invoice', token=invoice.public_token)
    return render_template('invoices/detail.html', invoice=invoice, portal_url=portal_url)


@billing_bp.route('/invoices/<int:invoice_id>/print')
@login_required
def invoice_print(invoice_id):
    """Standalone printable/PDF view of an invoice (staff view)"""
    invoice = Invoice.query.get_or_404(invoice_id)
    return render_template('print/document.html', **build_print_context(invoice, 'Invoice'))


@billing_bp.route('/invoices/<int:invoice_id>/edit', methods=['GET', 'POST'])
@login_required
def invoice_edit(invoice_id):
    """Edit an invoice. Allowed for draft and sent invoices (not yet paid) -
    blocked once paid or cancelled, since silently changing a paid invoice's
    amount after the fact would be misleading."""
    invoice = Invoice.query.get_or_404(invoice_id)
    if invoice.status not in (InvoiceStatus.DRAFT, InvoiceStatus.SENT):
        flash('This invoice has already been paid or cancelled and can no longer be edited.', 'warning')
        return redirect(url_for('billing.invoice_detail', invoice_id=invoice.id))

    form = InvoiceForm(obj=invoice)

    if form.validate_on_submit():
        items = parse_line_items(request.form)
        if not items:
            flash('Add at least one line item before saving the invoice.', 'danger')
        else:
            try:
                invoice.client_id = form.client_id.data
                invoice.project_id = form.project_id.data or None
                invoice.title = form.title.data
                invoice.currency = form.currency.data
                invoice.tax_rate = form.tax_rate.data or 0
                invoice.due_date = form.due_date.data
                invoice.notes = form.notes.data

                InvoiceItem.query.filter_by(invoice_id=invoice.id).delete()
                for item in items:
                    db.session.add(InvoiceItem(invoice_id=invoice.id, **item))

                db.session.commit()
                flash(f'Invoice {invoice.invoice_number} updated.', 'success')
                return redirect(url_for('billing.invoice_detail', invoice_id=invoice.id))
            except Exception as e:
                db.session.rollback()
                current_app.logger.error(f"Error updating invoice: {str(e)}")
                flash(f'Error updating invoice: {str(e)}', 'danger')
    elif request.method == 'GET':
        form.project_id.data = invoice.project_id or 0

    return render_template('invoices/form.html', form=form, invoice=invoice, items=invoice.items)


@billing_bp.route('/invoices/<int:invoice_id>/send', methods=['POST'])
@login_required
def invoice_send(invoice_id):
    """Email the client a portal link to view and pay this invoice"""
    invoice = Invoice.query.get_or_404(invoice_id)
    if not invoice.client.email:
        flash('This client has no email address on file. Add one before sending.', 'danger')
        return redirect(url_for('billing.invoice_detail', invoice_id=invoice.id))

    try:
        invoice.status = InvoiceStatus.SENT
        invoice.sent_at = datetime.utcnow()
        db.session.commit()

        portal_url = current_app.config['BASE_URL'].rstrip('/') + url_for('portal.view_invoice', token=invoice.public_token)
        send_invoice_email(invoice, portal_url)
        flash(f'Invoice sent to {invoice.client.email}.', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error sending invoice: {str(e)}")
        flash(f'Error sending invoice: {str(e)}', 'danger')

    return redirect(url_for('billing.invoice_detail', invoice_id=invoice.id))


@billing_bp.route('/invoices/<int:invoice_id>/mark-paid', methods=['POST'])
@login_required
def invoice_mark_paid(invoice_id):
    """Manually mark an invoice as paid (e.g. cash or bank transfer received offline)"""
    invoice = Invoice.query.get_or_404(invoice_id)
    try:
        invoice.status = InvoiceStatus.PAID
        invoice.paid_at = datetime.utcnow()
        invoice.payment_method = request.form.get('payment_method', 'manual')
        db.session.commit()
        flash(f'Invoice {invoice.invoice_number} marked as paid.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error marking invoice as paid: {str(e)}', 'danger')
    return redirect(url_for('billing.invoice_detail', invoice_id=invoice.id))


@billing_bp.route('/invoices/<int:invoice_id>/delete')
@login_required
def invoice_delete(invoice_id):
    """Delete an invoice"""
    invoice = Invoice.query.get_or_404(invoice_id)
    try:
        db.session.delete(invoice)
        db.session.commit()
        flash('Invoice deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting invoice: {str(e)}', 'danger')
    return redirect(url_for('billing.invoices_list'))
