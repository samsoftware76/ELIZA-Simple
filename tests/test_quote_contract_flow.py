"""
The core business loop: quote -> accept -> auto-invoice, and contract ->
send -> sign, including the amendment/void escape hatches around contract
immutability.

Covers, in order:
  * Creating a quote with line items computes correct subtotal/tax/total.
  * Sending a quote sets its status and it carries a public_token.
  * Accepting a quote via the portal token (no login - the plain `client`
    fixture, never logged in) auto-creates exactly one invoice with matching
    items and total, sets it to SENT, and re-accepting does NOT create a
    second invoice (idempotency - the exact bug class fixed this session).
  * Sending a contract freezes body_snapshot and further edits are refused.
  * Signing a contract via the portal token records signer_name/
    signature_image/signed_at/signer_ip, and a second sign attempt does not
    overwrite the first.
  * Voiding a SENT contract blocks signing; voiding a SIGNED one is refused.
  * Amending a signed contract creates a new draft version linked via
    supersedes_id, while the original is provably untouched.
"""
from conftest import login_as, get_csrf_token
from models import db
from models.billing import Quote, Invoice, Contract, QuoteStatus, InvoiceStatus, ContractStatus


def _owner_token(client):
    """A valid CSRF token for the logged-in staff session."""
    return get_csrf_token(client, '/clients/create')


def _portal_token(client, portal_url):
    """Visits the portal page (mirroring a real client opening the emailed
    link, and establishing the anonymous session in the process) and returns
    a valid CSRF token for that session.

    The token itself is scraped from the public /login page, not the portal
    page's own hidden field: once a quote/contract is no longer respondable/
    signable (already accepted/signed/declined/voided), the portal page stops
    rendering its accept/sign/decline form at all - see e.g. contract.html's
    `{% if contract.is_signable %}` - leaving nothing there to scrape. A
    Flask-WTF CSRF token is bound to the SESSION's stored secret, not to the
    specific page it was rendered on (templates/portal/*.html render it via
    a plain `csrf_token()` call, the same mechanism /login's hidden_tag()
    uses), so any always-present anonymous form works just as well.
    """
    client.get(portal_url)
    return get_csrf_token(client, '/login')


# ---------------------------------------------------------------------------
# Quote line items -> subtotal/tax/total
# ---------------------------------------------------------------------------

def test_quote_line_items_compute_correct_subtotal_tax_total(client, owner_user, make_client):
    login_as(client, owner_user)
    a_client = make_client(owner_user)
    token = _owner_token(client)

    resp = client.post('/quotes/create', data={
        'client_id': str(a_client.id),
        'project_id': '0',
        'title': 'Website redesign',
        'currency': 'USD',
        'tax_rate': '10',
        'valid_until': '',
        'notes': '',
        'item_description[]': ['Design', 'Development'],
        'item_quantity[]': ['2', '1'],
        'item_unit_price[]': ['50', '25.5'],
        'submit': 'Save Quote',
        'csrf_token': token,
    }, follow_redirects=True)
    assert resp.status_code == 200

    quote = Quote.query.filter_by(client_id=a_client.id).first()
    assert quote is not None
    # subtotal = 2*50 + 1*25.5 = 125.5
    assert quote.subtotal == 125.5
    # tax = 125.5 * 10% = 12.55
    assert quote.tax_amount == 12.55
    # total = 125.5 + 12.55 = 138.05
    assert quote.total == 138.05
    assert len(quote.items) == 2


# ---------------------------------------------------------------------------
# Sending a quote
# ---------------------------------------------------------------------------

def test_sending_a_quote_sets_status_and_has_a_public_token(client, owner_user, make_client, make_quote):
    login_as(client, owner_user)
    a_client = make_client(owner_user)
    quote = make_quote(a_client, owner_user)
    assert quote.status == QuoteStatus.DRAFT

    token = _owner_token(client)
    resp = client.post(f'/quotes/{quote.id}/send', data={'csrf_token': token}, follow_redirects=True)
    assert resp.status_code == 200

    db.session.refresh(quote)
    assert quote.status == QuoteStatus.SENT
    assert quote.sent_at is not None
    assert quote.public_token
    assert isinstance(quote.public_token, str) and len(quote.public_token) > 10


# ---------------------------------------------------------------------------
# Accepting a quote via the portal - auto-invoice + idempotency
# ---------------------------------------------------------------------------

def _send_quote_directly(quote):
    """Move a factory-made quote straight to SENT without going through the
    HTTP route - these tests are about the portal accept flow, not re-testing
    quote_send() (covered above)."""
    quote.status = QuoteStatus.SENT
    db.session.commit()
    return quote


