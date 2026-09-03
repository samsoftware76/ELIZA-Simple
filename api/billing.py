"""
Staff-facing Quote and Invoice management for the ELIZA Project Management App.

The client-facing counterpart (view/accept/decline a quote, view/pay an
invoice via a tokenized link, no login required) lives in api/portal.py.
"""
from datetime import datetime, date, timedelta
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, abort
from flask_login import login_required, current_user

from models import db
from models.models import Client, Project, User
from models.billing import Quote, QuoteItem, Invoice, InvoiceItem, QuoteStatus, InvoiceStatus
from forms import QuoteForm, InvoiceForm
from utils.email_utils import send_quote_email, send_invoice_email
from utils.document_print import build_print_context
from utils.analytics import log_event

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


def create_invoice_from_quote(quote, created_by_id):
    """Shared core of quote -> invoice conversion, used by both the staff
    convert route below and the client portal's accept-and-pay flow
    (api/portal.py imports this; billing.py must never import portal.py or
    that becomes circular).

    Copies title/currency/tax/notes and every line item, links the invoice
    back to the quote, and assigns the INV-xxxxx number. Adds to the session
    and flushes (so invoice.id/number exist) but does NOT commit - the caller
    owns the transaction and decides status/sent_at before committing.

    due_date: if the tenant owner has default_payment_terms_days set (the
    /profile invoice-defaults card), the invoice is due that many days from
    today; otherwise it stays unset as before. Applied HERE in the shared
    helper (not per call site) deliberately, so both paths behave the same:
    the portal accept flow commits the invoice as SENT immediately, and the
    staff convert route lands on invoice_edit where the prefilled date is
    just an initial value the user can still change before saving.
    """
    # The tenant is resolved from the quote's creator, not current_user -
    # the portal accept path has no logged-in user at all.
    creator = User.query.get(created_by_id)
    owner = User.query.get(creator.get_owner_id()) if creator else None
    due_date = None
    if owner and owner.default_payment_terms_days is not None:
        due_date = date.today() + timedelta(days=owner.default_payment_terms_days)

    invoice = Invoice(
        invoice_number='PENDING',
        client_id=quote.client_id,
        project_id=quote.project_id,
        quote_id=quote.id,
        created_by_id=created_by_id,
        title=quote.title,
        currency=quote.currency,
        tax_rate=quote.tax_rate,
        due_date=due_date,
        notes=quote.notes,
    )
    db.session.add(invoice)
    db.session.flush()  # assigns invoice.id for numbering, still inside the transaction
    invoice.invoice_number = f"INV-{invoice.id:05d}"

    for item in quote.items:
        db.session.add(InvoiceItem(
            invoice_id=invoice.id,
            description=item.description,
            quantity=item.quantity,
            unit_price=item.unit_price,
        ))
    return invoice


# --------------------------------------------------------------------------
# Quotes
# --------------------------------------------------------------------------

@billing_bp.route('/quotes')
@login_required
def quotes_list():
    """List all quotes"""
    # Paginated so the page doesn't load every quote row on every visit.
    page = request.args.get('page', 1, type=int)
    # Quote has no owner_id of its own - scope through its required client_id.
    pagination = Quote.query.join(Client).filter(Client.owner_id == current_user.get_owner_id()) \
        .order_by(Quote.created_at.desc()).paginate(page=page, per_page=20, error_out=False)
    return render_template('quotes/index.html', quotes=pagination.items, pagination=pagination)


@billing_bp.route('/quotes/create', methods=['GET', 'POST'])
@login_required
def quote_create():
    """Create a new quote"""
    # owner_id scopes the client_id/project_id dropdowns to this user's own records.
    form = QuoteForm(owner_id=current_user.get_owner_id())

    if request.method == 'GET':
        # Prefill the EMPTY form from the tenant owner's invoice defaults
        # (set on the /profile invoice-defaults card). GET only - a submitted
        # form's own values always win, defaults never overwrite user input.
        owner = User.query.get(current_user.get_owner_id())
        if owner.default_currency:
            form.currency.data = owner.default_currency
        if owner.default_tax_rate is not None:
            form.tax_rate.data = owner.default_tax_rate

    if form.validate_on_submit():
        items = parse_line_items(request.form)
        # Defense in depth: the form's client/project choices are already scoped to
        # this user's own records, but a crafted POST can submit any id directly -
        # re-check ownership server-side before trusting these foreign keys.
        client = Client.query.get(form.client_id.data)
        project = Project.query.get(form.project_id.data) if form.project_id.data else None
        if not items:
            flash('Add at least one line item before saving the quote.', 'danger')
        elif not client or client.owner_id != current_user.get_owner_id() or (project and project.owner_id != current_user.get_owner_id()):
            flash('Invalid client or project selected.', 'danger')
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
                log_event('quote_created', user_id=current_user.id, quote_id=quote.id)
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
    # Quote has no owner_id of its own - ownership is via its required client.
    if quote.client.owner_id != current_user.get_owner_id():
        abort(404)
    portal_url = current_app.config['BASE_URL'].rstrip('/') + url_for('portal.view_quote', token=quote.public_token)
    return render_template('quotes/detail.html', quote=quote, portal_url=portal_url)


