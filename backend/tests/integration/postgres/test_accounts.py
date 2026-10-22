from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.organization import invitations, service
from app.organization.models import AcceptInviteRequest


async def test_invite_reservation_acceptance_and_cancellation_survive_commits(pg_session):
    org = await service.create_organization(pg_session, "Migration integration", 3)
    owner = str(uuid4())
    await service.add_user_to_organization(pg_session, org.id, owner, "owner")
    member = SimpleNamespace(id=str(uuid4()), email=f"{uuid4().hex}@example.com")
    invite = await invitations.create_invitation(pg_session, org.id, member.email, owner)
    assert await service.get_pending_invitation_count(pg_session, org.id) == 1
    accepted = await invitations.accept_invite(
        AcceptInviteRequest(token=invite.token), member, pg_session
    )
    assert accepted.organization_id == org.id
    assert await service.get_member_count(pg_session, org.id) == 2
    assert await service.get_pending_invitation_count(pg_session, org.id) == 0
    org_id = org.id
    token = invite.token
    pg_session.expire_all()
    record = await invitations.get_invitation_by_token(pg_session, token)
    assert record.status == "accepted"
    assert record.accepted_at is not None
    second = await invitations.create_invitation(
        pg_session, org_id, f"{uuid4().hex}@example.com", owner
    )
    await invitations.cancel_invitation(pg_session, second)
    assert await service.get_pending_invitation_count(pg_session, org_id) == 0


async def test_invitation_email_mismatch_does_not_create_membership(pg_session):
    org = await service.create_organization(pg_session, "Mismatch integration", 2)
    invite = await invitations.create_invitation(
        pg_session, org.id, "intended@example.com", str(uuid4())
    )
    stranger = SimpleNamespace(id=str(uuid4()), email="different@example.com")
    with pytest.raises(HTTPException) as error:
        await invitations.accept_invite(
            AcceptInviteRequest(token=invite.token), stranger, pg_session
        )
    assert error.value.status_code == 403
    assert await service.get_membership_for_user(pg_session, stranger.id) is None
    assert await service.get_pending_invitation_count(pg_session, org.id) == 1
