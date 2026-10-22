import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.service import (
    send_magic_link_email,
)
from app.config import settings
from app.organization import repository
from app.organization.models import (
    AcceptInviteRequest,
    AcceptInviteResponse,
    InviteConflictResponse,
    InviteUserRequest,
    InviteUserResponse,
    OperationSuccessResponse,
    OrganizationInvitation,
    OrganizationInvitationResponse,
    ValidateInviteTokenResponse,
)
from app.organization.service import (
    add_user_to_organization,
    get_member_count,
    get_membership_for_user,
    get_organization_by_id,
    get_pending_invitation_count,
)
from app.providers.auth import AuthenticatedUser


def generate_invitation_token() -> str:
    """Generate secure random token for invitation"""
    return secrets.token_urlsafe(32)


async def create_invitation(
    db: AsyncSession,
    organization_id: str,
    email: str,
    invited_by_user_id: str,
    role: str = "member",
    expires_in_days: int = 7,
) -> OrganizationInvitation:
    """Create organization invitation record"""
    token = generate_invitation_token()
    expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days)

    invitation = OrganizationInvitation(
        organization_id=organization_id,
        email=email,
        role=role,
        token=token,
        status="pending",
        invited_by_user_id=invited_by_user_id,
        expires_at=expires_at,
        created_at=datetime.now(timezone.utc),
    )

    repository.save_invitation(db, invitation)
    await db.commit()
    await db.refresh(invitation)
    return invitation


async def expire_pending_invitations(
    db: AsyncSession, organization_id: str, email: str, exclude_token: str | None = None
) -> int:
    """Invalidate older pending invitations for the same org/email."""
    invitations = await repository.list_pending_invitations_for_email(db, organization_id, email)
    if not invitations:
        return 0

    now = datetime.now(timezone.utc)
    for invitation in invitations:
        if exclude_token and invitation.token == exclude_token:
            continue
        invitation.status = "expired"
        invitation.expires_at = now
        repository.save_invitation(db, invitation)

    await db.commit()
    return len([invitation for invitation in invitations if invitation.token != exclude_token])


async def get_invitation_by_token(db: AsyncSession, token: str) -> Optional[OrganizationInvitation]:
    """Get invitation by token"""
    return await repository.get_invitation_by_token(db, token)


async def get_invitation_by_id(
    db: AsyncSession, invitation_id: str
) -> Optional[OrganizationInvitation]:
    """Get invitation by ID."""
    return await repository.get_invitation_by_id(db, invitation_id)


async def mark_invitation_accepted(
    db: AsyncSession, invitation: OrganizationInvitation
) -> OrganizationInvitation:
    """Mark invitation as accepted"""
    invitation.status = "accepted"
    invitation.accepted_at = datetime.now(timezone.utc)
    repository.save_invitation(db, invitation)
    await db.commit()
    await db.refresh(invitation)
    return invitation


async def cancel_invitation(
    db: AsyncSession, invitation: OrganizationInvitation
) -> OrganizationInvitation:
    """Cancel a pending invitation and free its reserved seat."""
    invitation.status = "declined"
    invitation.expires_at = datetime.now(timezone.utc)
    repository.save_invitation(db, invitation)
    await db.commit()
    await db.refresh(invitation)
    return invitation


async def get_pending_invitation_or_error(db: AsyncSession, token: str):
    invitation = await get_invitation_by_token(db, token)

    if not invitation:
        raise HTTPException(status_code=404, detail="Invitation not found")

    if invitation.status != "pending":
        if invitation.status == "accepted":
            raise HTTPException(status_code=400, detail="Invitation already accepted")
        raise HTTPException(status_code=400, detail="Invitation is no longer valid")

    now = datetime.now(timezone.utc)
    if invitation.expires_at < now:
        raise HTTPException(status_code=400, detail="Invitation has expired")

    return invitation


async def get_organization_invitations(context_organization, db: AsyncSession):
    """Get pending invitations for the current organization."""
    invitations = await repository.list_pending_invitations(db, context_organization.id)

    now = datetime.now(timezone.utc)
    response = []
    for invitation in invitations:
        if invitation.expires_at < now:
            continue
        response.append(
            OrganizationInvitationResponse(
                id=invitation.id,
                email=invitation.email,
                role=invitation.role,
                status=invitation.status,
                expires_at=invitation.expires_at,
                created_at=invitation.created_at,
            )
        )

    return response


