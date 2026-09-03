"""
Staff-facing Contract management for the ELIZA Project Management App.

Same document family and conventions as Quotes/Invoices (api/billing.py):
tenant scoping through the required client's owner_id (Contract has no
owner_id of its own), 404-not-403 ownership checks, CT-00001 numbering
assigned inside the creating transaction, and a tokenized no-login portal
link. The client-facing signing flow (view/sign/decline via the portal
link) is stage 2 - the portal URL is built and emailed now, but the route
it points at doesn't exist yet, so it 404s until that ships.

Immutability guarantee: a contract is editable ONLY while draft. At send
time body_snapshot is frozen to a copy of body - that snapshot is the legal
text the client signs - and every later edit attempt is refused. A SIGNED
contract can never be deleted: it's a legal record.

That immutability is deliberate and stays. The escape hatches around it are
AMEND (contract_amend below - clone a settled contract into a new draft that
supersedes it, leaving the original untouched) and VOID (contract_void -
withdraw a sent, unsigned contract so its portal link stops accepting a
signature). Neither one ever rewrites a contract the client has already seen.
"""
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, abort
from flask_login import login_required, current_user

from models import db
from models.models import Client, Project, ActivityLog
from models.billing import Contract, ContractStatus, generate_public_token
from forms import ContractForm
from utils.email_utils import send_contract_email
from utils.document_print import build_contract_print_context
from utils.analytics import log_event

contracts_bp = Blueprint('contracts', __name__)


def _portal_url(contract):
    """Fully-qualified tokenized portal link for this contract.

    Built literally rather than via url_for: the portal signing route
    (/portal/contract/<token>) is stage 2 and doesn't exist yet, so
    url_for('portal.view_contract', ...) would raise BuildError. The path
    matches api/portal.py's established /portal/<doc>/<token> shape exactly,
    so the link becomes live unchanged the moment stage 2 lands.
    """
    return current_app.config['BASE_URL'].rstrip('/') + '/portal/contract/' + contract.public_token


def _log_contract_activity(action_type, contract, description):
    """ActivityLog entry in the established user_id/owner_id pair convention
    (see client_create in api/index.py). Adds to the session only - the
    caller owns the commit."""
    db.session.add(ActivityLog(
        user_id=current_user.id,
        owner_id=current_user.get_owner_id(),  # tenant isolation: activity log entries belong to the acting tenant
        action_type=action_type,
        entity_type='contract',
        entity_id=contract.id,
        description=description,
    ))


@contracts_bp.route('/contracts')
@login_required
def contracts_list():
    """List all contracts"""
    # Paginated so the page doesn't load every contract row on every visit.
    page = request.args.get('page', 1, type=int)
    # Contract has no owner_id of its own - scope through its required client_id.
    pagination = Contract.query.join(Client).filter(Client.owner_id == current_user.get_owner_id()) \
        .order_by(Contract.created_at.desc()).paginate(page=page, per_page=20, error_out=False)
    return render_template('contracts/index.html', contracts=pagination.items, pagination=pagination)


@contracts_bp.route('/contracts/create', methods=['GET', 'POST'])
@login_required
def contract_create():
    """Create a new contract (always starts as a draft)"""
    # owner_id scopes the client_id/project_id dropdowns to this user's own records.
    form = ContractForm(owner_id=current_user.get_owner_id())

    if form.validate_on_submit():
        # Defense in depth: the form's client/project choices are already scoped to
        # this user's own records, but a crafted POST can submit any id directly -
        # re-check ownership server-side before trusting these foreign keys.
        client = Client.query.get(form.client_id.data)
        project = Project.query.get(form.project_id.data) if form.project_id.data else None
        if not client or client.owner_id != current_user.get_owner_id() or (project and project.owner_id != current_user.get_owner_id()):
            flash('Invalid client or project selected.', 'danger')
        else:
            try:
                contract = Contract(
                    contract_number='PENDING',
                    client_id=form.client_id.data,
                    project_id=form.project_id.data or None,
                    created_by_id=current_user.id,
                    title=form.title.data,
                    body=form.body.data,
                )
                db.session.add(contract)
                db.session.flush()  # assigns contract.id for numbering, still inside the transaction
                contract.contract_number = f"CT-{contract.id:05d}"

                _log_contract_activity('created', contract, f'Created contract: {contract.contract_number} - {contract.title}')
                db.session.commit()
                log_event('contract_created', user_id=current_user.id, contract_id=contract.id)
                flash(f'Contract {contract.contract_number} created.', 'success')
                return redirect(url_for('contracts.contract_detail', contract_id=contract.id))
            except Exception as e:
                db.session.rollback()
                current_app.logger.error(f"Error creating contract: {str(e)}")
                flash(f'Error creating contract: {str(e)}', 'danger')

    return render_template('contracts/form.html', form=form, contract=None)