@billing_bp.route('/quotes/<int:quote_id>/print')
@login_required
def quote_print(quote_id):
    """Standalone printable/PDF view of a quote (staff view)"""
    quote = Quote.query.get_or_404(quote_id)
    # Quote has no owner_id of its own - ownership is via its required client.
    if quote.client.owner_id != current_user.get_owner_id():
        abort(404)
    return render_template('print/document.html', **build_print_context(quote, 'Quote'))


@billing_bp.route('/quotes/<int:quote_id>/edit', methods=['GET', 'POST'])
@login_required
def quote_edit(quote_id):
    """Edit a quote. Allowed for draft and sent quotes (the client hasn't
    responded yet) - blocked once the client has accepted/declined, since
    silently changing terms after a response is misleading."""
    quote = Quote.query.get_or_404(quote_id)
    # Quote has no owner_id of its own - ownership is via its required client.
    if quote.client.owner_id != current_user.get_owner_id():
        abort(404)
    if quote.status not in (QuoteStatus.DRAFT, QuoteStatus.SENT):
        flash('This quote has already been responded to and can no longer be edited.', 'warning')
        return redirect(url_for('billing.quote_detail', quote_id=quote.id))

    # owner_id scopes the client_id/project_id dropdowns to this user's own records.
    form = QuoteForm(obj=quote, owner_id=current_user.get_owner_id())

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
    # Quote has no owner_id of its own - ownership is via its required client.
    if quote.client.owner_id != current_user.get_owner_id():
        abort(404)
    if not quote.client.email:
        flash('This client has no email address on file. Add one on their record, then send.', 'danger')
        return redirect(url_for('billing.quote_detail', quote_id=quote.id))

    try:
        quote.status = QuoteStatus.SENT
        quote.sent_at = datetime.utcnow()
        db.session.commit()
        log_event('quote_sent', user_id=current_user.id, quote_id=quote.id)

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
    # Quote has no owner_id of its own - ownership is via its required client.
    if quote.client.owner_id != current_user.get_owner_id():
        abort(404)
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
    # Quote has no owner_id of its own - ownership is via its required client.
    if quote.client.owner_id != current_user.get_owner_id():
        abort(404)
    if quote.status != QuoteStatus.ACCEPTED:
        flash('Only accepted quotes can be converted to an invoice.', 'warning')
        return redirect(url_for('billing.quote_detail', quote_id=quote.id))

    try:
        invoice = create_invoice_from_quote(quote, current_user.id)
        db.session.commit()
        flash(f'Invoice {invoice.invoice_number} drafted from quote {quote.quote_number}. It is not sent yet - check it, then send.', 'success')
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
    # Invoice has no owner_id of its own - scope through its required client_id.
    pagination = Invoice.query.join(Client).filter(Client.owner_id == current_user.get_owner_id()) \
        .order_by(Invoice.created_at.desc()).paginate(page=page, per_page=20, error_out=False)
    return render_template('invoices/index.html', invoices=pagination.items, pagination=pagination)


