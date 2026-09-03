"""
Account statement builder for the ELIZA Project Management App.

Compiles a formal, bank/auditor-ready view of one tenant's invoiced,
collected and outstanding activity over a date range (see api/index.py's
wallet_statement* routes and api/admin.py's statement_generate for the
any-tenant compliance case). Mirrors utils/deliverables_report.py's shape:
a single pure builder function returning one plain dict, so the on-screen
preview, the print/PDF view and the Excel export all read the exact same
computed totals and can never disagree with each other.

Every query is scoped by the owner_id the caller passes in - this module
trusts that owner_id (and client_id, when given) is already the caller's
own tenant scope (the route is responsible for that, exactly like
build_deliverables_report's contract), and additionally filters every
query by owner_id/client_id itself rather than trusting a bare foreign key,
same layered-scoping style as the rest of this codebase.

Date field choice (see the BUILD spec this was written against):
  - Invoiced uses Invoice.sent_at, not created_at - a draft invoice isn't a
    real obligation yet, sent_at is the moment it became one, and a draft
    (sent_at is NULL) can never fall inside a date range on this field, so
    drafts are naturally excluded rather than needing an extra status filter.
  - Collected uses Invoice.paid_at - independent of whether the invoice was
    also *sent* inside this same period (a common, correct real-world case:
    something invoiced last quarter, paid this one).
  - Outstanding is a point-in-time snapshot "as of period_end": every
    invoice whose CURRENT status is SENT and whose sent_at is on or before
    period_end. ELIZA keeps no separate status-history table, so this is a
    live-status approximation, not a true historical reconstruction - an
    invoice sent inside the period but paid AFTER period_end will show here
    as outstanding (correct: it genuinely was, and still is if unpaid), but
    one sent inside the period and later paid (any time, including after
    period_end) will show only under Collected on whichever statement's
    range contains its paid_at, and will not appear here once paid. This
    matches how a bank statement's "amount due as of" line is always read:
    against the ledger's current state, not a full historical replay.

Never invents or estimates a number: every row here is one real Invoice or
Contract row, and every total is a plain Python sum of real .total values,
computed exactly once per section, grouped strictly by currency and never
summed across currencies.
"""
from datetime import datetime, time

from models.models import Client, User
from models.billing import Invoice, InvoiceStatus, Contract, ContractStatus


def _day_bounds(period_start, period_end):
    """[period_start, period_end] as inclusive datetime bounds, so a
    DateTime column (sent_at/paid_at/signed_at) is compared correctly
    against plain Date form inputs - the end date's whole day counts."""
    return datetime.combine(period_start, time.min), datetime.combine(period_end, time.max)


def _invoiced_rows(owner_id, client_id, start_dt, end_dt):
    q = Invoice.query.join(Client).filter(
        Client.owner_id == owner_id,
        Invoice.sent_at.isnot(None),
        Invoice.sent_at >= start_dt,
        Invoice.sent_at <= end_dt,
    )
    if client_id:
        q = q.filter(Invoice.client_id == client_id)
    invoices = q.order_by(Invoice.sent_at).all()

    by_currency = {}
    totals = {}
    for inv in invoices:
        code = inv.currency or 'USD'
        by_currency.setdefault(code, []).append({
            'invoice_number': inv.invoice_number,
            'client_name': inv.client.name,
            'date': inv.sent_at,
            'amount': inv.total,
            'status': inv.status,
        })
        totals[code] = round(totals.get(code, 0.0) + inv.total, 2)
    return by_currency, totals