@contracts_bp.route('/contracts/<int:contract_id>')
@login_required
def contract_detail(contract_id):
    """View a contract (staff view)"""
    contract = Contract.query.get_or_404(contract_id)
    # Contract has no owner_id of its own - ownership is via its required client.
    if contract.client.owner_id != current_user.get_owner_id():
        abort(404)
    return render_template('contracts/detail.html', contract=contract, portal_url=_portal_url(contract))


@contracts_bp.route('/contracts/<int:contract_id>/print')
@login_required
def contract_print(contract_id):
    """Standalone printable/PDF view of a contract (staff view).

    Prints body_snapshot once one exists (the frozen legal text), falling
    back to the live body only for a draft that has never been sent - the
    same snapshot-or-body choice the detail view makes."""
    contract = Contract.query.get_or_404(contract_id)
    # Contract has no owner_id of its own - ownership is via its required client.
    if contract.client.owner_id != current_user.get_owner_id():
        abort(404)
    return render_template('print/contract.html',
                           **build_contract_print_context(contract, contract.body_snapshot or contract.body))


@contracts_bp.route('/contracts/<int:contract_id>/edit', methods=['GET', 'POST'])
@login_required
def contract_edit(contract_id):
    """Edit a contract - DRAFT ONLY. Once sent, body_snapshot is the frozen
    legal text the client signs, so editing is refused outright (stricter
    than quotes, which allow editing while merely sent)."""
    contract = Contract.query.get_or_404(contract_id)
    # Contract has no owner_id of its own - ownership is via its required client.
    if contract.client.owner_id != current_user.get_owner_id():
        abort(404)
    if contract.status != ContractStatus.DRAFT:
        flash('This contract cannot be edited after sending - the sent text is what the client signs.', 'warning')
        return redirect(url_for('contracts.contract_detail', contract_id=contract.id))

    # owner_id scopes the client_id/project_id dropdowns to this user's own records.
    form = ContractForm(obj=contract, owner_id=current_user.get_owner_id())

    if form.validate_on_submit():
        # Same defense in depth as contract_create above - re-check ownership
        # of the submitted foreign keys server-side.
        client = Client.query.get(form.client_id.data)
        project = Project.query.get(form.project_id.data) if form.project_id.data else None
        if not client or client.owner_id != current_user.get_owner_id() or (project and project.owner_id != current_user.get_owner_id()):
            flash('Invalid client or project selected.', 'danger')
        else:
            try:
                contract.client_id = form.client_id.data
                contract.project_id = form.project_id.data or None
                contract.title = form.title.data
                contract.body = form.body.data

                _log_contract_activity('updated', contract, f'Updated contract: {contract.contract_number} - {contract.title}')
                db.session.commit()
                flash(f'Contract {contract.contract_number} updated.', 'success')
                return redirect(url_for('contracts.contract_detail', contract_id=contract.id))
            except Exception as e:
                db.session.rollback()
                current_app.logger.error(f"Error updating contract: {str(e)}")
                flash(f'Error updating contract: {str(e)}', 'danger')
    elif request.method == 'GET':
        form.project_id.data = contract.project_id or 0

    return render_template('contracts/form.html', form=form, contract=contract)


