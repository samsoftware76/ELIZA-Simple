"""
Locks in the public-payment-surface hardening shipped this session:

  1. RATE LIMITS on api/portal.py, which previously had none at all despite
     being fully public (no login - only an unguessable token in the URL) and
     being where clients accept quotes, sign contracts and pay invoices.
     Views/prints are generous (60/min), actions are tight (10/min), and the
     PesaPal payment callback is EXEMPT.
  2. The CSP directives added to security.py: frame-ancestors, base-uri and
     form-action, plus the tightened img-src.
  3. HSTS being emitted only over HTTPS, and never over plain http (so
     http://localhost development is not pinned to https).

WHY THE RATE-LIMIT TESTS COUNT THE 429 HANDLER'S FLASH TEXT RATHER THAN
STATUS CODES: the 429 handler in api/index.py deliberately answers a
throttled portal ACTION by redirecting to the document's own view page - and
the un-throttled route also answers with a redirect to that same page. So a
302 proves nothing on its own: both the allowed and the blocked path return
one. The handler's flash wording is the only thing unique to the blocked
path, so these tests follow the redirect and look for it. (Confirmed the hard
way - a first version of this check asserted on status codes and "passed"
against a route that turned out not to be limited at all.)

MUTATION SANITY CHECK for this file: delete the @limiter.limit decorator from
portal.accept_quote and re-run - test_action_route_is_rate_limited must fail.
Delete @limiter.exempt from portal.invoice_payment_callback and
test_payment_callback_is_never_throttled must fail.
"""
import pytest

from models.billing import ContractStatus

# The exact flash text api/index.py's 429 handler emits for a throttled
# portal ACTION route. Nothing on the normal (allowed) path produces it.
RATE_LIMITED_FLASH = 'Too many requests in a row'

# Limits as configured in api/portal.py.
ACTION_LIMIT_PER_MIN = 10
VIEW_LIMIT_PER_MIN = 60


# ---------------------------------------------------------------------------
# Fixtures: one public, tokenized document of each kind
# ---------------------------------------------------------------------------

@pytest.fixture
def portal_quote(owner_user, make_client, make_quote):
    return make_quote(make_client(owner_user), owner_user)


@pytest.fixture
def portal_invoice(owner_user, make_client, make_invoice):
    return make_invoice(make_client(owner_user), owner_user)


@pytest.fixture
def portal_contract(owner_user, make_client, make_contract):
    """A contract in the state a client actually receives it in: SENT, with
    the frozen body_snapshot the portal renders (portal/contract.html reads
    body_snapshot, never the live working copy)."""
    return make_contract(
        make_client(owner_user), owner_user,
        status=ContractStatus.SENT,
        body_snapshot='Frozen contract text for the portal view.',
    )


# ---------------------------------------------------------------------------
# 1. Security headers on a representative page
# ---------------------------------------------------------------------------

def test_public_portal_page_carries_the_security_headers(client, portal_invoice):
    """The public invoice page - the actual payment surface - carries the
    full header set, not just the app's logged-in pages."""
    resp = client.get(f'/portal/invoice/{portal_invoice.public_token}')
    assert resp.status_code == 200
    assert resp.headers['X-Frame-Options'] == 'SAMEORIGIN'
    assert resp.headers['X-Content-Type-Options'] == 'nosniff'
    assert resp.headers['Referrer-Policy'] == 'strict-origin-when-cross-origin'
    assert resp.headers.get('Content-Security-Policy')


def test_csp_has_the_directives_added_this_session(client, portal_invoice):
    """frame-ancestors / base-uri / form-action were all absent before."""
    csp = client.get(f'/portal/invoice/{portal_invoice.public_token}').headers['Content-Security-Policy']

    assert "frame-ancestors 'self'" in csp
    assert "base-uri 'self'" in csp
    # form-action MUST still permit PesaPal: the pay button is a real form
    # POST whose response is a 302 to PesaPal's hosted checkout, and
    # Chrome/Safari apply form-action to the redirect chain. A bare
    # form-action 'self' would break payment in those browsers.
    assert 'form-action' in csp
    assert 'https://pay.pesapal.com' in csp.split('form-action')[1]


