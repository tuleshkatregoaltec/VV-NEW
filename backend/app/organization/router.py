import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.billing import service as billing_service
from app.billing.models import CustomerPortalResponse, SubscriptionResponse
from app.core.dependencies import (
    AuthContext,
    require_active_owner,
    require_admin,
    require_auth,
    require_owner_membership,
)
from app.organization import invitations
from app.organization import service as organization_service
from app.organization.models import (
    AcceptInviteRequest,
    AcceptInviteResponse,
    InviteUserRequest,
    InviteUserResponse,
    OperationSuccessResponse,
    OrganizationCreateRequest,
    OrganizationDetailResponse,
    OrganizationInvitationResponse,
    OrganizationResponse,
    OrganizationUpdateRequest,
    OwnerOrganizationCreateRequest,
    OwnerOrganizationCreateResponse,
    UserTokenLimitUpdateResponse,
    UserUpdateRequest,
    ValidateInviteTokenResponse,
)
from app.postgres import get_db_session
from app.providers.auth import AuthenticatedUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/organization", tags=["organization"])


def _require_owner_org_access(context: AuthContext, org_id: str) -> None:
    if context.organization.id != org_id:
        raise HTTPException(status_code=403, detail="Organization access denied")


@router.post("/create", response_model=OwnerOrganizationCreateResponse)
async def create_owner_organization(
    request: OwnerOrganizationCreateRequest,
    current_user: AuthenticatedUser = Depends(require_auth),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Create organization for owner during self-signup (before checkout).

    Idempotent: If user already owns an organization, updates and returns existing org_id.
    This allows users to retry checkout after abandoning payment.
    """
    return await organization_service.create_owner_organization(
        request=request, current_user=current_user, db=db
    )


class OrganizationMemberResponse(BaseModel):
    """Organization member response."""

    id: str
    email: str
    first_name: str
    last_name: str
    role: str


@router.get("/members", response_model=List[OrganizationMemberResponse])
async def get_organization_members(
    context: AuthContext = Depends(require_owner_membership),
    db: AsyncSession = Depends(get_db_session),
):
    """Get organization members (query from memberships table)"""
    return [
        OrganizationMemberResponse(**member)
        for member in await organization_service.get_organization_members(
            context_organization=context.organization, db=db
        )
    ]


@router.get("/invitations", response_model=List[OrganizationInvitationResponse])
async def get_organization_invitations(
    context: AuthContext = Depends(require_owner_membership),
    db: AsyncSession = Depends(get_db_session),
):
    """Get pending invitations for the current organization."""
    return await invitations.get_organization_invitations(
        context_organization=context.organization, db=db
    )


@router.delete("/invitations/{invitation_id}", response_model=OperationSuccessResponse)
async def delete_organization_invitation(
    invitation_id: str,
    context: AuthContext = Depends(require_owner_membership),
    db: AsyncSession = Depends(get_db_session),
):
    """Cancel a pending invitation for the current organization."""
    return await invitations.delete_organization_invitation(
        invitation_id=invitation_id, context_organization=context.organization, db=db
    )


@router.delete("/members/{user_id}", response_model=OperationSuccessResponse)
async def remove_member(
    user_id: str,
    context: AuthContext = Depends(require_owner_membership),
    db: AsyncSession = Depends(get_db_session),
):
    """Remove user from organization (delete membership to free seat)"""
    return await organization_service.remove_member(
        user_id=user_id, context_organization=context.organization, context_user=context.user, db=db
    )


@router.get("/subscription", response_model=SubscriptionResponse)
async def get_organization_subscription(
    context: AuthContext = Depends(require_owner_membership),
    db: AsyncSession = Depends(get_db_session),
):
    """Get organization subscription details (owner only)."""
    return await billing_service.get_organization_subscription(
        context_organization=context.organization, context_subscription=context.subscription, db=db
    )


@router.get("/customer-portal", response_model=CustomerPortalResponse)
async def get_stripe_customer_portal(
    context: AuthContext = Depends(require_owner_membership),
    db: AsyncSession = Depends(get_db_session),
):
    """Get Stripe Customer Portal URL (admin only)."""
    return await billing_service.get_stripe_customer_portal(
        context_organization=context.organization, db=db
    )


@router.post("/invite-user", response_model=InviteUserResponse)
async def invite_user_to_organization(
    request: InviteUserRequest,
    context: AuthContext = Depends(require_active_owner),
    db: AsyncSession = Depends(get_db_session),
):
    """Invite user to organization (admin only)."""
    return await invitations.invite_user_to_organization(
        request=request, context_organization=context.organization, context_user=context.user, db=db
    )


@router.get("/validate-invite-token", response_model=ValidateInviteTokenResponse)
async def validate_invite_token(
    token: str,
    db: AsyncSession = Depends(get_db_session),
):
    """Validate invitation token and return organization details."""
    return await invitations.validate_invite_token(token=token, db=db)


@router.post(
    "/accept-invite",
    response_model=AcceptInviteResponse,
)
async def accept_invite(
    request: AcceptInviteRequest,
    current_user: AuthenticatedUser = Depends(require_auth),
    db: AsyncSession = Depends(get_db_session),
):
    """Accept organization invitation with conflict detection."""
    return await invitations.accept_invite(request=request, current_user=current_user, db=db)


# --- Organization management endpoints (migrated from admin) ---


@router.post(
    "/organizations",
    status_code=201,
    response_model=OrganizationResponse,
)
async def create_organization(
    organization_in: OrganizationCreateRequest,
    db: AsyncSession = Depends(get_db_session),
    _admin_user: AuthenticatedUser = Depends(require_admin),
):
    """Create a new organization (admin only)."""
    return await organization_service.create_organization_with_owner(
        organization_in=organization_in, db=db
    )


@router.get("/organizations", response_model=List[OrganizationResponse])
async def list_organization(
    db: AsyncSession = Depends(get_db_session), _admin: None = Depends(require_admin)
):
    """List all organizations with user counts (admin only)."""
    return await organization_service.list_organization_details(db=db)


@router.get("/organizations/{org_id}", response_model=OrganizationDetailResponse)
async def get_organization(
    org_id: str,
    db: AsyncSession = Depends(get_db_session),
    context: AuthContext = Depends(require_owner_membership),
):
    """Get organization details with user list (organization admin)."""
    _require_owner_org_access(context, org_id)
    return await organization_service.get_organization_details(org_id=org_id, db=db)


@router.put("/organizations/{org_id}", response_model=OrganizationResponse)
async def update_organization(
    org_id: str,
    organization_update: OrganizationUpdateRequest,
    db: AsyncSession = Depends(get_db_session),
    context: AuthContext = Depends(require_owner_membership),
):
    """Update organization settings (organization admin)."""
    _require_owner_org_access(context, org_id)
    return await organization_service.update_organization_for_owner(
        org_id=org_id, organization_update=organization_update, db=db
    )


@router.delete("/organizations/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_organization(
    org_id: str,
    db: AsyncSession = Depends(get_db_session),
    _admin: None = Depends(require_admin),
):
    """Delete organization (admin only)."""
    return await organization_service.delete_organization_checked(org_id=org_id, db=db)


@router.put("/users/{user_id}", response_model=UserTokenLimitUpdateResponse)
async def update_user(
    user_id: str,
    user_update: UserUpdateRequest,
    db: AsyncSession = Depends(get_db_session),
    context: AuthContext = Depends(require_owner_membership),
) -> UserTokenLimitUpdateResponse:
    """Update user daily token limit (organization admin)."""
    return await organization_service.update_member_token_limit(
        user_id=user_id, user_update=user_update, db=db, context_organization=context.organization
    )
