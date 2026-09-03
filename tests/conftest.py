"""
Shared pytest fixtures for the ELIZA test suite.

============================================================================
DATABASE ISOLATION - READ THIS BEFORE ADDING ANYTHING ABOVE THE FIRST BLOCK
============================================================================
This app (api/index.py) does, at MODULE IMPORT TIME:

    DATABASE_URL = os.getenv('DATABASE_URL')
    ...
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL   # falls back to a
                                                             # local sqlite
                                                             # file only if
                                                             # DATABASE_URL
                                                             # is unset

If a developer's shell still has the real Neon DATABASE_URL exported (or a
committed-by-accident .env sitting around) the moment `import api.index`
first runs in this process, the test session would bind to the live
production database. That must be structurally impossible, not something we
just ask developers to remember - hence everything below.

WHY THIS OVERRIDE LIVES IN MODULE-LEVEL CODE, NOT A pytest_configure() HOOK
OR AN AUTOUSE FIXTURE:

  - An autouse fixture only runs once pytest starts *executing* tests. But
    pytest COLLECTS (imports) every test module in tests/ before it runs any
    fixture at all. If a test file ever did `from api.index import app` at
    its own module level, that import - and every real DATABASE_URL read
    inside it - would happen during collection, before any fixture (autouse
    or not) has had a chance to run. A fixture-based override can arrive too
    late.
  - pytest_configure() does run before collection, so it would also be safe
    here - but plain top-level code in the rootdir's conftest.py runs even
    earlier (conftest.py files are imported as pytest's very first step in
    discovering a test session, before hook registration/dispatch begins),
    and it has no hook-registration machinery that could be misconfigured or
    silently skipped. Simpler mechanism, same guarantee, fewer ways to get it
    wrong - so that's what this file uses.

WHY A REAL TEMP FILE, NOT ':memory:':
  Flask-SQLAlchemy's connection pool (see utils/db_utils.configure_db_pool -
  QueuePool, pool_size=10) hands different requests different connections.
  An in-memory SQLite database is private to the single connection that
  created it, so a second pooled connection would see a completely empty
  (or separately-created) database - tables created in one request going
  invisible in the next. A tmp file on disk is one physical database that
  every pooled connection opens, so it behaves like the real Postgres file
  the app actually deploys against. The temp directory is created once per
  test SESSION (not via pytest's function-scoped tmp_path fixture, which
  does not exist yet at this point in startup - see above) and is removed in
  pytest_sessionfinish() at the very end.

WHY load_dotenv() elsewhere in the app can't undo this:
  Every load_dotenv() call in this codebase (config.py, api/index.py,
  api/payment.py, api/sms.py, ...) is called with its default
  override=False, which NEVER replaces a key that already exists in
  os.environ. Because the block below runs before api.index (or anything it
  imports) is ever imported, our sqlite DATABASE_URL is already sitting in
  os.environ by the time any of those load_dotenv() calls execute, so it
  always wins. (Verified by reading every load_dotenv(...) call site in this
  repo - none pass override=True.)
============================================================================
"""
import os
import pathlib
import tempfile

_TEST_DB_DIR = tempfile.mkdtemp(prefix="eliza_test_db_")
_TEST_DB_PATH = os.path.join(_TEST_DB_DIR, "eliza_test.db")
# Forward slashes + no leading slash before the drive letter is the form
# SQLAlchemy's sqlite dialect expects for an absolute Windows path
# (sqlite:///C:/foo/bar.db) - a plain os.path string with backslashes here
# would build a URL sqlite silently fails to parse as "this exact file".
os.environ["DATABASE_URL"] = "sqlite:///" + pathlib.Path(_TEST_DB_PATH).as_posix()

# ---------------------------------------------------------------------------
# Everything below this line is a normal import - the override above has
# already landed in os.environ before any of it runs.
# ---------------------------------------------------------------------------
import shutil  # noqa: E402
import re  # noqa: E402
from datetime import date  # noqa: E402

import pytest  # noqa: E402
from faker import Faker  # noqa: E402

from models import db  # noqa: E402
from models.models import (  # noqa: E402
    User, Client, Project, Task, TeamMembership, UserRole,
    ProjectStatus, TaskStatus,
)
from models.subscription import Subscription, SubscriptionPlan  # noqa: E402
from models.billing import Quote, QuoteItem, Invoice, InvoiceItem, Contract  # noqa: E402