def test_csp_still_allows_what_the_pages_actually_load(client, portal_invoice):
    """Guards against a future "tighten the CSP" change silently breaking the
    portal in the browser - none of this is visible to a status-code check.
    Every origin asserted here is one templates/portal/base.html really
    loads (Bootstrap 4, Font Awesome, jQuery, popper), plus the data: URIs
    the contract signature images are stored as, plus the inline
    <script>/<style> blocks this codebase uses throughout."""
    csp = client.get(f'/portal/invoice/{portal_invoice.public_token}').headers['Content-Security-Policy']

    script = csp.split('script-src')[1].split(';')[0]
    assert "'unsafe-inline'" in script          # portal/base.html inline <script>
    assert 'https://code.jquery.com' in script
    assert 'https://stackpath.bootstrapcdn.com' in script
    assert 'https://cdn.jsdelivr.net' in script  # popper

    style = csp.split('style-src')[1].split(';')[0]
    assert "'unsafe-inline'" in style           # portal/base.html inline <style>
    assert 'https://fonts.googleapis.com' in style   # print/document.html

    img = csp.split('img-src')[1].split(';')[0]
    assert 'data:' in img                       # signature images
    assert "'self'" in img

    font = csp.split('font-src')[1].split(';')[0]
    assert 'https://fonts.gstatic.com' in font
    assert 'https://cdnjs.cloudflare.com' in font    # Font Awesome webfonts


@pytest.mark.parametrize('path_for', [
    lambda d: f'/portal/quote/{d.public_token}',
    lambda d: f'/portal/quote/{d.public_token}/print',
])
def test_headers_present_on_quote_and_print_views(client, portal_quote, path_for):
    """The /print views render through a different template tree
    (templates/print/) with its own <head> - they must still be covered."""
    resp = client.get(path_for(portal_quote))
    assert resp.status_code == 200
    assert resp.headers.get('Content-Security-Policy')
    assert resp.headers['X-Content-Type-Options'] == 'nosniff'


# ---------------------------------------------------------------------------
# 2. HSTS
# ---------------------------------------------------------------------------

def _with_hsts_enabled(app):
    """HSTS is only switched on when security.configure_security() decides it
    is running in production, which it is not under pytest. Flip the real
    Talisman object's flag so the genuine header-emitting code path runs."""
    return app.extensions['talisman']


def test_hsts_sent_over_https_with_expected_directives(app, client):
    talisman = _with_hsts_enabled(app)
    previous = talisman.strict_transport_security
    talisman.strict_transport_security = True
    try:
        resp = client.get('/login', base_url='https://example.com')
        hsts = resp.headers.get('Strict-Transport-Security')
    finally:
        talisman.strict_transport_security = previous

    assert hsts, 'no HSTS header on an https request'
    assert 'max-age=31556926' in hsts
    assert 'includeSubDomains' in hsts
    # preload is deliberately NOT set: it is effectively irreversible and is
    # the domain owner's decision, not something this app should ship on its
    # own. If someone adds it, this test should stop them and make them say
    # so out loud.
    assert 'preload' not in hsts


def test_hsts_not_sent_over_plain_http(app, client):
    """Local development runs on http://localhost. Sending HSTS there would
    pin the developer's browser to https for localhost - painful to undo and
    affecting every other localhost project on the machine."""
    talisman = _with_hsts_enabled(app)
    previous = talisman.strict_transport_security
    talisman.strict_transport_security = True
    try:
        resp = client.get('/login', base_url='http://localhost')
    finally:
        talisman.strict_transport_security = previous

    assert resp.headers.get('Strict-Transport-Security') is None


# ---------------------------------------------------------------------------
# 3. Rate limiting the public payment surface
# ---------------------------------------------------------------------------

def _first_throttled_request(client, method, url, attempts):
    """Fire `attempts` rapid requests and return the 1-based index of the
    first one answered by the 429 handler, or None if none were. Detects the
    block by the handler's unique flash text (see this module's docstring),
    not by status code."""
    send = client.post if method == 'POST' else client.get
    for i in range(1, attempts + 1):
        resp = send(url, follow_redirects=True)
        if resp.status_code == 429 or RATE_LIMITED_FLASH in resp.get_data(as_text=True):
            return i
    return None


@pytest.mark.parametrize('action', ['accept', 'decline'])
def test_action_route_is_rate_limited(client, csrf_disabled, portal_quote, action):
    """A public, no-login action that changes state must stop after the
    configured burst - previously these were unlimited."""
    url = f'/portal/quote/{portal_quote.public_token}/{action}'
    first = _first_throttled_request(client, 'POST', url, ACTION_LIMIT_PER_MIN + 4)
    assert first == ACTION_LIMIT_PER_MIN + 1, (
        f'expected the first block on request #{ACTION_LIMIT_PER_MIN + 1}, got {first}'
    )


def test_pay_invoice_is_rate_limited(client, csrf_disabled, portal_invoice):
    url = f'/portal/invoice/{portal_invoice.public_token}/pay'
    first = _first_throttled_request(client, 'POST', url, ACTION_LIMIT_PER_MIN + 4)
    assert first == ACTION_LIMIT_PER_MIN + 1


def test_sign_contract_is_rate_limited(client, csrf_disabled, portal_contract):
    url = f'/portal/contract/{portal_contract.public_token}/sign'
    first = _first_throttled_request(client, 'POST', url, ACTION_LIMIT_PER_MIN + 4)
    assert first == ACTION_LIMIT_PER_MIN + 1


