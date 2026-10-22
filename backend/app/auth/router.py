from fastapi import APIRouter, Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.models import AuthMeResponse, UpdateProfileRequest, UpdateProfileResponse
from app.auth.profile import get_user_profile
from app.auth.service import update_user_profile_metadata
from app.core.dependencies import require_auth
from app.postgres import get_db_session
from app.providers.auth import AuthenticatedUser

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.get("/me", response_model=AuthMeResponse)
async def get_user(
    db: AsyncSession = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_auth),
) -> AuthMeResponse:
    """
    Get current authenticated user with organization and subscription context.

    Returns first_name/last_name from the authenticated user principal.
    Returns subscription_status from organization.subscription_status (single source of truth).
    """
    return await get_user_profile(db, current_user)


@router.post("/update-profile", response_model=UpdateProfileResponse)
async def update_profile(
    request: UpdateProfileRequest,
    current_user: AuthenticatedUser = Depends(require_auth),
):
    """Update user profile (first name and last name)."""
    await update_user_profile_metadata(current_user.id, request.first_name, request.last_name)

    return UpdateProfileResponse(
        success=True, first_name=request.first_name, last_name=request.last_name
    )
