from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.billing.models import Subscription


async def get_by_stripe_id(db: AsyncSession, subscription_id: str):
    stmt = select(Subscription).where(Subscription.stripe_subscription_id == subscription_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_by_organization(db: AsyncSession, organization_id: str):
    stmt = select(Subscription).where(Subscription.organization_id == organization_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_latest_by_organization(db: AsyncSession, organization_id: str):
    result = await db.exec(
        select(Subscription)
        .where(Subscription.organization_id == organization_id)
        .order_by(Subscription.created_at.desc())
    )
    return result.first()


def save(db: AsyncSession, subscription: Subscription):
    db.add(subscription)