def test_view_route_tolerates_ordinary_client_refreshing(client, portal_invoice):
    """The generous tier earns its keep: a client reloading their invoice a
    couple of dozen times (or several people at one office opening the same
    forwarded link from one NAT'd IP) must never be locked out of reading the
    document they are being asked to pay."""
    url = f'/portal/invoice/{portal_invoice.public_token}'
    for _ in range(25):
        assert client.get(url).status_code == 200


def test_view_route_is_still_limited_eventually(client, portal_invoice):
    """...but it is not unlimited - a token-scanning script is still stopped."""
    url = f'/portal/invoice/{portal_invoice.public_token}'
    first = _first_throttled_request(client, 'GET', url, VIEW_LIMIT_PER_MIN + 3)
    assert first == VIEW_LIMIT_PER_MIN + 1


def test_throttled_view_route_renders_a_page_not_a_redirect_loop(client, portal_quote):
    """A throttled VIEW cannot redirect to itself - that would loop while the
    limit is still in force. It must render a real page instead."""
    url = f'/portal/quote/{portal_quote.public_token}'
    for _ in range(VIEW_LIMIT_PER_MIN):
        client.get(url)
    resp = client.get(url)

    assert resp.status_code == 429
    assert resp.location is None, 'a throttled view route must not redirect'
    assert 'Too many requests' in resp.get_data(as_text=True)


def test_payment_callback_is_never_throttled(client, portal_invoice):
    """THE MONEY TEST. PesaPal calls this server-to-server as an IPN, and
    retries come from PesaPal's own small pool of addresses - so every
    confirmation for every tenant arrives under a handful of rate-limit keys.
    Throttling one means an invoice the client HAS PAID is never marked paid
    and the owner is never notified. Far past both configured limits here.

    NOTE - follow_redirects is deliberately OFF, and this asserts on the
    callback's OWN response. The callback answers with a redirect to the
    invoice view, and that view is limited (60/min); following the redirect
    would therefore measure view_invoice's limit rather than this endpoint's
    and report a "throttled callback" that is nothing of the kind. An IPN
    caller does not follow the redirect either, so not following it is also
    the more faithful simulation of what PesaPal's server actually does.
    """
    url = f'/portal/invoice/{portal_invoice.public_token}/payment/callback'
    for i in range(1, VIEW_LIMIT_PER_MIN * 2 + 1):
        resp = client.get(url)
        assert resp.status_code != 429, f'payment callback was throttled on request #{i}'


# ---------------------------------------------------------------------------
# 4. The rate-limit key really is per-client
# ---------------------------------------------------------------------------

def test_rate_limit_key_uses_the_proxy_supplied_address(app):
    """Behind Vercel, request.remote_addr is the proxy - identical for every
    visitor - so keying on it would put the whole world in one bucket. The
    key must come from X-Forwarded-For, and specifically from its RIGHTMOST
    entry: everything to the left is client-supplied and forgeable."""
    from security import client_ip_key

    with app.test_request_context('/', headers={'X-Forwarded-For': '9.9.9.9, 203.0.113.7'}):
        assert client_ip_key() == '203.0.113.7'
    with app.test_request_context('/', headers={'X-Forwarded-For': '203.0.113.7'}):
        assert client_ip_key() == '203.0.113.7'
    # No proxy in front (local dev, and this test suite): fall back to the
    # real peer address rather than to nothing.
    with app.test_request_context('/'):
        assert client_ip_key() == '127.0.0.1'


def test_forged_x_forwarded_for_cannot_buy_a_fresh_bucket(client, portal_quote):
    """An attacker rotating the forgeable LEFTMOST X-Forwarded-For entry must
    not escape the limit. If this ever fails, the limit is decorative: a
    scanner just adds a header and walks straight through it."""
    url = f'/portal/quote/{portal_quote.public_token}'
    blocked_at = None
    for i in range(1, VIEW_LIMIT_PER_MIN + 3):
        resp = client.get(url, headers={'X-Forwarded-For': f'9.9.9.{i}, 203.0.113.7'})
        if resp.status_code == 429:
            blocked_at = i
            break

    assert blocked_at == VIEW_LIMIT_PER_MIN + 1


def test_two_different_clients_do_not_share_a_bucket(client, portal_quote):
    """The other half of the same property: one noisy client must not lock
    out an unrelated one. This is the failure mode that would have been worse
    than having no rate limit at all."""
    url = f'/portal/quote/{portal_quote.public_token}'
    for _ in range(VIEW_LIMIT_PER_MIN + 1):
        client.get(url, headers={'X-Forwarded-For': '203.0.113.1'})

    assert client.get(url, headers={'X-Forwarded-For': '203.0.113.1'}).status_code == 429
    assert client.get(url, headers={'X-Forwarded-For': '203.0.113.2'}).status_code == 200
