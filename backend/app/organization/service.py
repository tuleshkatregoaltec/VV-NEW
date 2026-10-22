import logging
from datetime import datetime, timezone
from typing import Optional, Sequence
from uuid import uuid4

from fastapi import HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.service import (
    get_user_by_id,
    update_user_token_limit,
)
from app.organization import repository
from app.organization.models import (
    OperationSuccessResponse,
    Organization,
    OrganizationCreateRequest,
    OrganizationDetailResponse,
    OrganizationMembership,
    OrganizationResponse,
    OrganizationUpdateRequest,
    OrganizationUserDetailResponse,
    OwnerOrganizationCreateRequest,
    OwnerOrganizationCreateResponse,
    UserTokenLimitUpdateResponse,
    UserUpdateRequest,
)
from app.providers.auth import AuthenticatedUser
from app.token_usage.service import token_usage_service

logger = logging.getLogger(__name__)


async def get_free_org_id(db: AsyncSession):
    done = False
    while not done:
        uuid = uuid4()
        uuid_str = str(uuid)

        done = await get_organization_by_id(db, uuid_str) is None
    return uuid_str


async def get_organization_by_id(db: AsyncSession, org_id: str) -> Organization | None:
    """Get organization by ID"""
    return await repository.get_organization_by_id(db, org_id)


async def get_membership_for_user(
    db: AsyncSession, user_id: str
) -> Optional[OrganizationMembership]:
    """
    Get membership for user.

    Invariant: Returns exactly zero or one membership (enforced by UNIQUE user_id).
    Raises RuntimeError if constraint violated.
    """
    memberships = await repository.find_memberships_for_user(db, user_id)

    if len(memberships) > 1:
        raise RuntimeError(
            f"Constraint violation: User {user_id} has {len(memberships)} memberships. "
            "Expected at most one due to UNIQUE(user_id) constraint."
        )

    return memberships[0] if memberships else None


async def get_owner_membership_for_org(
    db: AsyncSession, org_id: str
) -> Optional[OrganizationMembership]:
    return await repository.get_owner_membership_for_org(db, org_id)


async def get_organization_for_user(db: AsyncSession, user_id: str) -> Organization | None:
    """Get organization via membership (replaces JSONB lookup)"""
    membership = await get_membership_for_user(db, user_id)
    if not membership:
        return None
    return await repository.get_organization_by_id(db, membership.organization_id)


async def get_member_count(db: AsyncSession, org_id: str) -> int:
    """Count members (replaces JSONB counting)"""
    return await repository.get_member_count(db, org_id)


async def get_active_user_count(db: AsyncSession, org_id: str) -> int:
    """Count active users in an organization (alias for get_member_count)"""
    return await get_member_count(db, org_id)


async def get_pending_invitation_count(db: AsyncSession, org_id: str) -> int:
    """Count non-expired pending invitations for seat reservation checks."""
    return await repository.get_pending_invitation_count(db, org_id, datetime.now(timezone.utc))