fake = Faker()

# Plaintext password used by every factory-created User unless a test passes
# its own - satisfies every form's Length(min=8) password validator. Kept in
# one place so login_as()'s default lookup (user._test_password, set by
# make_user below) and any test asserting login behavior agree on one value.
DEFAULT_TEST_PASSWORD = "Test-Passw0rd!"


def pytest_sessionfinish(session, exitstatus):
    """Delete the whole per-session tmp sqlite directory (db file, -wal/-shm
    journal files, everything) once every test has finished, pass or fail."""
    shutil.rmtree(_TEST_DB_DIR, ignore_errors=True)


# ---------------------------------------------------------------------------
# Core fixtures: app / db_session / client
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def app():
    """The real Flask app, imported AFTER the DATABASE_URL override above.

    Session-scoped: importing api.index has real side effects (registers
    every blueprint, wires up Talisman/CSRF/rate-limiting) that only need to
    happen once. Table creation likewise only needs to happen once here -
    per-test isolation is db_session's job below, not this fixture's.
    """
    import api.index as index_module  # deferred: must not happen before the override above

    flask_app = index_module.app
    flask_app.config["TESTING"] = True
    # Explicit, not just "left at the Flask-WTF default of True" - states the
    # intent in one place a reader can find. A test that specifically needs
    # to skip CSRF (e.g. hitting a route with a hand-built POST body) should
    # request the csrf_disabled fixture below rather than editing this.
    flask_app.config["WTF_CSRF_ENABLED"] = True
    flask_app.config["MAIL_SUPPRESS_SEND"] = True

    # --- Safety trip-wire -------------------------------------------------
    # Fails LOUDLY (not a log line) if, despite everything above, this
    # process is somehow about to run tests against a non-sqlite database.
    # This is the last line of defense the task's hard rule demands: even if
    # every mechanism above were bypassed, this stops the session before a
    # single test touches the database.
    db_uri = flask_app.config["SQLALCHEMY_DATABASE_URI"]
    assert db_uri.startswith("sqlite"), (
        "REFUSING TO RUN TESTS: SQLALCHEMY_DATABASE_URI is "
        f"{db_uri!r}, not a sqlite database. This test suite must NEVER run "
        "against a non-sqlite database (see the DATABASE ISOLATION comment "
        "at the top of tests/conftest.py)."
    )
    print(f"[conftest] SQLALCHEMY_DATABASE_URI = {db_uri}")

    with flask_app.app_context():
        db.create_all()

    yield flask_app

    with flask_app.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.fixture
def db_session(app):
    """Function-scoped, leak-proof test isolation.

    Chosen over a SAVEPOINT/nested-transaction rollback specifically because
    this app's routes call db.session.commit() directly and repeatedly
    within a single request (see api/index.py - client_create,
    accept_invite, etc. each commit more than once), rather than the
    request-scoped "one commit at the very end" shape that the classic
    begin_nested()-and-roll-back-the-outer-transaction recipe assumes. Making
    that recipe safe here would mean intercepting every commit() call with a
    SQLAlchemy event listener that restarts the SAVEPOINT each time - solving
    a subtle, easy-to-get-wrong problem to save a bit of time on a local
    SQLite file.

    Dropping and recreating every table before each test is simple, and
    leak-proof by construction: it does not matter what the previous test
    committed, because the tables it wrote into no longer exist afterwards.
    Verified live with a throwaway two-test sanity check (written, run,
    watched pass, then deleted) confirming a row committed in one test is
    genuinely absent in the next - see the "Running the tests" section of
    README.md for how to rerun that kind of check if this fixture is ever
    changed.
    """
    with app.app_context():
        db.session.remove()
        db.drop_all()
        db.create_all()
        yield db.session
        db.session.remove()


@pytest.fixture
def client(app, db_session):
    """Flask test client. Depends on db_session (even though it never uses
    it directly) so that simply requesting `client` in a test is enough to
    get a freshly-reset database - a test never has to remember to also ask
    for db_session itself."""
    return app.test_client()


