from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.providers.auth import client


async def test_token_limit_update_preserves_other_app_metadata(monkeypatch):
    admin = SimpleNamespace(
        get_user_by_id=AsyncMock(
            return_value=SimpleNamespace(
                user=SimpleNamespace(app_metadata={"is_admin": True, "provider": "email"})
            )
        ),
        update_user_by_id=AsyncMock(),
    )
    monkeypatch.setattr(client, "admin_client", SimpleNamespace(admin=admin))
    await client.update_user_token_limit("user-1", 1234)
    admin.update_user_by_id.assert_awaited_once_with(
        "user-1",
        {"app_metadata": {"is_admin": True, "provider": "email", "daily_token_limit": 1234}},
    )


async def test_magic_link_preserves_redirect_and_creation_flag(monkeypatch):
    auth = SimpleNamespace(sign_in_with_otp=AsyncMock())
    monkeypatch.setattr(client, "auth_client", auth)
    result = await client.send_magic_link_email(
        "invitee@example.com", "https://example.com/invite?token=one", False
    )
    assert result == {"success": True, "error": None}
    auth.sign_in_with_otp.assert_awaited_once_with(
        {
            "email": "invitee@example.com",
            "options": {
                "email_redirect_to": "https://example.com/invite?token=one",
                "should_create_user": False,
            },
        }
    )
