"""
Team member management for the ELIZA Project Management App.

An account owner invites team members, who then share the owner's tenant
data (see User.get_owner_id() in models/models.py). This module is the
owner-facing side of that: the dashboard, sending invites, and revoking
invites/removing members. The public accept-invite route (no login required)
lives in api/index.py, mirroring reset_password()'s shape - see there.
"""
from flask import Blueprint, render_template, redirect, url_for, flash, current_app, abort
from flask_login import login_required, current_user

from models import db
from models.models import User, TeamMembership, TeamInvite, UserRole, ActivityLog
from models.subscription import Subscription
from forms import TeamInviteForm
from utils.email_utils import send_team_invite_email

team_bp = Blueprint('team', __name__)


def _require_owner():
    """Only the account owner may manage their team - a team member sharing
    the owner's data has no invites of their own to send/revoke and no
    members of their own to remove. Mirrors admin_required's flash+redirect
    shape exactly (api/admin.py, including the redirect target of 'home')
    rather than the 404 used for "not your resource" lookups elsewhere:
    this is a page-level permission check, not a specific record whose
    existence we want to hide from another tenant. Redirecting to 'home'
    rather than back to team.dashboard matters here specifically - dashboard()
    below calls this same helper, so redirecting to it instead would be an
    infinite redirect loop for every non-owner.

    Returns a redirect response if the current user isn't the owner, else None.
    """
    if current_user.id != current_user.get_owner_id():
        flash('Only the account owner can manage team members.', 'danger')
        return redirect(url_for('home'))
    return None


@team_bp.route('/team')
@login_required
def dashboard():
    """Shows active members, pending invites, and the invite form. Owner-only
    (see _require_owner) - a team member is bounced to the homepage with a
    flash rather than seeing the management controls.

    FOLLOWUP: this means a team member gets no use at all from the "Team"
    nav link that's visible to them too (see base.html) - a read-only "your
    teammates" view for non-owners would be a better landing than a bounce,
    but is out of scope for this batch.
    """
    not_owner = _require_owner()
    if not_owner:
        return not_owner

    owner_id = current_user.get_owner_id()
    memberships = TeamMembership.query.filter_by(account_owner_id=owner_id, is_active=True).all()
    pending_invites = TeamInvite.query.filter_by(inviter_id=owner_id, status='pending').order_by(TeamInvite.created_at.desc()).all()
    form = TeamInviteForm()
    return render_template('team/index.html', memberships=memberships, pending_invites=pending_invites, form=form)