def test_accepting_a_quote_via_portal_creates_exactly_one_matching_invoice(client, owner_user, make_client, make_quote):
    a_client = make_client(owner_user)
    quote = make_quote(a_client, owner_user, tax_rate=10, items=[
        {'description': 'Consulting', 'quantity': 3, 'unit_price': 40.0},
    ])
    _send_quote_directly(quote)

    portal_url = f'/portal/quote/{quote.public_token}'
    token = _portal_token(client, portal_url)  # client is never logged in here
    resp = client.post(f'{portal_url}/accept', data={'csrf_token': token}, follow_redirects=True)
    assert resp.status_code == 200

    db.session.refresh(quote)
    assert quote.status == QuoteStatus.ACCEPTED

    invoices = Invoice.query.filter_by(quote_id=quote.id).all()
    assert len(invoices) == 1
    invoice = invoices[0]
    assert invoice.status == InvoiceStatus.SENT
    assert invoice.sent_at is not None
    assert len(invoice.items) == 1
    assert invoice.items[0].description == 'Consulting'
    assert invoice.items[0].quantity == 3
    assert invoice.items[0].unit_price == 40.0
    assert invoice.total == quote.total


def test_reaccepting_a_quote_does_not_create_a_second_invoice(client, owner_user, make_client, make_quote):
    a_client = make_client(owner_user)
    quote = make_quote(a_client, owner_user)
    _send_quote_directly(quote)

    portal_url = f'/portal/quote/{quote.public_token}'
    token = _portal_token(client, portal_url)
    first = client.post(f'{portal_url}/accept', data={'csrf_token': token}, follow_redirects=True)
    assert first.status_code == 200
    assert Invoice.query.filter_by(quote_id=quote.id).count() == 1
    first_invoice_id = Invoice.query.filter_by(quote_id=quote.id).first().id

    # Double-submit / back-button re-accept - quote.is_respondable is now
    # False (status is ACCEPTED, not SENT), so the portal page no longer
    # renders an accept/decline form at all (nothing left to scrape a fresh
    # token from) - reuse the token already captured above instead. A
    # Flask-WTF CSRF token is bound to the session's stored secret, not
    # single-use, so the same token validates again here.
    second = client.post(f'{portal_url}/accept', data={'csrf_token': token}, follow_redirects=True)
    assert second.status_code == 200

    invoices = Invoice.query.filter_by(quote_id=quote.id).all()
    assert len(invoices) == 1
    assert invoices[0].id == first_invoice_id


# ---------------------------------------------------------------------------
# Contract: send freezes body_snapshot, further edits refused
# ---------------------------------------------------------------------------

def test_sending_a_contract_freezes_body_snapshot_and_blocks_further_edits(client, owner_user, make_client, make_contract):
    login_as(client, owner_user)
    a_client = make_client(owner_user)
    contract = make_contract(a_client, owner_user, body='Original terms of engagement.')
    token = _owner_token(client)

    resp = client.post(f'/contracts/{contract.id}/send', data={'csrf_token': token}, follow_redirects=True)
    assert resp.status_code == 200

    db.session.refresh(contract)
    assert contract.status == ContractStatus.SENT
    assert contract.body_snapshot == 'Original terms of engagement.'

    # Further edit attempt - refused before the form is even processed.
    token2 = _owner_token(client)
    edit_resp = client.post(f'/contracts/{contract.id}/edit', data={
        'client_id': str(a_client.id),
        'project_id': '0',
        'title': 'Renamed',
        'body': 'Sneaky rewritten terms.',
        'submit': 'Save Contract',
        'csrf_token': token2,
    }, follow_redirects=True)
    assert edit_resp.status_code == 200
    assert 'cannot be edited after sending' in edit_resp.get_data(as_text=True)

    db.session.refresh(contract)
    assert contract.body == 'Original terms of engagement.'
    assert contract.body_snapshot == 'Original terms of engagement.'
    assert contract.title != 'Renamed'


# ---------------------------------------------------------------------------
# Contract: signing via the portal, idempotent
# ---------------------------------------------------------------------------

def _create_and_send_contract(client, owner_user, a_client, make_contract, body='Terms.'):
    login_as(client, owner_user)
    contract = make_contract(a_client, owner_user, body=body)
    token = _owner_token(client)
    resp = client.post(f'/contracts/{contract.id}/send', data={'csrf_token': token}, follow_redirects=True)
    assert resp.status_code == 200
    db.session.refresh(contract)
    assert contract.status == ContractStatus.SENT
    client.get('/logout')
    return contract


def test_signing_a_contract_via_portal_records_full_audit_trail(client, owner_user, make_client, make_contract):
    a_client = make_client(owner_user)
    contract = _create_and_send_contract(client, owner_user, a_client, make_contract)

    portal_url = f'/portal/contract/{contract.public_token}'
    token = _portal_token(client, portal_url)
    resp = client.post(f'{portal_url}/sign', data={
        'signer_name': 'Jane Doe',
        'consent': 'on',
        'signature_image': 'data:image/png;base64,AAAABBBBCCCC',
        'csrf_token': token,
    }, follow_redirects=True)
    assert resp.status_code == 200

    db.session.refresh(contract)
    assert contract.status == ContractStatus.SIGNED
    assert contract.signer_name == 'Jane Doe'
    assert contract.signature_image == 'data:image/png;base64,AAAABBBBCCCC'
    assert contract.signed_at is not None
    assert contract.signer_ip  # remote_addr, best-effort - just must be recorded


