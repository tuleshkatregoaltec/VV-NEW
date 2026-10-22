from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.feasibility.models import (
    FeasibilitySavedStudy,
)


async def find_study(
    db: AsyncSession, organization_id: str, study_id: int, user_id: str
) -> FeasibilitySavedStudy | None:
    stmt = select(FeasibilitySavedStudy).where(
        FeasibilitySavedStudy.id == study_id,
        FeasibilitySavedStudy.user_id == user_id,
        FeasibilitySavedStudy.organization_id == organization_id,
    )
    result = await db.exec(stmt)
    study = result.first()
    return study


async def list_studies(
    db: AsyncSession, organization_id: str, user_id: str
) -> list[FeasibilitySavedStudy]:
    stmt = (
        select(FeasibilitySavedStudy)
        .where(
            FeasibilitySavedStudy.user_id == user_id,
            FeasibilitySavedStudy.organization_id == organization_id,
        )
        .order_by(FeasibilitySavedStudy.updated_at.desc())
    )
    result = await db.exec(stmt)
    return result.all()


def add_study(db: AsyncSession, study: FeasibilitySavedStudy) -> None:
    db.add(study)


async def refresh_study(db: AsyncSession, study: FeasibilitySavedStudy) -> None:
    await db.refresh(study)


async def delete_study(db: AsyncSession, study: FeasibilitySavedStudy) -> None:
    await db.delete(study)