@pytest.fixture(autouse=True)
def _reset_rate_limits(app):
    """Reset Flask-Limiter's in-memory counters before every test.

    security.limiter (used by /login and /register - see api/index.py) uses
    Flask-Limiter's default in-memory storage, which is a single counter
    dict shared for the lifetime of the process. Because the `app` fixture
    above is session-scoped (deliberately - see its own docstring), that one
    limiter instance is shared by every test in the whole session, and the
    Werkzeug test client always presents as the same remote address. Without
    this, the login limit (5 per minute) would trip partway through the
    session - not because any single test hammered login, but because many
    DIFFERENT tests each called login_as() a few times - and every test
    after the 5th login attempt across the WHOLE SUITE would get a 429 that
    has nothing to do with what that test is actually checking.
    Resetting the storage's counters before each test (not the configured
    limit itself, which stays exactly 5/minute as shipped) keeps one test's
    login attempts from ever affecting another's.
    """
    from security import limiter
    limiter.storage.reset()
    yield


@pytest.fixture
def csrf_disabled(app):
    """Opt-in per-test override for the one case WTF_CSRF_ENABLED=True (set
    on the `app` fixture above) gets in the way: a test that deliberately
    wants to POST without a token. Restores whatever value was set before,
    so the override can never leak into a later test."""
    previous = app.config["WTF_CSRF_ENABLED"]
    app.config["WTF_CSRF_ENABLED"] = False
    yield
    app.config["WTF_CSRF_ENABLED"] = previous


# ---------------------------------------------------------------------------
# Auth helpers - plain functions, not fixtures (import them explicitly:
# `from conftest import login_as, get_csrf_token` works from any module
# inside tests/, since pytest puts conftest.py's directory on sys.path).
# ---------------------------------------------------------------------------

def get_csrf_token(client, url):
    """GET url and scrape the real csrf_token value out of the rendered
    HTML - the same hidden input form.hidden_tag() renders on every real
    form in this app. Matches the input tag regardless of attribute order
    (Flask-WTF's own rendering order is not a contract this should depend
    on), then pulls value="..." out of that tag specifically."""
    response = client.get(url)
    html = response.get_data(as_text=True)
    tag_match = re.search(r'<input[^>]*name=["\']csrf_token["\'][^>]*>', html)
    assert tag_match, (
        f"No csrf_token hidden field found on GET {url} (status "
        f"{response.status_code}) - is that really a page with a form on it?"
    )
    value_match = re.search(r'value=["\']([^"\']+)["\']', tag_match.group(0))
    assert value_match, f"csrf_token field on {url} has no value attribute: {tag_match.group(0)!r}"
    return value_match.group(1)


def login_as(client, user, password=None):
    """Log `user` into `client` via a REAL POST to /login (the actual auth
    path - not a session shortcut), including a real CSRF token scraped from
    the login page first. Returns the (redirected-through) response so a
    caller can assert on it.

    password defaults to the plaintext password make_user() stashed on the
    user object as `_test_password` (a plain Python attribute, not a mapped
    column) at creation time; pass password= explicitly for a user created
    any other way.
    """
    plain_password = password if password is not None else getattr(user, "_test_password", None)
    assert plain_password is not None, (
        "login_as() has no password for this user - pass password=... "
        "explicitly, or create the user via the make_user fixture, which "
        "stashes the plaintext password it set for exactly this purpose."
    )
    token = get_csrf_token(client, "/login")
    return client.post(
        "/login",
        data={
            "email": user.email,
            "password": plain_password,
            "csrf_token": token,
            "submit": "Log in",
        },
        follow_redirects=True,
    )


# ---------------------------------------------------------------------------
# Factory fixtures. Each is a FACTORY (a callable returned by the fixture),
# never a hardcoded singleton instance - call it as many times as a test
# needs distinct rows. All uniqueness-constrained fields use fake.unique.*,
# so two calls in the same test session never collide.
# ---------------------------------------------------------------------------

