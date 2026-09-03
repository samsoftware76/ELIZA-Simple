"""
Unit-level tests for filters.py and small utils modules - no HTTP, no routes.

Covers three bug classes fixed this session:
  * nl2br() stored-XSS: content used to go straight to the page as raw HTML.
  * status_badge()/billing_status_badge() enum-vs-.name mismatch that made
    every project/task badge render grey regardless of real status.
  * exchange_rates.get_rate() must never raise, even when the network call
    blows up - a display nicety must never 500 a money page.

Also covers utils/plan_limits.py's can_add_project/can_add_client at the
three interesting points: unlimited (0), under-limit, and at-limit.
"""
import pytest

from filters import nl2br, status_badge, billing_status_badge
from models.models import ProjectStatus, TaskStatus
from models.billing import QuoteStatus, InvoiceStatus, ContractStatus
from utils.plan_limits import can_add_project, can_add_client


# ---------------------------------------------------------------------------
# nl2br() - stored XSS fix
# ---------------------------------------------------------------------------

def test_nl2br_escapes_script_tag():
    """A <script> payload must come back HTML-escaped, never as a raw tag -
    this is the actual stored-XSS fix, not just a newline-to-<br> nicety."""
    payload = "<script>alert('xss')</script>"
    result = str(nl2br(payload))
    assert "<script>" not in result
    assert "&lt;script&gt;" in result


def test_nl2br_converts_newlines_to_br_after_escaping():
    result = str(nl2br("line one\nline two"))
    assert "<br>" in result
    assert "line one" in result and "line two" in result
    # The real newline character itself should be gone, replaced by markup.
    assert "\n" not in result


def test_nl2br_empty_value_returns_empty_string():
    assert nl2br(None) == ""
    assert nl2br("") == ""


def test_nl2br_result_is_safe_markup_not_double_escaped():
    """Escaping must happen exactly once - the result is real Markup, so a
    template rendering it with |safe must not see double-escaped entities."""
    from markupsafe import Markup
    result = nl2br("a & b\n<b>bold</b>")
    assert isinstance(result, Markup)
    assert "&amp;" in result  # the literal & is escaped once
    assert "&amp;amp;" not in result  # never escaped twice
    assert "&lt;b&gt;" in result
    assert "<b>bold</b>" not in result


# ---------------------------------------------------------------------------
# Email templates - task_assignment.html / task_comment.html escaping fix
#
# Both used to render their user-controlled field via
# |replace('\n', '<br>')|safe, which (unlike nl2br above) never escapes the
# value first - a <script>/onerror/phishing-link payload rendered live,
# unescaped, in the recipient's email client. Fixed by switching both to the
# already-tested nl2br filter.
# ---------------------------------------------------------------------------

def test_task_assignment_email_escapes_html_in_description(app, owner_user, make_client, make_project, make_task, monkeypatch):
    import utils.email_utils as email_utils

    a_client = make_client(owner_user)
    project = make_project(a_client)
    task = make_task(project, description="<img src=x onerror=alert(document.cookie)>")

    captured = {}
    monkeypatch.setattr(email_utils.mail, 'send', lambda msg: captured.__setitem__('html', msg.html))

    # A real request context, matching how this is always actually called
    # (from inside a route handler) - base.html's inject_sidebar_plan()
    # context processor reads current_user, which is only ever a real
    # AnonymousUserMixin/User (not None) once a request context exists.
    with app.test_request_context():
        email_utils.send_task_assignment_notification(task, owner_user)

    assert 'html' in captured, "the notification must actually render and attempt to send"
    assert '<img src=x onerror=' not in captured['html']
    assert '&lt;img src=x onerror=' in captured['html']


