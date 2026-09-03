"""
Seed known invoice/time-entry amounts across two currencies and check the
dashboard KPI, wallet totals and deliverables report all agree with a
hand-computed expected value - and, critically, that amounts are NEVER
summed across currencies (home()'s outstanding KPI, wallet()'s collected/
outstanding buckets, and build_deliverables_report()'s total_hours are all
grouped/summed in Python, never via a cross-currency SQL SUM).
"""
from datetime import datetime

from conftest import login_as
from models.billing import InvoiceStatus


# ---------------------------------------------------------------------------
# Dashboard KPI + wallet: outstanding invoices across two currencies
# ---------------------------------------------------------------------------

def test_dashboard_and_wallet_outstanding_never_summed_across_currencies(
        client, owner_user, make_client, make_invoice):
    a_client = make_client(owner_user)
    # USD 100.00 outstanding (2 * 50.00, no tax)
    make_invoice(a_client, owner_user, currency='USD', tax_rate=0,
                 items=[{'description': 'USD work', 'quantity': 2, 'unit_price': 50.0}],
                 status=InvoiceStatus.SENT)
    # EUR 50.00 outstanding (1 * 50.00, no tax) - deliberately smaller, so the
    # dashboard's "dominant currency" pick is unambiguous.
    make_invoice(a_client, owner_user, currency='EUR', tax_rate=0,
                 items=[{'description': 'EUR work', 'quantity': 1, 'unit_price': 50.0}],
                 status=InvoiceStatus.SENT)

    login_as(client, owner_user)

    # --- Dashboard KPI ---
    home_html = client.get('/').get_data(as_text=True)
    # Dominant currency (larger amount) is USD 100.00 - never "150.00" (the
    # naively-summed, currency-blind total).
    assert 'USD 100.00' in home_html
    assert '150.00' not in home_html
    # extra_currencies = len(outstanding_by_currency) - 1 = 1 more currency.
    assert '+1 more currency' in home_html

    # --- Wallet ---
    wallet_html = client.get('/wallet').get_data(as_text=True)
    assert 'USD 100.00' in wallet_html
    assert 'EUR 50.00' in wallet_html
    assert '150.00' not in wallet_html


def test_wallet_collected_never_summed_across_currencies(client, owner_user, make_client, make_invoice):
    a_client = make_client(owner_user)
    now = datetime.utcnow()
    make_invoice(a_client, owner_user, currency='USD', tax_rate=0,
                 items=[{'description': 'Paid in USD', 'quantity': 1, 'unit_price': 200.0}],
                 status=InvoiceStatus.PAID, paid_at=now)
    make_invoice(a_client, owner_user, currency='EUR', tax_rate=0,
                 items=[{'description': 'Paid in EUR', 'quantity': 1, 'unit_price': 30.0}],
                 status=InvoiceStatus.PAID, paid_at=now)

    login_as(client, owner_user)
    wallet_html = client.get('/wallet').get_data(as_text=True)
    assert 'USD 200.00' in wallet_html
    assert 'EUR 30.00' in wallet_html
    assert '230.00' not in wallet_html


def test_dashboard_outstanding_with_tax_matches_hand_computed_total(client, owner_user, make_client, make_invoice):
    a_client = make_client(owner_user)
    # subtotal = 3 * 40 = 120; tax = 120 * 15% = 18; total = 138.00
    make_invoice(a_client, owner_user, currency='USD', tax_rate=15,
                 items=[{'description': 'Taxed work', 'quantity': 3, 'unit_price': 40.0}],
                 status=InvoiceStatus.SENT)

    login_as(client, owner_user)
    home_html = client.get('/').get_data(as_text=True)
    assert 'USD 138.00' in home_html


# ---------------------------------------------------------------------------
# Deliverables report: hours logged via TimeEntry, hand-computed
# ---------------------------------------------------------------------------

def test_deliverables_report_total_hours_matches_hand_computed_sum(
        client, owner_user, make_client, make_project, make_task, db_session):
    from models.models import TimeEntry

    a_client = make_client(owner_user)
    project = make_project(a_client)
    task_a = make_task(project)
    task_b = make_task(project)

    # Known durations, deliberately already clean to 1 decimal place -
    # build_project_report() does round(total_hours, 1).
    entries = [
        (task_a, 2.0),
        (task_a, 0.5),
        (task_b, 1.0),
    ]
    for task, duration in entries:
        db_session.add(TimeEntry(
            task_id=task.id, user_id=owner_user.id,
            start_time=datetime.utcnow(), end_time=datetime.utcnow(),
            duration=duration,
        ))
    db_session.commit()
    expected_total_hours = sum(d for _, d in entries)  # 3.5
    assert expected_total_hours == 3.5

    login_as(client, owner_user)
    resp = client.get(f'/reports/deliverables?project_id={project.id}')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert '3.5' in html


def test_deliverables_report_hours_scoped_to_the_right_project_only(
        client, owner_user, make_client, make_project, make_task, db_session):
    """Two projects under the same owner, each with its own time entries -
    a project's reported hours must be exactly its own, never the other
    project's added in."""
    from models.models import TimeEntry

    a_client = make_client(owner_user)
    project_a = make_project(a_client)
    project_b = make_project(a_client)
    task_a = make_task(project_a)
    task_b = make_task(project_b)

    db_session.add(TimeEntry(task_id=task_a.id, user_id=owner_user.id,
                              start_time=datetime.utcnow(), end_time=datetime.utcnow(), duration=1.5))
    db_session.add(TimeEntry(task_id=task_b.id, user_id=owner_user.id,
                              start_time=datetime.utcnow(), end_time=datetime.utcnow(), duration=9.0))
    db_session.commit()

    login_as(client, owner_user)
    resp_a = client.get(f'/reports/deliverables?project_id={project_a.id}')
    assert resp_a.status_code == 200
    html_a = resp_a.get_data(as_text=True)
    assert '1.5' in html_a
    # project_b's own 9.0-hour entry must never leak into project_a's report
    # (build_deliverables_report scopes tasks strictly to the requested
    # project, not the whole owner's account).
    assert '9.0' not in html_a