@pytest.fixture
def make_user(db_session):
    """Factory for a User row. Role defaults to DEVELOPER (an ordinary staff
    account); pass role=UserRole.CLIENT / .ADMIN / etc. to override. The
    plaintext password is stashed on the returned object as _test_password
    (see login_as above) - never stored anywhere in the database itself."""
    def _make_user(*, role=UserRole.DEVELOPER, password=DEFAULT_TEST_PASSWORD, **overrides):
        defaults = dict(
            username=fake.unique.user_name(),
            email=fake.unique.email(),
            first_name=fake.first_name(),
            last_name=fake.last_name(),
            phone=fake.numerify("+2567########"),
            role=role,
        )
        defaults.update(overrides)
        user = User(**defaults)
        user.set_password(password)
        db_session.add(user)
        db_session.commit()
        user._test_password = password
        return user
    return _make_user


@pytest.fixture
def owner_user(make_user):
    """A single ready-to-use account-owning User (DEVELOPER role, no
    TeamMembership pointing at it) - the common case where a test just needs
    "some owner" and doesn't care which. For a second, independent tenant
    (cross-tenant isolation tests), call make_user() again, or use
    other_owner_user below."""
    return make_user()


@pytest.fixture
def other_owner_user(make_user):
    """A second, fully independent owner User - for cross-tenant tests that
    need to prove owner_user's data is invisible to this account and vice
    versa. Deliberately a distinct fixture (not just "call make_user twice")
    so a test signature makes the two-tenant setup obvious at a glance:
    `def test_x(owner_user, other_owner_user, ...)`."""
    return make_user()


@pytest.fixture
def make_team_member(make_user, db_session):
    """Factory for a team-member User under a given owner: creates the User
    row (role defaults to DEVELOPER - the access level) plus the
    TeamMembership row that makes owner.get_owner_id() resolve for them.
    role_title is the free-text display label (see
    models.models.TeamMembership.role_title) and never affects access."""
    def _make_team_member(owner, *, role=UserRole.DEVELOPER, role_title=None, **overrides):
        member = make_user(role=role, **overrides)
        membership = TeamMembership(
            account_owner_id=owner.id,
            member_user_id=member.id,
            role=role,
            role_title=role_title,
            is_active=True,
        )
        db_session.add(membership)
        db_session.commit()
        return member
    return _make_team_member


@pytest.fixture
def make_client(db_session):
    """Factory for a Client row (a tenant's own customer) owned by `owner`."""
    def _make_client(owner, **overrides):
        defaults = dict(
            name=fake.unique.company(),
            contact_person=fake.name(),
            email=fake.unique.email(),
            phone=fake.numerify("+2567########"),
            address=fake.address(),
            owner_id=owner.id,
        )
        defaults.update(overrides)
        client_row = Client(**defaults)
        db_session.add(client_row)
        db_session.commit()
        return client_row
    return _make_client


@pytest.fixture
def make_client_login(make_user, db_session):
    """Factory for a CLIENT-role User linked to an existing Client record
    (Client.user_id) - the client's own portal login, distinct from
    client_row.owner_id (the staff tenant that manages them)."""
    def _make_client_login(client_row, *, password=DEFAULT_TEST_PASSWORD, **overrides):
        portal_user = make_user(role=UserRole.CLIENT, password=password, **overrides)
        client_row.user_id = portal_user.id
        db_session.commit()
        return portal_user
    return _make_client_login


@pytest.fixture
def make_project(db_session):
    """Factory for a Project. owner defaults to the client's own owner_id
    (the normal case - a project belongs to whoever owns the client), pass
    owner= explicitly only to deliberately construct a mismatched/cross-
    tenant scenario."""
    def _make_project(client_row, *, owner=None, **overrides):
        defaults = dict(
            title=fake.unique.catch_phrase(),
            description=fake.text(max_nb_chars=200),
            client_id=client_row.id,
            start_date=date.today(),
            status=ProjectStatus.PENDING,
            owner_id=(owner.id if owner is not None else client_row.owner_id),
        )
        defaults.update(overrides)
        project = Project(**defaults)
        db_session.add(project)
        db_session.commit()
        return project
    return _make_project


@pytest.fixture
def make_task(db_session):
    """Factory for a Task under an existing Project."""
    def _make_task(project, **overrides):
        defaults = dict(
            title=fake.sentence(nb_words=4).rstrip("."),
            description=fake.text(max_nb_chars=200),
            project_id=project.id,
            status=TaskStatus.TODO,
        )
        defaults.update(overrides)
        task = Task(**defaults)
        db_session.add(task)
        db_session.commit()
        return task
    return _make_task