@contracts_bp.route('/contracts/<int:contract_id>/send', methods=['POST'])
@login_required
def contract_send(contract_id):
    """Email the client a portal link to review and sign this contract.

    Freezes body_snapshot = body on the FIRST send - after that the snapshot
    is the legal text and is never overwritten, even on a re-send. Refused
    entirely once the client has signed or declined.
    """
    contract = Contract.query.get_or_404(contract_id)
    # Contract has no owner_id of its own - ownership is via its required client.
    if contract.client.owner_id != current_user.get_owner_id():
        abort(404)
    if contract.status == ContractStatus.VOIDED:
        # Distinct message from the "already responded to" one below: a voided
        # contract wasn't answered by the client, it was withdrawn by this
        # user, and the way forward is a new version rather than a re-send.
        flash('This contract has been voided and cannot be sent. Amend it to issue a new version.', 'warning')
        return redirect(url_for('contracts.contract_detail', contract_id=contract.id))
    if contract.status not in (ContractStatus.DRAFT, ContractStatus.SENT):
        flash('This contract has already been responded to and cannot be re-sent.', 'warning')
        return redirect(url_for('contracts.contract_detail', contract_id=contract.id))
    if not contract.client.email:
        flash('This client has no email address on file. Add one on their record, then send.', 'danger')
        return redirect(url_for('contracts.contract_detail', contract_id=contract.id))

    try:
        if contract.body_snapshot is None:
            # Frozen exactly once, at first send - the legal text.
            contract.body_snapshot = contract.body
        contract.status = ContractStatus.SENT
        contract.sent_at = datetime.utcnow()
        _log_contract_activity('sent', contract, f'Sent contract {contract.contract_number} to {contract.client.name}')
        db.session.commit()
        log_event('contract_sent', user_id=current_user.id, contract_id=contract.id)

        send_contract_email(contract, _portal_url(contract))
        flash(f'Contract sent to {contract.client.email}.', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error sending contract: {str(e)}")
        flash(f'Error sending contract: {str(e)}', 'danger')

    return redirect(url_for('contracts.contract_detail', contract_id=contract.id))


@contracts_bp.route('/contracts/<int:contract_id>/amend', methods=['POST'])
@login_required
def contract_amend(contract_id):
    """Clone a settled contract into a NEW draft that supersedes it.

    This is the escape hatch that lets the immutability rule above stay
    absolute. Editing a sent contract is refused forever (a signature is
    meaningless if the text can change under it), so changing the terms means
    issuing a new document: same client/project/title, the body the client
    actually saw as its starting point, a fresh contract number and a fresh
    public token, version = original + 1, and supersedes_id pointing back at
    the original.

    The ORIGINAL IS LEFT COMPLETELY UNTOUCHED - status, signature, audit
    trail, token and all. It is permanent history; the amendment is a
    separate document that happens to know what it replaces.

    The new contract starts from body_snapshot when there is one (what was
    actually sent) and falls back to body only if there somehow isn't, so the
    amendment begins from the text the client read, not a working copy.
    """
    contract = Contract.query.get_or_404(contract_id)
    # Contract has no owner_id of its own - ownership is via its required client.
    if contract.client.owner_id != current_user.get_owner_id():
        abort(404)
    if not contract.is_amendable:
        flash('Only a sent, signed or declined contract can be amended.', 'warning')
        return redirect(url_for('contracts.contract_detail', contract_id=contract.id))

    try:
        amendment = Contract(
            contract_number='PENDING',
            client_id=contract.client_id,
            project_id=contract.project_id,
            created_by_id=current_user.id,
            title=contract.title,
            # What was actually sent, not the working copy.
            body=contract.body_snapshot or contract.body,
            status=ContractStatus.DRAFT,
            supersedes_id=contract.id,
            version=contract.version_number + 1,
            # Explicit fresh token rather than relying on the column default -
            # the old token must never be reused: it is in the client's inbox
            # and still points at the original document, which stays live.
            public_token=generate_public_token(),
        )
        db.session.add(amendment)
        db.session.flush()  # assigns amendment.id for numbering, still inside the transaction
        amendment.contract_number = f"CT-{amendment.id:05d}"

        _log_contract_activity(
            'amended', amendment,
            f'Amended contract {contract.contract_number} as {amendment.contract_number} '
            f'(version {amendment.version_number})')
        db.session.commit()
        log_event('contract_amended', user_id=current_user.id,
                  contract_id=amendment.id, supersedes_id=contract.id, version=amendment.version_number)
        flash(f'{amendment.contract_number} created as version {amendment.version_number} of '
              f'{contract.contract_number}. Edit the terms below, then send it.', 'success')
        # Straight to the edit page: the whole point of amending is changing
        # the terms, and the amendment is a draft so editing is allowed again.
        return redirect(url_for('contracts.contract_edit', contract_id=amendment.id))
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error amending contract {contract_id}: {str(e)}")
        flash(f'Error amending contract: {str(e)}', 'danger')

    return redirect(url_for('contracts.contract_detail', contract_id=contract.id))


@contracts_bp.route('/contracts/<int:contract_id>/void', methods=['POST'])
@login_required
def contract_void(contract_id):
    """Withdraw a SENT, unsigned contract.

    The client keeps the emailed link, so voiding cannot just delete the row:
    the portal page still renders (with a clear "withdrawn" banner - a 404
    would just look broken) but is_signable is False, and api/portal.py's
    sign/decline routes re-check that server-side and refuse.

    A SIGNED contract is never voided - it is a legal record, and the answer
    there is an amendment (a new document) or a written termination, not
    unwinding the signature.
    """
    contract = Contract.query.get_or_404(contract_id)
    # Contract has no owner_id of its own - ownership is via its required client.
    if contract.client.owner_id != current_user.get_owner_id():
        abort(404)
    if contract.status == ContractStatus.SIGNED:
        flash('A signed contract is a legal record and cannot be voided. Amend it to issue a new version instead.', 'danger')
        return redirect(url_for('contracts.contract_detail', contract_id=contract.id))
    if not contract.is_voidable:
        flash('Only a sent contract that has not been signed can be voided.', 'warning')
        return redirect(url_for('contracts.contract_detail', contract_id=contract.id))

    try:
        contract.status = ContractStatus.VOIDED
        contract.voided_at = datetime.utcnow()
        _log_contract_activity(
            'voided', contract,
            f'Voided contract {contract.contract_number} - withdrawn before signature')
        db.session.commit()
        log_event('contract_voided', user_id=current_user.id, contract_id=contract.id)
        flash(f'Contract {contract.contract_number} has been voided. Its client link can no longer be signed.', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error voiding contract {contract_id}: {str(e)}")
        flash(f'Error voiding contract: {str(e)}', 'danger')

    return redirect(url_for('contracts.contract_detail', contract_id=contract.id))


@contracts_bp.route('/contracts/<int:contract_id>/delete', methods=['POST'])
@login_required
def contract_delete(contract_id):
    """Delete a contract. POST-only (deliberately NOT the legacy GET-delete
    pattern quotes/invoices still use - the audit flagged that class as
    CSRF-exposed). A SIGNED contract is a legal record and is never deleted.
    """
    contract = Contract.query.get_or_404(contract_id)
    # Contract has no owner_id of its own - ownership is via its required client.
    if contract.client.owner_id != current_user.get_owner_id():
        abort(404)
    if contract.status == ContractStatus.SIGNED:
        flash('A signed contract is a legal record and cannot be deleted.', 'danger')
        return redirect(url_for('contracts.contract_detail', contract_id=contract.id))
    if contract.superseded_by:
        # A later version points at this one via supersedes_id. Deleting it
        # would either fail on the foreign key or (worse, if that pointer were
        # ever nulled) silently orphan the amendment chain, so refuse and say
        # which version depends on it. Delete the amendment first if the whole
        # chain really is meant to go.
        newer = contract.latest_amendment
        flash(f'This contract cannot be deleted because {newer.contract_number} amends it. '
              f'Delete the newer version first.', 'danger')
        return redirect(url_for('contracts.contract_detail', contract_id=contract.id))
    try:
        _log_contract_activity('deleted', contract, f'Deleted contract: {contract.contract_number} - {contract.title}')
        db.session.delete(contract)
        db.session.commit()
        flash('Contract deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting contract: {str(e)}', 'danger')
        return redirect(url_for('contracts.contract_detail', contract_id=contract.id))
    return redirect(url_for('contracts.contracts_list'))