@billing_bp.route('/invoices/create', methods=['GET', 'POST'])
@login_required
def invoice_create():
    """Create a new invoice"""
    # owner_id scopes the client_id/project_id dropdowns to this user's own records.
    form = InvoiceForm(owner_id=current_user.get_owner_id())

    if request.method == 'GET':
        # Prefill the EMPTY form from the tenant owner's invoice defaults
        # (set on the /profile invoice-defaults card). GET only - a submitted
        # form's own values always win, defaults never overwrite user input.
        owner = User.query.get(current_user.get_owner_id())
        if owner.default_currency:
            form.currency.data = owner.default_currency
        if owner.default_tax_rate is not None:
            form.tax_rate.data = owner.default_tax_rate
        if owner.default_payment_terms_days is not None:
            form.due_date.data = date.today() + timedelta(days=owner.default_payment_terms_days)

    if form.validate_on_submit():
        items = parse_line_items(request.form)
        # Defense in depth: the form's client/project choices are already scoped to
        # this user's own records, but a crafted POST can submit any id directly -
        # re-check ownership server-side before trusting these foreign keys.
        client = Client.query.get(form.client_id.data)
        project = Project.query.get(form.project_id.data) if form.project_id.data else None
        if not items:
            flash('Add at least one line item before saving the invoice.', 'danger')
        elif not client or client.owner_id != current_user.get_owner_id() or (project and project.owner_id != current_user.get_owner_id()):
            flash('Invalid client or project selected.', 'danger')
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
                log_event('invoice_created', user_id=current_user.id, invoice_id=invoice.id)
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
    # Invoice has no owner_id of its own - ownership is via its required client.
    if invoice.client.owner_id != current_user.get_owner_id():
        abort(404)
    portal_url = current_app.config['BASE_URL'].rstrip('/') + url_for('portal.view_invoice', token=invoice.public_token)
    return render_template('invoices/detail.html', invoice=invoice, portal_url=portal_url)


@billing_bp.route('/invoices/<int:invoice_id>/print')
@login_required
def invoice_print(invoice_id):
    """Standalone printable/PDF view of an invoice (staff view)"""
    invoice = Invoice.query.get_or_404(invoice_id)
    # Invoice has no owner_id of its own - ownership is via its required client.
    if invoice.client.owner_id != current_user.get_owner_id():
        abort(404)
    return render_template('print/document.html', **build_print_context(invoice, 'Invoice'))


@billing_bp.route('/invoices/<int:invoice_id>/edit', methods=['GET', 'POST'])
@login_required
def invoice_edit(invoice_id):
    """Edit an invoice. Allowed for draft and sent invoices (not yet paid) -
    blocked once paid or cancelled, since silently changing a paid invoice's
    amount after the fact would be misleading."""
    invoice = Invoice.query.get_or_404(invoice_id)
    # Invoice has no owner_id of its own - ownership is via its required client.
    if invoice.client.owner_id != current_user.get_owner_id():
        abort(404)
    if invoice.status not in (InvoiceStatus.DRAFT, InvoiceStatus.SENT):
        flash('This invoice has already been paid or cancelled and can no longer be edited.', 'warning')
        return redirect(url_for('billing.invoice_detail', invoice_id=invoice.id))

    # owner_id scopes the client_id/project_id dropdowns to this user's own records.
    form = InvoiceForm(obj=invoice, owner_id=current_user.get_owner_id())

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
    # Invoice has no owner_id of its own - ownership is via its required client.
    if invoice.client.owner_id != current_user.get_owner_id():
        abort(404)
    if not invoice.client.email:
        flash('This client has no email address on file. Add one on their record, then send.', 'danger')
        return redirect(url_for('billing.invoice_detail', invoice_id=invoice.id))

    try:
        invoice.status = InvoiceStatus.SENT
        invoice.sent_at = datetime.utcnow()
        db.session.commit()
        log_event('invoice_sent', user_id=current_user.id, invoice_id=invoice.id)

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
    # Invoice has no owner_id of its own - ownership is via its required client.
    if invoice.client.owner_id != current_user.get_owner_id():
        abort(404)
    try:
        invoice.status = InvoiceStatus.PAID
        invoice.paid_at = datetime.utcnow()
        invoice.payment_method = request.form.get('payment_method', 'manual')
        db.session.commit()
        log_event('invoice_paid', user_id=invoice.created_by_id, invoice_id=invoice.id, amount=invoice.total)
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
    # Invoice has no owner_id of its own - ownership is via its required client.
    if invoice.client.owner_id != current_user.get_owner_id():
        abort(404)
    try:
        db.session.delete(invoice)
        db.session.commit()
        flash('Invoice deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting invoice: {str(e)}', 'danger')
    return redirect(url_for('billing.invoices_list'))
