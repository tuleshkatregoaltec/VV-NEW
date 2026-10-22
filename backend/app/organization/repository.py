"""Organization, membership and invitation persistence. Callers own commits."""

from datetime import datetime

from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.organization.models import Organization, OrganizationInvitation, OrganizationMembership


async def get_organization_by_id(db: AsyncSession, org_id: str):
    return await db.get(Organization, org_id)


async def find_memberships_for_user(db: AsyncSession, user_id: str):
    result = await db.exec(
        select(OrganizationMembership).where(OrganizationMembership.user_id == user_id)
    )
    return result.all()


async def get_owner_membership_for_org(db: AsyncSession, org_id: str):
    result = await db.exec(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == org_id, OrganizationMembership.role == "owner"
        )
    )
    return result.first()


async def get_member_count(db: AsyncSession, org_id: str):
    result = await db.exec(
        select(func.count(OrganizationMembership.id)).where(
            OrganizationMembership.organization_id == org_id
        )
    )
    return result.first()


async def get_pending_invitation_count(db: AsyncSession, org_id: str, now: datetime):
    result = await db.exec(
        select(func.count(OrganizationInvitation.id)).where(
            OrganizationInvitation.organization_id == org_id,
            OrganizationInvitation.status == "pending",
            OrganizationInvitation.expires_at >= now,
        )
    )
    return result.first()


async def list_organizations(db: AsyncSession):
    result = await db.exec(select(Organization))
    return result.all()


async def list_memberships(db: AsyncSession, org_id: str):
    result = await db.exec(
        select(OrganizationMembership).where(OrganizationMembership.organization_id == org_id)
    )
    return result.all()


async def get_membership_in_org(db: AsyncSession, org_id: str, user_id: str):
    result = await db.exec(
        select(OrganizationMembership)
        .where(OrganizationMembership.organization_id == org_id)
        .where(OrganizationMembership.user_id == user_id)
    )
    return result.first()


async def list_pending_invitations(db: AsyncSession, org_id: str):
    result = await db.exec(
        select(OrganizationInvitation).where(
            OrganizationInvitation.organization_id == org_id,
            OrganizationInvitation.status == "pending",
        )
    )
    return result.all()


async def list_pending_invitations_for_email(db: AsyncSession, org_id: str, email: str):
    result = await db.exec(
        select(OrganizationInvitation).where(
            OrganizationInvitation.organization_id == org_id,
            OrganizationInvitation.email == email,
            OrganizationInvitation.status == "pending",
        )
    )
    return result.all()


async def get_invitation_by_token(db: AsyncSession, token: str):
    result = await db.exec(
        select(OrganizationInvitation).where(OrganizationInvitation.token == token)
    )
    return result.first()


async def get_invitation_by_id(db: AsyncSession, invitation_id: str):
    return await db.get(OrganizationInvitation, invitation_id)


def save_organization(db: AsyncSession, organization: Organization):
    db.add(organization)


def save_membership(db: AsyncSession, membership: OrganizationMembership):
    db.add(membership)


def save_invitation(db: AsyncSession, invitation: OrganizationInvitation):
    db.add(invitation)


async def delete_membership(db: AsyncSession, membership: OrganizationMembership):
    await db.delete(membership)


async def delete_organization(db: AsyncSession, organization: Organization):
    await db.delete(organization)


async def get_organization_by_customer(db: AsyncSession, customer_id: str):
    stmt = select(Organization).where(Organization.stripe_customer_id == customer_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_owner_membership_for_user(db: AsyncSession, org_id: str, user_id: str):
    stmt = select(OrganizationMembership).where(
        OrganizationMembership.organization_id == org_id,
        OrganizationMembership.user_id == user_id,
        OrganizationMembership.role == "owner",
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()