def test_second_sign_attempt_does_not_overwrite_the_first_signature(client, owner_user, make_client, make_contract):
    a_client = make_client(owner_user)
    contract = _create_and_send_contract(client, owner_user, a_client, make_contract)

    portal_url = f'/portal/contract/{contract.public_token}'
    token = _portal_token(client, portal_url)
    client.post(f'{portal_url}/sign', data={
        'signer_name': 'Jane Doe', 'consent': 'on', 'csrf_token': token,
    }, follow_redirects=True)
    db.session.refresh(contract)
    first_signed_at = contract.signed_at
    assert contract.signer_name == 'Jane Doe'

    # The portal page no longer renders a sign form once SIGNED, so there is
    # nothing left to scrape a fresh token from - reuse the one already
    # captured above (not single-use, see the reaccept-quote test's comment).
    resp = client.post(f'{portal_url}/sign', data={
        'signer_name': 'Someone Else', 'consent': 'on', 'csrf_token': token,
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert 'already been signed' in resp.get_data(as_text=True)

    db.session.refresh(contract)
    assert contract.signer_name == 'Jane Doe'
    assert contract.signed_at == first_signed_at


# ---------------------------------------------------------------------------
# Contract: void blocks signing on a SENT contract; voiding SIGNED is refused
# ---------------------------------------------------------------------------

def test_voiding_a_sent_contract_blocks_signing(client, owner_user, make_client, make_contract):
    a_client = make_client(owner_user)
    contract = _create_and_send_contract(client, owner_user, a_client, make_contract)

    login_as(client, owner_user)
    token = _owner_token(client)
    void_resp = client.post(f'/contracts/{contract.id}/void', data={'csrf_token': token}, follow_redirects=True)
    assert void_resp.status_code == 200
    db.session.refresh(contract)
    assert contract.status == ContractStatus.VOIDED
    client.get('/logout')

    portal_url = f'/portal/contract/{contract.public_token}'
    sign_token = _portal_token(client, portal_url)
    sign_resp = client.post(f'{portal_url}/sign', data={
        'signer_name': 'Too Late', 'consent': 'on', 'csrf_token': sign_token,
    }, follow_redirects=True)
    assert sign_resp.status_code == 200
    assert 'withdrawn' in sign_resp.get_data(as_text=True).lower()

    db.session.refresh(contract)
    assert contract.status == ContractStatus.VOIDED
    assert contract.signer_name is None


def test_voiding_a_signed_contract_is_refused(client, owner_user, make_client, make_contract):
    a_client = make_client(owner_user)
    contract = _create_and_send_contract(client, owner_user, a_client, make_contract)

    portal_url = f'/portal/contract/{contract.public_token}'
    sign_token = _portal_token(client, portal_url)
    client.post(f'{portal_url}/sign', data={
        'signer_name': 'Jane Doe', 'consent': 'on', 'csrf_token': sign_token,
    }, follow_redirects=True)
    db.session.refresh(contract)
    assert contract.status == ContractStatus.SIGNED

    login_as(client, owner_user)
    token = _owner_token(client)
    void_resp = client.post(f'/contracts/{contract.id}/void', data={'csrf_token': token}, follow_redirects=True)
    assert void_resp.status_code == 200
    assert 'cannot be voided' in void_resp.get_data(as_text=True).lower()

    db.session.refresh(contract)
    assert contract.status == ContractStatus.SIGNED


# ---------------------------------------------------------------------------
# Contract: amending a signed contract
# ---------------------------------------------------------------------------

def test_amending_a_signed_contract_creates_new_version_and_leaves_original_untouched(
        client, owner_user, make_client, make_contract):
    a_client = make_client(owner_user)
    contract = _create_and_send_contract(client, owner_user, a_client, make_contract, body='V1 terms.')

    portal_url = f'/portal/contract/{contract.public_token}'
    sign_token = _portal_token(client, portal_url)
    client.post(f'{portal_url}/sign', data={
        'signer_name': 'Jane Doe', 'consent': 'on', 'csrf_token': sign_token,
    }, follow_redirects=True)
    db.session.refresh(contract)
    assert contract.status == ContractStatus.SIGNED

    original_id = contract.id
    original_number = contract.contract_number
    original_body = contract.body
    original_body_snapshot = contract.body_snapshot
    original_signer = contract.signer_name
    original_signed_at = contract.signed_at
    original_public_token = contract.public_token

    login_as(client, owner_user)
    token = _owner_token(client)
    amend_resp = client.post(f'/contracts/{original_id}/amend', data={'csrf_token': token}, follow_redirects=True)
    assert amend_resp.status_code == 200

    amendment = Contract.query.filter_by(supersedes_id=original_id).first()
    assert amendment is not None
    assert amendment.status == ContractStatus.DRAFT
    assert amendment.version == 2
    assert amendment.body == original_body_snapshot
    assert amendment.contract_number != original_number
    assert amendment.public_token != original_public_token

    # The original is provably untouched - status, signature, body, token.
    original = Contract.query.get(original_id)
    assert original.status == ContractStatus.SIGNED
    assert original.body == original_body
    assert original.body_snapshot == original_body_snapshot
    assert original.signer_name == original_signer
    assert original.signed_at == original_signed_at
    assert original.public_token == original_public_token