async def delete_organization_invitation(
    invitation_id: str, context_organization, db: AsyncSession
):
    """Cancel a pending invitation for the current organization."""
    invitation = await get_invitation_by_id(db, invitation_id)
    if not invitation or invitation.organization_id != context_organization.id:
        raise HTTPException(status_code=404, detail="Invitation not found")

    if invitation.status != "pending":
        raise HTTPException(status_code=400, detail="Invitation is no longer pending")

    if invitation.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Invitation has already expired")

    await cancel_invitation(db, invitation)

    return OperationSuccessResponse(success=True)


async def invite_user_to_organization(
    request: InviteUserRequest, context_organization, context_user, db: AsyncSession
):
    """Invite user to organization (admin only)."""

    organization = context_organization

    active_user_count = await get_member_count(db, organization.id)
    pending_invitation_count = await get_pending_invitation_count(db, organization.id)
    reserved_seat_count = active_user_count + pending_invitation_count
    if reserved_seat_count >= organization.max_users:
        raise HTTPException(
            status_code=400,
            detail=(
                "Organization has reached maximum seat limit "
                f"({organization.max_users}) including pending invitations"
            ),
        )

    # Create invitation record
    invitation = await create_invitation(
        db=db,
        organization_id=organization.id,
        email=request.email,
        invited_by_user_id=context_user.id,
        role="member",
    )

    redirect_url = f"{settings.FRONTEND_URL}/invite?token={invitation.token}"
    auth_result = await send_magic_link_email(
        email=request.email,
        redirect_url=redirect_url,
    )
    if not auth_result["success"]:
        invitation.status = "expired"
        repository.save_invitation(db, invitation)
        await db.commit()
        raise HTTPException(status_code=400, detail=auth_result["error"])

    await expire_pending_invitations(
        db, organization.id, request.email, exclude_token=invitation.token
    )

    return InviteUserResponse(success=True, invitation_token=invitation.token)


async def validate_invite_token(token: str, db: AsyncSession):
    """Validate invitation token and return organization details."""
    invitation = await get_pending_invitation_or_error(db, token)

    # Get organization details
    organization = await get_organization_by_id(db, invitation.organization_id)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    return ValidateInviteTokenResponse(
        organization_name=organization.name,
        organization_id=organization.id,
        role=invitation.role,
        email=invitation.email,
        expires_at=invitation.expires_at,
        status=invitation.status,
    )


async def accept_invite(
    request: AcceptInviteRequest, current_user: AuthenticatedUser, db: AsyncSession
):
    """Accept organization invitation with conflict detection."""
    invitation = await get_pending_invitation_or_error(db, request.token)

    # Validate email matches
    if invitation.email.lower() != current_user.email.lower():
        raise HTTPException(status_code=403, detail="Invitation email does not match your account")

    # Check for existing membership (conflict detection)
    existing_membership = await get_membership_for_user(db, current_user.id)

    if existing_membership:
        # User already has a membership - return conflict details
        current_org = await get_organization_by_id(db, existing_membership.organization_id)
        if not current_org:
            raise HTTPException(status_code=500, detail="Current organization not found")

        member_count = await get_member_count(db, current_org.id)
        is_single_seat = member_count == 1

        conflict_response = InviteConflictResponse(
            conflict=True,
            current_org_id=current_org.id,
            current_org_name=current_org.name,
            current_role=existing_membership.role,
            member_count=member_count,
            is_single_seat=is_single_seat,
        )
        raise HTTPException(status_code=409, detail=conflict_response.model_dump())

    organization = await get_organization_by_id(db, invitation.organization_id)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    member_count = await get_member_count(db, organization.id)
    if member_count >= organization.max_users:
        raise HTTPException(
            status_code=409,
            detail="This organization has no available seats left for this invitation.",
        )

    # No conflict - create membership
    await add_user_to_organization(
        db=db,
        org_id=invitation.organization_id,
        user_id=current_user.id,
        role=invitation.role,
    )

    # Mark invitation as accepted
    await mark_invitation_accepted(db, invitation)

    return AcceptInviteResponse(
        success=True,
        organization_id=organization.id,
        organization_name=organization.name,
        role=invitation.role,
    )