def test_task_comment_email_escapes_html_in_content(app, owner_user, make_client, make_project, make_task, make_user, db_session, monkeypatch):
    import utils.email_utils as email_utils
    from models.models import Comment, UserRole

    a_client = make_client(owner_user)
    project = make_project(a_client)
    assignee = make_user(role=UserRole.DEVELOPER)
    task = make_task(project, assignee_id=assignee.id)
    comment = Comment(task_id=task.id, user_id=owner_user.id,
                       content='<a href="https://evil.example">click here</a>', is_internal=False)
    db_session.add(comment)
    db_session.commit()

    captured = {}
    monkeypatch.setattr(email_utils.mail, 'send', lambda msg: captured.__setitem__('html', msg.html))

    with app.test_request_context():
        email_utils.send_task_comment_notification(task, comment, owner_user)

    assert 'html' in captured, "the assignee must be a recipient, so the notification must actually attempt to send"
    assert '<a href="https://evil.example">' not in captured['html']
    assert '&lt;a href=' in captured['html']


# ---------------------------------------------------------------------------
# status_badge() - enum-vs-.name call-site mismatch fix
# ---------------------------------------------------------------------------

EXPECTED_PROJECT_BADGES = {
    ProjectStatus.PENDING: 'secondary',
    ProjectStatus.IN_PROGRESS: 'primary',
    ProjectStatus.COMPLETED: 'success',
    ProjectStatus.ON_HOLD: 'warning',
    ProjectStatus.CANCELLED: 'danger',
}

EXPECTED_TASK_BADGES = {
    TaskStatus.TODO: 'secondary',
    TaskStatus.IN_PROGRESS: 'primary',
    TaskStatus.REVIEW: 'info',
    TaskStatus.COMPLETED: 'success',
}


@pytest.mark.parametrize("member,expected", list(EXPECTED_PROJECT_BADGES.items()))
def test_status_badge_every_project_status_enum_member(member, expected):
    """Real call sites pass the enum member itself (project.status|status_badge)."""
    assert status_badge(member) == expected


@pytest.mark.parametrize("member,expected", list(EXPECTED_PROJECT_BADGES.items()))
def test_status_badge_every_project_status_dot_name(member, expected):
    """Some call sites pass .name (a plain string) - both shapes must resolve
    to the same class, or the badge silently falls back to grey 'secondary'."""
    assert status_badge(member.name) == expected


@pytest.mark.parametrize("member,expected", list(EXPECTED_TASK_BADGES.items()))
def test_status_badge_every_task_status_enum_member(member, expected):
    assert status_badge(member) == expected


@pytest.mark.parametrize("member,expected", list(EXPECTED_TASK_BADGES.items()))
def test_status_badge_every_task_status_dot_name(member, expected):
    assert status_badge(member.name) == expected


def test_status_badge_unknown_value_falls_back_to_secondary():
    assert status_badge('SOMETHING_UNKNOWN') == 'secondary'
    assert status_badge(None) == 'secondary'


# ---------------------------------------------------------------------------
# billing_status_badge() - every real Quote/Invoice/Contract status string
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status,expected", [
    (QuoteStatus.DRAFT, 'secondary'),
    (QuoteStatus.SENT, 'primary'),
    (QuoteStatus.ACCEPTED, 'success'),
    (QuoteStatus.DECLINED, 'danger'),
])
def test_billing_status_badge_quote_statuses(status, expected):
    assert billing_status_badge(status) == expected


@pytest.mark.parametrize("status,expected", [
    (InvoiceStatus.DRAFT, 'secondary'),
    (InvoiceStatus.SENT, 'primary'),
    (InvoiceStatus.PAID, 'success'),
    (InvoiceStatus.CANCELLED, 'danger'),
])
def test_billing_status_badge_invoice_statuses(status, expected):
    assert billing_status_badge(status) == expected


@pytest.mark.parametrize("status,expected", [
    (ContractStatus.DRAFT, 'secondary'),
    (ContractStatus.SENT, 'primary'),
    (ContractStatus.SIGNED, 'success'),
    (ContractStatus.DECLINED, 'danger'),
    (ContractStatus.VOIDED, 'dark'),
])
def test_billing_status_badge_contract_statuses(status, expected):
    assert billing_status_badge(status) == expected


def test_billing_status_badge_unknown_falls_back_to_secondary():
    assert billing_status_badge('nonexistent') == 'secondary'


# ---------------------------------------------------------------------------
# utils/exchange_rates.get_rate() - never raises
# ---------------------------------------------------------------------------

