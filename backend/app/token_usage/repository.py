from datetime import date

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.token_usage.models import UserTokenUsage


async def get_usage(db: AsyncSession, user_id: str, day: date):
    stmt = select(UserTokenUsage).where(
        UserTokenUsage.user_id == user_id, UserTokenUsage.date == day
    )
    result = await db.exec(stmt)
    return result.first()


async def get_usage_records(db: AsyncSession, day: date, user_ids: list[str] | None = None):
    stmt = select(UserTokenUsage).where(UserTokenUsage.date == day)
    if user_ids is not None:
        stmt = stmt.where(UserTokenUsage.user_id.in_(user_ids))
    result = await db.execute(stmt)
    return result.scalars().all()


def save(db: AsyncSession, usage: UserTokenUsage):
    db.add(usage)