def _collected_rows(owner_id, client_id, start_dt, end_dt):
    q = Invoice.query.join(Client).filter(
        Client.owner_id == owner_id,
        Invoice.status == InvoiceStatus.PAID,
        Invoice.paid_at.isnot(None),
        Invoice.paid_at >= start_dt,
        Invoice.paid_at <= end_dt,
    )
    if client_id:
        q = q.filter(Invoice.client_id == client_id)
    invoices = q.order_by(Invoice.paid_at).all()

    by_currency = {}
    totals = {}
    for inv in invoices:
        code = inv.currency or 'USD'
        # Traceability: invoice_mark_paid (api/billing.py) hardcodes
        # payment_method to the literal string 'manual' - every other path
        # to PAID is api/portal.py's PesaPal callback, which sets
        # payment_method from PesaPal's own transaction-status response
        # (a card/mobile-money network name, or the literal 'pesapal' as a
        # fallback - see build_print_context's neighboring comment) and is
        # therefore NEVER the literal string 'manual'. So this equality
        # check is the correct signal - a substring search for "pesapal" in
        # payment_method would be wrong: PesaPal often reports something
        # like "Visa" or "MPESA" there, not the word "pesapal" itself.
        is_pesapal = bool(inv.payment_method) and inv.payment_method != 'manual'
        by_currency.setdefault(code, []).append({
            'invoice_number': inv.invoice_number,
            'client_name': inv.client.name,
            'paid_at': inv.paid_at,
            'amount': inv.total,
            'is_pesapal': is_pesapal,
            'payment_method': inv.payment_method or 'manual',
            'pesapal_merchant_reference': inv.pesapal_merchant_reference if is_pesapal else None,
            'pesapal_order_tracking_id': inv.pesapal_order_tracking_id if is_pesapal else None,
            # GAP, not a guess: invoice_mark_paid (api/billing.py) never
            # records WHICH staff user clicked "Mark as Paid" - it stamps
            # paid_at/payment_method only. There is no column to read this
            # from, so a manually-marked row is reported as unrecorded
            # (None) rather than attributed to invoice.created_by_id (which
            # is merely who created the invoice, not who marked it paid, and
            # would be a guess). Doesn't apply at all to a PesaPal payment -
            # no staff member "marked" that one paid, the client did.
            'marked_paid_by': 'n/a' if is_pesapal else None,
        })
        totals[code] = round(totals.get(code, 0.0) + inv.total, 2)
    return by_currency, totals


def _outstanding_rows(owner_id, client_id, period_end, end_dt):
    q = Invoice.query.join(Client).filter(
        Client.owner_id == owner_id,
        Invoice.status == InvoiceStatus.SENT,
        Invoice.sent_at.isnot(None),
        Invoice.sent_at <= end_dt,
    )
    if client_id:
        q = q.filter(Invoice.client_id == client_id)
    invoices = q.order_by(Invoice.sent_at).all()

    by_currency = {}
    totals = {}
    for inv in invoices:
        code = inv.currency or 'USD'
        days = max((period_end - inv.sent_at.date()).days, 0)
        by_currency.setdefault(code, []).append({
            'invoice_number': inv.invoice_number,
            'client_name': inv.client.name,
            'sent_at': inv.sent_at,
            'due_date': inv.due_date,
            'amount': inv.total,
            'days_outstanding': days,
        })
        totals[code] = round(totals.get(code, 0.0) + inv.total, 2)
    return by_currency, totals


def _contracts_signed(owner_id, client_id, start_dt, end_dt):
    q = Contract.query.join(Client).filter(
        Client.owner_id == owner_id,
        Contract.status == ContractStatus.SIGNED,
        Contract.signed_at.isnot(None),
        Contract.signed_at >= start_dt,
        Contract.signed_at <= end_dt,
    )
    if client_id:
        q = q.filter(Contract.client_id == client_id)
    contracts = q.order_by(Contract.signed_at).all()
    return [{
        'contract_number': c.contract_number,
        'client_name': c.client.name,
        'signer_name': c.signer_name,
        'signed_at': c.signed_at,
    } for c in contracts]


def build_account_statement(owner_id, period_start, period_end, client_id=None):
    """Build the account statement dict for one tenant over [period_start,
    period_end] (both datetime.date), optionally scoped to one client_id.

    Assumes owner_id/client_id are already the caller's own, checked scope -
    same contract as build_deliverables_report.
    """
    owner = User.query.get(owner_id)
    client = Client.query.get(client_id) if client_id else None
    start_dt, end_dt = _day_bounds(period_start, period_end)

    invoiced_by_currency, invoiced_totals = _invoiced_rows(owner_id, client_id, start_dt, end_dt)
    collected_by_currency, collected_totals = _collected_rows(owner_id, client_id, start_dt, end_dt)
    outstanding_by_currency, outstanding_totals = _outstanding_rows(owner_id, client_id, period_end, end_dt)
    contracts_signed = _contracts_signed(owner_id, client_id, start_dt, end_dt)

    return {
        'owner': owner,
        'client': client,
        'period_start': period_start,
        'period_end': period_end,
        'invoiced_by_currency': invoiced_by_currency,
        'invoiced_totals': invoiced_totals,
        'collected_by_currency': collected_by_currency,
        'collected_totals': collected_totals,
        'outstanding_by_currency': outstanding_by_currency,
        'outstanding_totals': outstanding_totals,
        'contracts_signed': contracts_signed,
        'generated_at': datetime.utcnow(),
    }
