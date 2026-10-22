import logging
from typing import List

from fastapi import APIRouter, Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from app.admin import service
from app.admin.models import (
    CreateUserRequest,
    CreateUserResponse,
    OrganizationListResponse,
    OrganizationMemberResponse,
    OrgDetailResponse,
    UpdateOrgRequest,
    UpdateSubscriptionRequest,
    UpdateUserRequest,
    UserDetailResponse,
)
from app.core.dependencies import require_admin
from app.postgres import get_db_session
from app.token_usage.models import UserTokenStatsResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"], dependencies=[Depends(require_admin)])


# ─── Organizations ────────────────────────────────────────────────────────────


@router.get("/organizations", response_model=List[OrganizationListResponse])
async def list_all_organizations(db: AsyncSession = Depends(get_db_session)):
    """List all organizations with stats."""
    return await service.list_all_organizations(db=db)


@router.get("/organizations/{org_id}", response_model=OrgDetailResponse)
async def get_organization(org_id: str, db: AsyncSession = Depends(get_db_session)):
    """Get organization details with members."""
    return await service.get_organization(org_id=org_id, db=db)


@router.put("/organizations/{org_id}", response_model=OrganizationListResponse)
async def update_org(
    org_id: str, request: UpdateOrgRequest, db: AsyncSession = Depends(get_db_session)
):
    """Update organization settings."""
    return await service.update_org(org_id=org_id, request=request, db=db)


@router.patch("/organizations/{org_id}/subscription", response_model=OrganizationListResponse)
async def set_subscription_status(
    org_id: str,
    request: UpdateSubscriptionRequest,
    db: AsyncSession = Depends(get_db_session),
):
    """Update organization subscription status."""
    return await service.set_subscription_status(org_id=org_id, request=request, db=db)


@router.delete("/organizations/{org_id}", status_code=204)
async def delete_org(org_id: str, db: AsyncSession = Depends(get_db_session)):
    """Delete organization and all memberships."""
    return await service.delete_org(org_id=org_id, db=db)


# ─── Users ────────────────────────────────────────────────────────────────────


@router.get("/token-usage", response_model=List[UserTokenStatsResponse])
async def list_token_usage(db: AsyncSession = Depends(get_db_session)):
    """List today's token usage for all users."""
    return await service.list_token_usage(db=db)


@router.post("/users", response_model=CreateUserResponse, status_code=201)
async def create_user(request: CreateUserRequest, db: AsyncSession = Depends(get_db_session)):
    """Create a Supabase user and assign to an org (new or existing)."""
    return await service.create_user(request=request, db=db)


@router.get("/users/{user_id}", response_model=UserDetailResponse)
async def get_user_details(user_id: str, db: AsyncSession = Depends(get_db_session)):
    """Get user details."""
    return await service.get_user_details(user_id=user_id, db=db)


@router.patch("/users/{user_id}", response_model=UserDetailResponse)
async def update_user(
    user_id: str, request: UpdateUserRequest, db: AsyncSession = Depends(get_db_session)
):
    """Update user settings."""
    return await service.update_user(user_id=user_id, request=request, db=db)


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(user_id: str, db: AsyncSession = Depends(get_db_session)):
    """Delete user from Supabase and remove org membership."""
    return await service.delete_user(user_id=user_id, db=db)


@router.get(
    "/organizations/{organization_id}/members",
    response_model=List[OrganizationMemberResponse],
)
async def list_organization_members(
    organization_id: str,
    db: AsyncSession = Depends(get_db_session),
):
    """List all members in an organization."""
    return await service.list_organization_members(organization_id=organization_id, db=db)