async def create_organization(db: AsyncSession, name: str, max_users: int) -> Organization:
    organization = Organization(
        id=await get_free_org_id(db),
        name=name,
        max_users=max_users,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    repository.save_organization(db, organization)
    await db.commit()
    await db.refresh(organization)
    return organization


async def list_organizations(db: AsyncSession) -> Sequence[Organization]:
    """List all organizations"""
    return await repository.list_organizations(db)


async def update_organization(db: AsyncSession, org_id: str, max_users: int) -> Organization | None:
    """Update organization settings"""
    organization = await repository.get_organization_by_id(db, org_id)

    if not organization:
        return None

    organization.max_users = max_users
    organization.updated_at = datetime.now(timezone.utc)

    repository.save_organization(db, organization)
    await db.commit()
    await db.refresh(organization)

    return organization


async def add_user_to_organization(
    db: AsyncSession, org_id: str, user_id: str, role: str = "member"
) -> OrganizationMembership:
    """Create membership record"""
    membership = OrganizationMembership(
        organization_id=org_id,
        user_id=user_id,
        role=role,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    repository.save_membership(db, membership)
    await db.commit()
    await db.refresh(membership)
    return membership


async def remove_user_from_organization(db: AsyncSession, org_id: str, user_id: str) -> bool:
    """Delete membership to free seat"""
    membership = await repository.get_membership_in_org(db, org_id, user_id)
    if not membership:
        return False

    await repository.delete_membership(db, membership)
    await db.commit()
    return True


async def update_subscription_status(
    db: AsyncSession, org_id: str, status: str
) -> Organization | None:
    """Update organization subscription status."""
    org = await repository.get_organization_by_id(db, org_id)
    if not org:
        return None
    org.subscription_status = status
    org.updated_at = datetime.now(timezone.utc)
    repository.save_organization(db, org)
    await db.commit()
    await db.refresh(org)
    return org


async def delete_organization(db: AsyncSession, org_id: str) -> bool:
    """Delete organization from WorkOS and local DB"""
    organization = await repository.get_organization_by_id(db, org_id)

    if not organization:
        return False

    # Delete locally
    await repository.delete_organization(db, organization)
    await db.commit()

    return True


async def list_memberships(db: AsyncSession, org_id: str):
    return await repository.list_memberships(db, org_id)


async def create_owner_organization(
    request: OwnerOrganizationCreateRequest, current_user: AuthenticatedUser, db: AsyncSession
):
    """
    Create organization for owner during self-signup (before checkout).

    Idempotent: If user already owns an organization, updates and returns existing org_id.
    This allows users to retry checkout after abandoning payment.
    """
    from uuid import uuid4

    existing_membership = await get_membership_for_user(db, current_user.id)

    if existing_membership:
        if existing_membership.role != "owner":
            raise HTTPException(
                status_code=400,
                detail="You already belong to an organization",
            )

        # User already owns an org - update existing org (retry scenario)
        org = await repository.get_organization_by_id(db, existing_membership.organization_id)
        if not org:
            raise HTTPException(
                status_code=500,
                detail="Organization membership exists but org not found",
            )

        # Update org details for retry
        org.name = request.name
        org.max_users = request.max_users
        org.updated_at = datetime.now(timezone.utc)

        repository.save_organization(db, org)
        await db.commit()
        await db.refresh(org)

        logger.info(
            f"Updated existing org {org.id} for user {current_user.id} (retry/abandon scenario)"
        )

        return OwnerOrganizationCreateResponse(
            organization_id=org.id,
            name=org.name,
            max_users=org.max_users,
            is_new=False,
        )

    # Create new organization
    org_id = str(uuid4())
    org = Organization(
        id=org_id,
        name=request.name,
        max_users=request.max_users,
        subscription_status=None,  # No subscription yet
        stripe_customer_id=None,  # Created during checkout
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    repository.save_organization(db, org)

    # Add owner membership
    membership = OrganizationMembership(
        organization_id=org.id,
        user_id=current_user.id,
        role="owner",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    repository.save_membership(db, membership)

    try:
        await db.commit()
        await db.refresh(org)
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to create organization for user {current_user.id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create organization: {str(e)}")

    logger.info(f"Created new organization {org.id} for user {current_user.id}")

    return OwnerOrganizationCreateResponse(
        organization_id=org.id,
        name=org.name,
        max_users=org.max_users,
        is_new=True,
    )


async def get_organization_members(context_organization, db: AsyncSession):
    """Get organization members (query from memberships table)"""
    members = await repository.list_memberships(db, context_organization.id)

    response = []
    for member in members:
        user = await get_user_by_id(member.user_id)
        if user:
            response.append(
                dict(
                    id=member.user_id,
                    email=user.email,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    role=member.role,
                )
            )

    return response


async def remove_member(user_id: str, context_organization, context_user, db: AsyncSession):
    """Remove user from organization (delete membership to free seat)"""
    if user_id == context_user.id:
        raise HTTPException(status_code=400, detail="Cannot remove yourself from organization")

    success = await remove_user_from_organization(db, context_organization.id, user_id)

    if not success:
        raise HTTPException(status_code=404, detail="User not found in organization")

    return OperationSuccessResponse(success=True)


async def create_organization_with_owner(
    organization_in: OrganizationCreateRequest, db: AsyncSession
):
    """Create a new organization (admin only)."""
    if not await get_user_by_id(organization_in.owner_id):
        raise HTTPException(status_code=404, detail="Owner user not found")

    existing_membership = await get_membership_for_user(db, organization_in.owner_id)
    if existing_membership:
        raise HTTPException(
            status_code=400,
            detail="Owner user already belongs to an organization",
        )

    organization = None
    try:
        organization = await create_organization(
            db=db,
            name=organization_in.name,
            max_users=organization_in.max_users,
        )
        await add_user_to_organization(
            db=db,
            org_id=organization.id,
            user_id=organization_in.owner_id,
            role="owner",
        )
        response = OrganizationResponse.model_validate(organization)
        response.active_user_count = 1
        return response
    except Exception as e:
        if organization is not None:
            try:
                await delete_organization(db, organization.id)
            except Exception:
                logger.exception(
                    "Failed to clean up organization %s after owner membership error",
                    organization.id,
                )
        logger.exception(f"Failed to create organization: {e}")
        raise HTTPException(status_code=400, detail="Failed to create organization")


async def list_organization_details(db: AsyncSession):
    """List all organizations with user counts (admin only)."""
    organizations = await list_organizations(db)

    result = []
    for org in organizations:
        active_count = await get_active_user_count(db, org.id)
        response = OrganizationResponse.model_validate(org)
        response.active_user_count = active_count
        result.append(response)

    return result


async def get_organization_details(org_id: str, db: AsyncSession):
    """Get organization details with user list (organization admin)."""

    organization = await get_organization_by_id(db, org_id)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    active_count = await get_active_user_count(db, org_id)

    user_list = []

    # Get all memberships for this organization
    memberships = await repository.list_memberships(db, org_id)

    for membership in memberships:
        user = await get_user_by_id(membership.user_id)

        if user is not None:
            usage = await token_usage_service.get_today_usage(db, user.id)
            tokens_used = usage.total_tokens if usage else 0
            daily_limit = user.daily_token_limit
            usage_percentage = (tokens_used / daily_limit * 100) if daily_limit > 0 else 0

            user_list.append(
                OrganizationUserDetailResponse(
                    id=user.id,
                    email=user.email,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    created_at=user.created_at,
                    last_login=user.last_login_at,
                    daily_token_limit=daily_limit,
                    tokens_used_today=tokens_used,
                    usage_percentage=usage_percentage,
                )
            )

    org_response = OrganizationResponse.model_validate(organization)
    org_response.active_user_count = active_count

    return OrganizationDetailResponse(
        organization=org_response,
        users=user_list,
    )


async def update_organization_for_owner(
    org_id: str, organization_update: OrganizationUpdateRequest, db: AsyncSession
):
    """Update organization settings (organization admin)."""

    organization = await update_organization(
        db=db,
        org_id=org_id,
        max_users=organization_update.max_users,
    )

    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    active_count = await get_active_user_count(db, org_id)

    response = OrganizationResponse.model_validate(organization)
    response.active_user_count = active_count
    return response


async def delete_organization_checked(org_id: str, db: AsyncSession):
    """Delete organization (admin only)."""
    success = await delete_organization(db, org_id)
    if not success:
        raise HTTPException(status_code=404, detail="Organization not found")
    return None


async def update_member_token_limit(
    user_id: str, user_update: UserUpdateRequest, db: AsyncSession, context_organization
):
    """Update user daily token limit (organization admin)."""

    if user_update.daily_token_limit is None:
        raise HTTPException(status_code=400, detail="daily_token_limit is required")

    membership = await get_membership_for_user(db, user_id)
    if not membership or membership.organization_id != context_organization.id:
        raise HTTPException(status_code=404, detail="User not found in organization")

    await update_user_token_limit(user_id, user_update.daily_token_limit)

    user = await get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return UserTokenLimitUpdateResponse(
        id=user.id,
        email=user.email,
        daily_token_limit=user.daily_token_limit,
        updated_at=user.updated_at,
    )


async def get_organization_by_customer(db: AsyncSession, customer_id: str):
    return await repository.get_organization_by_customer(db, customer_id)


async def get_owner_membership_for_user(db: AsyncSession, org_id: str, user_id: str):
    return await repository.get_owner_membership_for_user(db, org_id, user_id)