@team_bp.route('/team/invite', methods=['POST'])
@login_required
def invite():
    """Send a team invite, enforcing the subscription plan's max_users seat cap."""
    not_owner = _require_owner()
    if not_owner:
        return not_owner

    owner_id = current_user.get_owner_id()
    form = TeamInviteForm()
    if form.validate_on_submit():
        # Seat cap: active members + invites already pending (uninvited seats
        # a pending invite could still fill) - all counted together so a
        # burst of invites can't outrun a small cap by sending them before
        # any is individually accepted. The owner is NOT counted here -
        # max_users is advertised everywhere (plan descriptions in
        # api/subscription.py, the admin form field in forms.py, the pricing
        # page in templates/landing.html) as how many people can be INVITED,
        # not the account's total user count including the owner.
        active_member_count = TeamMembership.query.filter_by(account_owner_id=owner_id, is_active=True).count()
        pending_invite_count = TeamInvite.query.filter_by(inviter_id=owner_id, status='pending').count()
        seats_used = active_member_count + pending_invite_count

        # 0 means unlimited, and no active subscription also means
        # unlimited - same convention as SubscriptionPlan's other limits
        # (see models/subscription.py), so this never blocks an owner's own
        # free daily use of the app while they have no paid plan.
        subscription = Subscription.query.filter_by(user_id=owner_id, is_active=True).first()
        max_users = subscription.plan.max_users if subscription else 0

        if max_users and seats_used >= max_users:
            flash(f'You have reached your plan\'s limit of {max_users} team members. Upgrade your plan to invite more.', 'danger')
            return redirect(url_for('team.dashboard'))

        # An email that already has a User account can't go through the
        # ordinary invite-by-email flow below: accept_invite() in
        # api/index.py unconditionally refuses to create a second account
        # for an email that already has one, so the TeamInvite created below
        # would just sit at status='pending' forever with no signal to the
        # owner that anything failed. Check up front instead.
        existing_user = User.query.filter_by(email=form.email.data).first()
        if existing_user:
            # remove_member() only ever soft-deletes (is_active=False) and
            # never touches the User row, so a previously-removed member of
            # THIS SAME team still has their old TeamMembership row sitting
            # inactive - reactivate it rather than trying to invite/create a
            # second account for them. No new TeamInvite/email needed: the
            # account already exists, it's simply being turned back on.
            existing_membership = TeamMembership.query.filter_by(
                account_owner_id=owner_id, member_user_id=existing_user.id
            ).first()
            if existing_membership:
                existing_membership.is_active = True
                existing_membership.role = UserRole[form.role.data]
                activity = ActivityLog(
                    user_id=current_user.id,
                    owner_id=owner_id,  # tenant isolation: activity log entries belong to the acting tenant
                    action_type='re-added',
                    entity_type='team_membership',
                    entity_id=existing_membership.id,
                    description=f'Re-added {existing_user.email} to the team'
                )
                db.session.add(activity)
                db.session.commit()
                flash(f'{existing_user.email} has been re-added to your team.', 'success')
                return redirect(url_for('team.dashboard'))

            # No membership under THIS owner at all - some other, unrelated
            # platform account (e.g. someone else's own tenant). Refuse
            # clearly rather than repeat the silent pending-forever dead end
            # above. Doesn't solve the harder case of merging platform
            # accounts - that's out of scope here.
            flash('An account with this email already exists on ELIZA and cannot be invited as a new team member.', 'danger')
            return redirect(url_for('team.dashboard'))

        team_invite = TeamInvite(
            inviter_id=current_user.id,
            invitee_email=form.email.data,
            role=UserRole[form.role.data],
            status='pending'
        )
        db.session.add(team_invite)
        db.session.commit()

        # Build from app.config['BASE_URL'] rather than url_for(_external=True) -
        # same reasoning as reset_password_request() in api/index.py: the
        # latter's scheme-guessing produces an https:// link even on the
        # plain-http local dev server (Talisman sets PREFERRED_URL_SCHEME to
        # https there), which the local server can't actually serve.
        accept_url = current_app.config['BASE_URL'].rstrip('/') + url_for('accept_invite', token=team_invite.get_invite_token())
        send_team_invite_email(team_invite, accept_url)

        flash(f'Invite sent to {team_invite.invitee_email}.', 'success')
        return redirect(url_for('team.dashboard'))

    for field, errors in form.errors.items():
        for error in errors:
            flash(f'{field}: {error}', 'danger')
    return redirect(url_for('team.dashboard'))


@team_bp.route('/team/invite/<int:invite_id>/revoke', methods=['POST'])
@login_required
def revoke_invite(invite_id):
    """Revoke a pending invite so its token can no longer be accepted."""
    not_owner = _require_owner()
    if not_owner:
        return not_owner

    team_invite = TeamInvite.query.get_or_404(invite_id)
    # 404 (not 403) so a caller can't distinguish "not yours" from "doesn't
    # exist" and confirm another tenant's invite id - same pattern used for
    # Client/Project/Quote/Invoice ownership checks in api/index.py and
    # api/billing.py.
    if team_invite.inviter_id != current_user.get_owner_id():
        abort(404)

    team_invite.status = 'revoked'
    db.session.commit()
    flash(f'Invite to {team_invite.invitee_email} has been revoked.', 'success')
    return redirect(url_for('team.dashboard'))


@team_bp.route('/team/members/<int:membership_id>/remove', methods=['POST'])
@login_required
def remove_member(membership_id):
    """Deactivate a team membership. Soft-deleted (is_active=False), never
    deleted outright, so the removed member's historical
    ActivityLog/Comment/TimeEntry authorship is preserved."""
    not_owner = _require_owner()
    if not_owner:
        return not_owner

    membership = TeamMembership.query.get_or_404(membership_id)
    # 404 (not 403) - same "don't confirm another tenant's record id exists"
    # reasoning as revoke_invite above.
    if membership.account_owner_id != current_user.get_owner_id():
        abort(404)

    membership.is_active = False
    db.session.commit()
    flash('Team member removed.', 'success')
    return redirect(url_for('team.dashboard'))
