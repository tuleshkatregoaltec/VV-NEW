"""Identity operations backed by the GoTrue provider adapter."""

from typing import Optional

from app.providers.auth import AuthenticatedUser
from app.providers.auth.client import create_user_admin as create_user_admin
from app.providers.auth.client import delete_user_admin as delete_user_admin
from app.providers.auth.client import get_auth_user as get_auth_user
from app.providers.auth.client import get_user_by_id as get_user_by_id
from app.providers.auth.client import send_magic_link_email as send_magic_link_email
from app.providers.auth.client import update_user_profile_metadata as update_user_profile_metadata
from app.providers.auth.client import update_user_token_limit as update_user_token_limit


async def verify_user(jwt: str) -> Optional[AuthenticatedUser]:
    auth_user = await get_auth_user(jwt)
    if auth_user is None:
        return None

    return AuthenticatedUser(jwt, auth_user)