def test_get_rate_returns_none_when_network_call_raises(monkeypatch):
    """A network failure (timeout, DNS error, connection refused, ...) must
    come back as None, never propagate as an exception - approx_display()
    treats None as 'show no conversion', and a raised exception there would
    500 a money-bearing page for a purely cosmetic feature."""
    import utils.exchange_rates as exchange_rates

    def _boom(*args, **kwargs):
        raise ConnectionError("simulated network failure")

    monkeypatch.setattr(exchange_rates.requests, 'get', _boom)
    # A currency pair unlikely to already be cached from another test in this
    # session - get_rate's cache is a module-level dict keyed by base currency.
    assert exchange_rates.get_rate('ZWL', 'KES') is None


def test_get_rate_returns_none_on_bad_input():
    from utils.exchange_rates import get_rate
    assert get_rate(None, 'USD') is None
    assert get_rate('USD', None) is None
    assert get_rate('', '') is None


def test_get_rate_same_currency_short_circuits_to_one():
    """Same from/to currency needs no network call at all."""
    from utils.exchange_rates import get_rate
    assert get_rate('USD', 'usd') == 1.0


# ---------------------------------------------------------------------------
# utils/plan_limits.can_add_project / can_add_client
# ---------------------------------------------------------------------------

def test_can_add_project_unlimited_plan_always_allowed(owner_user, make_subscription, make_subscription_plan, make_client, make_project):
    plan = make_subscription_plan(max_projects=0)
    make_subscription(owner_user, plan=plan)
    client = make_client(owner_user)
    for _ in range(3):
        make_project(client)
    allowed, limit, current = can_add_project(owner_user.get_owner_id())
    assert allowed is True
    assert limit == 0
    assert current == 3


def test_can_add_project_under_limit_allowed(owner_user, make_subscription, make_subscription_plan, make_client, make_project):
    plan = make_subscription_plan(max_projects=5)
    make_subscription(owner_user, plan=plan)
    client = make_client(owner_user)
    make_project(client)
    make_project(client)
    allowed, limit, current = can_add_project(owner_user.get_owner_id())
    assert allowed is True
    assert limit == 5
    assert current == 2


def test_can_add_project_at_limit_blocked(owner_user, make_subscription, make_subscription_plan, make_client, make_project):
    plan = make_subscription_plan(max_projects=2)
    make_subscription(owner_user, plan=plan)
    client = make_client(owner_user)
    make_project(client)
    make_project(client)
    allowed, limit, current = can_add_project(owner_user.get_owner_id())
    assert allowed is False
    assert limit == 2
    assert current == 2


def test_can_add_client_unlimited_plan_always_allowed(owner_user, make_subscription, make_subscription_plan, make_client):
    plan = make_subscription_plan(max_clients=0)
    make_subscription(owner_user, plan=plan)
    for _ in range(4):
        make_client(owner_user)
    allowed, limit, current = can_add_client(owner_user.get_owner_id())
    assert allowed is True
    assert limit == 0
    assert current == 4


def test_can_add_client_under_limit_allowed(owner_user, make_subscription, make_subscription_plan, make_client):
    plan = make_subscription_plan(max_clients=3)
    make_subscription(owner_user, plan=plan)
    make_client(owner_user)
    allowed, limit, current = can_add_client(owner_user.get_owner_id())
    assert allowed is True
    assert limit == 3
    assert current == 1


def test_can_add_client_at_limit_blocked(owner_user, make_subscription, make_subscription_plan, make_client):
    plan = make_subscription_plan(max_clients=1)
    make_subscription(owner_user, plan=plan)
    make_client(owner_user)
    allowed, limit, current = can_add_client(owner_user.get_owner_id())
    assert allowed is False
    assert limit == 1
    assert current == 1


def test_can_add_project_no_subscription_at_all_is_unlimited(owner_user, make_client, make_project):
    """No active subscription at all (a brand-new account) must never block
    - same convention as an unlimited (0) plan."""
    client = make_client(owner_user)
    make_project(client)
    allowed, limit, current = can_add_project(owner_user.get_owner_id())
    assert allowed is True
    assert limit == 0