@pytest.fixture
def make_quote(db_session):
    """Factory for a Quote with line items. Mirrors the real
    'PENDING' -> flush -> f"Q-{id:05d}" numbering api/billing.py uses, so a
    quote_number here looks exactly like one the app itself would generate.
    items defaults to a single realistic line item; pass items=[{...}, ...]
    (each a QuoteItem kwargs dict) to control them explicitly."""
    def _make_quote(client_row, created_by, *, items=None, **overrides):
        defaults = dict(
            quote_number="PENDING",
            client_id=client_row.id,
            created_by_id=created_by.id,
            title=fake.catch_phrase(),
            currency="USD",
        )
        defaults.update(overrides)
        quote = Quote(**defaults)
        db_session.add(quote)
        db_session.flush()  # assigns quote.id without a separate round trip
        quote.quote_number = f"Q-{quote.id:05d}"
        for item_kwargs in (items or [{"description": fake.bs().capitalize(), "quantity": 1, "unit_price": 100.0}]):
            db_session.add(QuoteItem(quote_id=quote.id, **item_kwargs))
        db_session.commit()
        return quote
    return _make_quote


@pytest.fixture
def make_invoice(db_session):
    """Factory for an Invoice with line items - same numbering/items
    contract as make_quote above (INV-00001 style, via flush -> id)."""
    def _make_invoice(client_row, created_by, *, items=None, **overrides):
        defaults = dict(
            invoice_number="PENDING",
            client_id=client_row.id,
            created_by_id=created_by.id,
            title=fake.catch_phrase(),
            currency="USD",
        )
        defaults.update(overrides)
        invoice = Invoice(**defaults)
        db_session.add(invoice)
        db_session.flush()  # assigns invoice.id without a separate round trip
        invoice.invoice_number = f"INV-{invoice.id:05d}"
        for item_kwargs in (items or [{"description": fake.bs().capitalize(), "quantity": 1, "unit_price": 100.0}]):
            db_session.add(InvoiceItem(invoice_id=invoice.id, **item_kwargs))
        db_session.commit()
        return invoice
    return _make_invoice


@pytest.fixture
def make_contract(db_session):
    """Factory for a Contract - same 'PENDING' -> flush -> CT-{id:05d}
    numbering convention as api/contracts.py."""
    def _make_contract(client_row, created_by, **overrides):
        defaults = dict(
            contract_number="PENDING",
            client_id=client_row.id,
            created_by_id=created_by.id,
            title=fake.catch_phrase(),
            body=fake.text(max_nb_chars=1000),
        )
        defaults.update(overrides)
        contract = Contract(**defaults)
        db_session.add(contract)
        db_session.flush()  # assigns contract.id without a separate round trip
        contract.contract_number = f"CT-{contract.id:05d}"
        db_session.commit()
        return contract
    return _make_contract


@pytest.fixture
def make_subscription_plan(db_session):
    """Factory for a SubscriptionPlan. Limits default to 0 (unlimited), the
    same convention utils/plan_limits.py reads."""
    def _make_subscription_plan(**overrides):
        defaults = dict(
            name=fake.unique.bs().title() + " Plan",
            description=fake.sentence(),
            price_monthly=19.0,
            price_yearly=190.0,
            max_projects=0,
            max_users=0,
            max_clients=0,
            is_active=True,
        )
        defaults.update(overrides)
        plan = SubscriptionPlan(**defaults)
        db_session.add(plan)
        db_session.commit()
        return plan
    return _make_subscription_plan


@pytest.fixture
def make_subscription(db_session, make_subscription_plan):
    """Factory for an ACTIVE Subscription tied to `user`. Creates its own
    SubscriptionPlan via make_subscription_plan() unless plan= is passed in
    (e.g. to share one plan across several subscriptions in the same test)."""
    def _make_subscription(user, *, plan=None, **overrides):
        plan = plan or make_subscription_plan()
        defaults = dict(
            user_id=user.id,
            plan_id=plan.id,
            is_active=True,
            is_trial=False,
            billing_cycle="monthly",
        )
        defaults.update(overrides)
        subscription = Subscription(**defaults)
        db_session.add(subscription)
        db_session.commit()
        return subscription
    return _make_subscription
